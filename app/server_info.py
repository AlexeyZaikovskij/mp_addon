"""Overview's server block: what MultiProbe shows on Administration -> Nodes, per connected server.

Per probe node: hardware (description, IPs), load (CPU, temperature, disks — only where MultiProbe keeps
these charts, 26.09 does, 26.06 does not), object counts, encoders, recording tasks. Per server: the
version and the licences in use.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from . import mp_client

LOAD_WINDOW = timedelta(minutes=10)  # newer measurements than this count as "now"
CPU_ANALYZER, DISK_ANALYZER = 1, 4   # analyzers of /api/Test/ChartData/Find


async def servers(conns: list, tz) -> dict[str, Any]:
    results = await asyncio.gather(*(_server(c, tz) for c in conns), return_exceptions=True)
    out, errors = [], []
    for conn, res in zip(conns, results):
        if isinstance(res, Exception):
            errors.append({"server_id": conn.server_id, "server": conn.name, "message": str(res)[:200]})
        else:
            out.append(res)
    return {"servers": out, "errors": errors}


async def _optional(coro, default):
    """Parts an older MultiProbe may not have: the block shows what there is."""
    try:
        return await coro
    except mp_client.MPError as exc:
        if isinstance(exc, mp_client.MPAuthError) or exc.code == "unreachable":
            raise
        return default


async def _server(conn, tz) -> dict[str, Any]:
    call = lambda method, path, body=None: mp_client.call(conn, method, path, body)  # noqa: E731
    nodes, version, licences, encoders, devices, tasks = await asyncio.gather(
        mp_client.nodes(conn),
        _optional(call("GET", "/api/Info/GetVersion"), {}),
        _optional(call("POST", "/api/Probe/License/Find", {}), {}),
        _optional(call("POST", "/api/Probe/StreamEncoder/Find", {}), []),
        _optional(call("POST", "/api/Hardware/Device/Find", {}), []),
        _optional(call("POST", "/api/Recording/Task/Find", {}), []),
    )
    now = datetime.now(timezone.utc)
    since = (now - LOAD_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    contents, charts = await asyncio.gather(
        asyncio.gather(*(_optional(call("POST", f"/api/Probe/Node/GetContent?id={n['nodeID']}"), {}) for n in nodes)),
        asyncio.gather(*(_optional(call("POST", "/api/Test/ChartData/Find", {"nodeID": n["nodeID"], "begin": since}), [])
                         for n in nodes)),
    )
    device_names = {d["deviceID"]: d.get("name") or "" for d in devices}
    return {
        "server_id": conn.server_id, "server": conn.name,
        "version": version.get("backEndVersion") or "",
        "nodes": [_node(n, content or {}, chart or [], device_names, encoders, tasks)
                  for n, content, chart in zip(nodes, contents, charts)],
        "licence": _licence(licences, now, tz),
    }


def _node(node: dict, content: dict, chart: list, device_names: dict, encoders: list, tasks: list) -> dict:
    nid = node["nodeID"]
    latest: dict[tuple, dict] = {}
    for r in chart:
        key = (r.get("deviceID"), r.get("analyzerID"))
        if key not in latest or r.get("measureTime", "") > latest[key].get("measureTime", ""):
            latest[key] = r
    cpu = next((r for (_, a), r in latest.items() if a == CPU_ANALYZER), None)
    disks = sorted(({"name": device_names.get(d) or "?", "used": r.get("usedSpace")}
                    for (d, a), r in latest.items() if a == DISK_ANALYZER and r.get("usedSpace") is not None),
                   key=lambda x: x["name"])
    own_encoders = [e for e in encoders if e.get("nodeID") == nid and not e.get("isTemporary")]
    own_tasks = [t for t in tasks if t.get("nodeID") == nid]
    return {
        "id": nid, "name": node.get("name") or content.get("nodeName") or "",
        "description": node.get("description") or "",
        "ip": content.get("mainIPAddress") or "",
        "cpu": cpu.get("cpU_Usage") if cpu else None,
        "temperature": cpu.get("temperature") if cpu else None,
        "disks": disks,
        "objects": [{"name": o.get("objectName") or "", "count": o.get("objectCount") or 0}
                    for o in content.get("objectStatistics") or [] if o.get("objectCount")],
        "encoders": len(own_encoders),
        "encoders_off": sum(1 for e in own_encoders if e.get("isDisabled")),
        "recordings": len(own_tasks),
        "recordings_off": sum(1 for t in own_tasks if t.get("isDisabled")),
    }


def _licence(data: dict, now: datetime, tz) -> dict:
    keys = data.get("keys") or []
    until = None
    for k in keys:
        if k.get("trialPeriod"):  # a key with an end date; a permanent one has none
            end = datetime.fromisoformat(k["trialPeriod"])
            until = end if until is None or end < until else until
    return {
        "alive": all(k.get("isAlive") for k in keys) if keys else None,
        "until": until.astimezone(tz).strftime("%Y-%m-%d") if until else None,
        "days_left": max(0, (until - now).days) if until else None,
        "items": [{"name": l.get("licenseName") or "", "short": l.get("licenseShortName") or "",
                   "used": l.get("usedCount") or 0, "total": l.get("totalCount") or 0}
                  for l in sorted(data.get("licenseTotals") or [], key=lambda l: l.get("licenseOrderNumber") or 0)
                  if l.get("usedCount")],
    }
