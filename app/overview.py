"""Overview page data: the state of every channel right now and the most problematic channels.

A channel is a service (Mpeg Service / Signal Service). Its state is the most severe alarm active
on it or on its PIDs right now. Stream- and transport-level alarms are listed separately, as in
the alarm report.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import mp_client
from .config import settings
from .jobs import Job
from .reports import alarms as alarm_report
from .reports.availability import SERVICE_KINDS

TOP_CACHE_TTL = 180  # seconds; the ranking is recalculated at most this often per user and period
_top_cache: dict[tuple, tuple[float, Any]] = {}
_top_locks: dict[tuple, asyncio.Lock] = {}

# Server hardware and peripherals (GPU, NIC, audio devices, script-fed external devices) are not
# streams, so they stay out of the wizard and the overview.
HARDWARE_KINDS = {"device", "deviceConnector", "externalDevice"}


def reportable(obj: dict) -> bool:
    """Streams and the objects inside them, not hidden or disabled in MultiProbe."""
    return (obj.get("objectKind") not in HARDWARE_KINDS and not obj.get("isDisabled")
            and not str(obj.get("objectType") or "").endswith("Hidden"))


def state_of(priority: int | None) -> str:
    if priority is None or priority < settings.degraded_min_priority:
        return "ok"
    if priority >= 400:
        return "error"
    if priority >= settings.outage_min_priority:
        return "warning"
    return "caution"


def _owner_service(oid: str, objects: dict[str, dict]) -> str | None:
    cur, seen = objects.get(oid), set()
    while cur and cur["monitoringObjectID"] not in seen:
        if cur.get("objectKind") in SERVICE_KINDS:
            return cur["monitoringObjectID"]
        seen.add(cur["monitoringObjectID"])
        cur = objects.get(cur.get("parentID") or "")
    return None


def _stream_of(oid: str, objects: dict[str, dict]) -> str:
    cur, seen = objects.get(oid), set()
    while cur and cur.get("parentID") in objects and cur["parentID"] not in seen:
        seen.add(cur["parentID"])
        cur = objects[cur["parentID"]]
    return "" if cur is None or cur["monitoringObjectID"] == oid else cur.get("name") or ""


async def _all_objects(token: str) -> tuple[list[dict], dict[str, dict]]:
    nodes = await mp_client.nodes(token)
    lists = await asyncio.gather(*(mp_client.monitoring_objects(token, n["nodeID"]) for n in nodes))
    objects = {o["monitoringObjectID"]: o for items in lists for o in items}
    return nodes, objects


async def current_state(token: str, tz) -> dict[str, Any]:
    nodes, objects = await _all_objects(token)
    now = datetime.now(timezone.utc)
    page = await mp_client.call(token, "POST", "/api/Alarm/MonitoringObjectLog/Find", {"dataFilter": {
        "beginDate": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isActive": True,
        "pagingProperty": {"topCount": 20000, "fromIndex": 0},
    }}) or {}
    # The log repeats an event once per view (stream, service, object): keep one per event.
    active: dict[tuple, dict] = {}
    for r in page.get("monitoringObjectLog") or []:
        if r.get("isActive"):
            active.setdefault((r.get("objectAnalyzerLogID"), r.get("monitoringObjectID"), r.get("alarmID")), r)

    channels: dict[str, dict] = {}
    for oid, o in objects.items():
        if o.get("objectKind") in SERVICE_KINDS and reportable(o):
            channels[oid] = {"id": oid, "name": o.get("name") or "", "stream": _stream_of(oid, objects),
                             "node_id": o.get("nodeID"), "node": o.get("nodeName") or "",
                             "priority": None, "alarms": [], "since": None}
    streams: dict[str, dict] = {}
    for r in active.values():
        oid = r.get("monitoringObjectID")
        if oid not in objects or not reportable(objects[oid]):
            continue
        prio = int(r.get("priority") or 0)
        since = datetime.fromisoformat(r["alarmBegin"]) if r.get("alarmBegin") else None
        owner = _owner_service(oid, objects)
        target = channels.get(owner) if owner else None
        if target is None:  # stream / transport level
            o = objects[oid]
            target = streams.setdefault(oid, {"id": oid, "name": o.get("name") or "", "kind": o.get("objectKindName") or "",
                                              "stream": _stream_of(oid, objects), "node_id": o.get("nodeID"),
                                              "priority": None, "alarms": [], "since": None})
        target["priority"] = max(prio, target["priority"] or 0)
        name = r.get("alarmName") or ""
        if name not in target["alarms"]:
            target["alarms"].append(name)
        if since and (target["since"] is None or since < target["since"]):
            target["since"] = since

    def out(item: dict) -> dict:
        state = state_of(item["priority"])
        since = item["since"]
        return {**item, "state": state,
                "since": since.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if since else None,
                "since_sec": int((now - since).total_seconds()) if since else None}

    chan = [out(c) for c in channels.values()]
    order = {"error": 0, "warning": 1, "caution": 2, "ok": 3}
    chan.sort(key=lambda c: (order[c["state"]], -(c["since_sec"] or 0), c["name"].lower()))
    counts = {s: sum(1 for c in chan if c["state"] == s) for s in ("ok", "caution", "warning", "error")}
    stream_list = sorted((out(s) for s in streams.values() if state_of(s["priority"]) != "ok"),
                         key=lambda s: (order[s["state"]], s["name"].lower()))
    return {"updated": now.astimezone(tz).strftime("%H:%M:%S"), "counts": counts, "channels": chan,
            "streams": stream_list, "nodes": [{"id": n["nodeID"], "name": n["name"]} for n in nodes]}


async def top_channels(token: str, user_id: str, hours: int, tz_name: str, limit: int = 15) -> dict[str, Any]:
    """Channels ranked by outage time over the last `hours`, from the same calculation as the report."""
    key = (settings.mp_api_url, user_id, hours)
    hit = _top_cache.get(key)
    if hit and time.monotonic() - hit[0] < TOP_CACHE_TTL:
        return hit[1]
    lock = _top_locks.setdefault(key, asyncio.Lock())
    async with lock:  # several browser tabs asking at once compute it once
        hit = _top_cache.get(key)
        if hit and time.monotonic() - hit[0] < TOP_CACHE_TTL:
            return hit[1]
        nodes, objects = await _all_objects(token)
        end = datetime.now(timezone.utc).replace(microsecond=0)
        begin = end - timedelta(hours=hours)
        rows = []
        for n in nodes:
            ids = [oid for oid, o in objects.items() if o.get("nodeID") == n["nodeID"] and reportable(o)]
            if not ids:
                continue
            job = Job(id="overview", owner=user_id, params={
                "node_id": n["nodeID"], "object_ids": ids, "alarm_exclude": [],
                "period_utc": [begin.isoformat(), end.isoformat()],
            })
            report = await alarm_report.build(job, token, tz_name)
            for a in report.availability:
                if a.kind in SERVICE_KINDS and (a.outage_sec or a.degraded_sec):
                    rows.append({"id": a.object_id, "name": a.name, "stream": _stream_of(a.object_id, objects),
                                 "node_id": n["nodeID"], "outage_sec": a.outage_sec, "degraded_sec": a.degraded_sec,
                                 "availability": a.availability, "incidents": a.incidents,
                                 "longest_sec": a.longest_sec})
        rows.sort(key=lambda r: (-r["outage_sec"], -r["degraded_sec"], r["name"].lower()))
        result = {"hours": hours, "period_sec": hours * 3600, "rows": rows[:limit], "total_with_problems": len(rows),
                  "calculated": datetime.now(timezone.utc).isoformat()}
        _top_cache[key] = (time.monotonic(), result)
        return result


def subtree(object_id: str, objects: dict[str, dict]) -> list[str]:
    """The object and every reportable object inside it — what ticking it in the wizard selects."""
    children: dict[str, list[str]] = {}
    for oid, o in objects.items():
        if o.get("parentID"):
            children.setdefault(o["parentID"], []).append(oid)
    out, stack = [], [object_id]
    while stack:
        cur = stack.pop()
        if cur in objects and reportable(objects[cur]):
            out.append(cur)
        stack.extend(children.get(cur, []))
    return out
