"""MultiProbe services: their state, start / stop / restart and the start type.

Two ways to reach them:
- MultiProbe Guard (through the MultiProbe API): the services of each probe node — Probe Manager, App Manager,
  OTT Probe, Script Probe, Media Signal (Guard itself only shows). Works for remote servers too, and needs no
  rights on this computer: Guard does the work on its node.
- Windows on this computer, for the server the Toolkit runs on: every Stream Labs service, including the ones
  Guard does not manage (Backend, Frontend, Database Agent, Emailer...) and the start type. Changing anything
  this way needs the Toolkit to run as an administrator (or as a service).

The services MultiProbe and the Toolkit talk through can be restarted but not stopped: a stopped API cannot be
asked to start again.
"""
import asyncio
import ctypes
import ipaddress
import json
import socket
import subprocess
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from . import mp_client

WIN_PREFIX = "Stream Labs"
PROTECTED = ("Backend", "Frontend", "App Manager", "Database Agent", "Guard")  # restart only, never stop
START_TYPES = ("automatic", "delayed", "manual", "disabled")
_SC_START = {"automatic": "auto", "delayed": "delayed-auto", "manual": "demand", "disabled": "disabled"}
_WIN_STATE = {"Running": "running", "Stopped": "stopped", "Start Pending": "startPending",
              "Stop Pending": "stopPending", "Paused": "paused", "Pause Pending": "pausePending",
              "Continue Pending": "continuePending"}


class ServiceError(Exception):
    pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def protected(name: str) -> bool:
    return any(f"MP {p} Service" in name for p in PROTECTED)


def _local_addresses() -> set[str]:
    addrs = {"127.0.0.1", "::1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addrs.add(info[4][0])
    except OSError:
        pass
    return addrs


def is_local(url: str) -> bool:
    """The MultiProbe server at `url` runs on this computer."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", socket.gethostname().lower()):
        return True
    try:
        ipaddress.ip_address(host)
        return host in _local_addresses()
    except ValueError:
        try:
            return socket.gethostbyname(host) in _local_addresses()
        except OSError:
            return False


# --- Windows (this computer) ---------------------------------------------------------------------------

_PS_LIST = (
    f"$s = Get-CimInstance Win32_Service -Filter \"Name LIKE '{WIN_PREFIX}%'\";"
    "$ids = @($s | Where-Object ProcessId | ForEach-Object { \"ProcessId=$($_.ProcessId)\" });"
    "$p = @{}; if ($ids) { Get-CimInstance Win32_Process -Filter ($ids -join ' OR ') |"
    " ForEach-Object { $p[[int]$_.ProcessId] = $_.CreationDate } };"
    "$s | ForEach-Object {"
    " [pscustomobject]@{ name = $_.Name; state = $_.State; mode = $_.StartMode; delayed = [bool]$_.DelayedAutoStart;"
    " since = if ($_.ProcessId) { $p[[int]$_.ProcessId].ToString('o') } else { $null } } }"
    " | ConvertTo-Json -Compress"
)


async def _run(*args: str, timeout: float = 30) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ServiceError(f"{args[0]}: no answer in {timeout:.0f} s")
    return proc.returncode, out.decode("utf-8", "replace") if out else ""


async def windows_services() -> list[dict]:
    code, out = await _run("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_LIST)
    if code != 0:
        raise ServiceError(out.strip()[:300])
    items = json.loads(out) if out.strip() else []
    items = items if isinstance(items, list) else [items]
    return [{
        "name": s["name"],
        "status": _WIN_STATE.get(s["state"], s["state"]),
        "start_type": "disabled" if s["mode"] == "Disabled" else "manual" if s["mode"] == "Manual"
        else "delayed" if s["delayed"] else "automatic",
        "since": s.get("since"),
    } for s in items]


def _sc_error(out: str) -> str:
    """sc.exe prints "[SC] StartService FAILED 5:\\n\\nAccess is denied." — keep the reason."""
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    return " ".join(lines[-2:])[:300] if lines else "sc.exe failed"


async def _sc(*args: str) -> None:
    code, out = await _run("sc.exe", *args)
    if code not in (0, 1056, 1062):  # 1056: already running, 1062: not started (stopping a stopped one)
        raise ServiceError(_sc_error(out))


async def _wait_state(name: str, state: str, seconds: int = 60) -> None:
    for _ in range(seconds):
        code, out = await _run("sc.exe", "query", name)
        if state in out:
            return
        await asyncio.sleep(1)
    raise ServiceError(f"{name}: did not reach {state} in {seconds} s")


async def windows_action(name: str, action: str) -> None:
    if action in ("stop", "restart"):
        await _sc("stop", name)
        await _wait_state(name, "STOPPED")
    if action in ("start", "restart"):
        await _sc("start", name)


async def set_start_type(name: str, start_type: str) -> None:
    await _sc("config", name, "start=", _SC_START[start_type])


# --- MultiProbe Guard -----------------------------------------------------------------------------------

def _guard_failed(resp: Any) -> str | None:
    if not isinstance(resp, dict):
        return None
    code = resp.get("rpcErrorStatusCode")
    if code and code != "ok":
        msg = (resp.get("errorMessages") or {}).get("message")
        return f"{code}: {msg}" if msg else code
    return None


async def guard_software(conn, node_id: int) -> list[dict]:
    """The node's software that is a Windows service (the rest are programs App Manager starts)."""
    items = await mp_client.call(conn, "POST", "/api/ProbeManager/CurrentSoftware/Find", {"nodeID": node_id}) or []
    return [s for s in items if s.get("serviceName")]


async def guard_status(conn, node_id: int, software: list[dict]) -> dict[str, dict]:
    if not software:
        return {}
    resp = await mp_client.call(conn, "POST", "/api/Probe/SlmpGuard/GetServicesStatus",
                                {"nodeID": node_id, "softwareIDList": [s["softwareID"] for s in software]}) or {}
    if err := _guard_failed(resp):
        raise ServiceError(err)
    return {r["softwareID"]: r for r in resp.get("slmpGuardServiceStatusReplyItemsList") or []}


async def guard_action(conn, node_id: int, software_ids: list[str], action: str) -> None:
    path = {"start": "StartServices", "stop": "StopServices", "restart": "RestartServices"}[action]
    resp = await mp_client.call(conn, "POST", f"/api/Probe/SlmpGuard/{path}",
                                {"nodeID": node_id, "softwareIDList": software_ids})
    if err := _guard_failed(resp):
        raise ServiceError(err)


# --- Together ---------------------------------------------------------------------------------------------

def _since(value: str | None, tz) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def _node_ip(conn, node_id: int) -> str:
    content = await mp_client.call(conn, "POST", f"/api/Probe/Node/GetContent?id={node_id}") or {}
    return content.get("mainIPAddress") or ""


async def server_services(conn, tz) -> dict[str, Any]:
    """The services of one server: per node what Guard manages; on this computer, every Stream Labs service."""
    local = is_local(conn.url)
    admin = local and is_admin()
    nodes = await mp_client.nodes(conn)
    softwares, ips = await asyncio.gather(
        asyncio.gather(*(guard_software(conn, n["nodeID"]) for n in nodes)),
        asyncio.gather(*(_node_ip(conn, n["nodeID"]) for n in nodes)),
    )
    statuses = await asyncio.gather(*(guard_status(conn, n["nodeID"], sw) for n, sw in zip(nodes, softwares)),
                                    return_exceptions=True)
    win = {s["name"]: s for s in await windows_services()} if local else {}
    # The node on this computer gets the Windows view; with one node on a local server, that is it.
    here = next((i for i, ip in enumerate(ips) if ip in _local_addresses()), 0 if local and len(nodes) == 1 else None)

    out_nodes = []
    for i, (node, software, status) in enumerate(zip(nodes, softwares, statuses)):
        on_this_pc = i == here
        rows: dict[str, dict] = {}
        for s in software:
            st = {} if isinstance(status, Exception) else status.get(s["softwareID"], {})
            name = s["serviceName"]
            rows[name] = {
                "name": name, "software_id": s["softwareID"], "version": s.get("softwareVersion") or "",
                "via": "guard", "can_control": bool(s.get("canRestart")),
                "status": st.get("status") or "unknown", "start_type": st.get("startType") or "",
                "since": _since(st.get("startupDateTime"), tz),
            }
        if on_this_pc:
            for name, w in win.items():
                row = rows.setdefault(name, {"name": name, "software_id": None, "version": "", "via": "windows",
                                             "can_control": admin})
                row.update(status=w["status"], start_type=w["start_type"], since=_since(w["since"], tz))
                if not row["can_control"] and admin:  # Guard cannot restart itself; Windows can
                    row.update(via="windows", can_control=True)
        for row in rows.values():
            row["protected"] = protected(row["name"])
            row["can_set_start"] = on_this_pc and admin
            row["display"] = row["name"].removeprefix("Stream Labs MP ").removesuffix(" Service") or row["name"]
        out_nodes.append({
            "id": node["nodeID"], "name": node.get("name") or "", "this_pc": on_this_pc,
            "error": str(status)[:300] if isinstance(status, Exception) else None,
            "services": sorted(rows.values(), key=lambda r: (r["via"] != "windows", r["display"].lower())),
        })
    return {"server_id": conn.server_id, "server": conn.name, "local": local, "admin": admin, "nodes": out_nodes}


async def act(conn, node_id: int, names: list[str], action: str, tz) -> list[str]:
    """Start / stop / restart these services of a node. Returns the failures (name: reason)."""
    data = await server_services(conn, tz)
    node = next((n for n in data["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise ServiceError(f"node {node_id} not found")
    rows = {r["name"]: r for r in node["services"]}
    failed, guard_ids, win_names = [], [], []
    for name in names:
        row = rows.get(name)
        if row is None:
            failed.append(f"{name}: not found")
        elif action == "stop" and row["protected"]:
            failed.append(f"{row['display']}: can only be restarted")
        elif not row["can_control"]:
            failed.append(f"{row['display']}: cannot be controlled from here")
        elif row["via"] == "guard":
            guard_ids.append(row["software_id"])
        else:
            win_names.append(name)

    async def by_guard():
        if guard_ids:
            try:
                await guard_action(conn, node_id, guard_ids, action)
            except (ServiceError, mp_client.MPError) as exc:
                failed.append(f"Guard: {getattr(exc, 'message', '') or exc}")

    async def by_windows():
        for name in win_names:  # one by one: a service may need another one running first
            try:
                await windows_action(name, action)
            except ServiceError as exc:
                failed.append(f"{rows[name]['display']}: {exc}")

    # Server services (Windows) come up before the node ones Guard manages, and go down after them.
    first, second = (by_windows, by_guard) if action == "start" else (by_guard, by_windows)
    await first()
    await second()
    return failed
