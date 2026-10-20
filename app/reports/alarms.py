"""Alarm history report: fetch the alarm log, drop duplicates, build rows and a summary."""
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import mp_client
from ..jobs import Job

WINDOW = timedelta(hours=6)  # the period is fetched in slices of this size ...
CONCURRENCY = 4              # ... this many at a time
MAX_EVENTS = 200_000         # safety net against runaway reports

STREAM_KINDS = {"softwareStream", "ethernetTransport", "deviceTransport"}


@dataclass
class AlarmRow:
    key: str
    priority: int
    group: str
    alarm: str
    condition: str
    stream: str
    object: str
    object_id: str
    node: str
    detected: datetime
    finished: datetime | None
    duration_sec: int
    active: bool
    confirmed: bool


@dataclass
class AlarmReport:
    params: dict[str, Any]
    rows: list[AlarmRow]
    summary: dict[str, Any] = field(default_factory=dict)
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    truncated: bool = False


def _parse(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(tz)


def _windows(begin: datetime, end: datetime) -> list[tuple[str, str]]:
    out, cursor = [], begin
    while cursor < end:
        nxt = min(cursor + WINDOW, end)
        out.append((cursor.strftime("%Y-%m-%dT%H:%M:%SZ"), nxt.strftime("%Y-%m-%dT%H:%M:%SZ")))
        cursor = nxt
    return out


def _top_most(selected: set[str], parents: dict[str, str | None]) -> list[str]:
    """Selected objects whose ancestors are not selected. Asking MultiProbe for a stream already
    returns everything inside it, so the children need not be requested again."""
    def has_selected_ancestor(oid: str) -> bool:
        seen, parent = set(), parents.get(oid)
        while parent and parent not in seen:
            if parent in selected:
                return True
            seen.add(parent)
            parent = parents.get(parent)
        return False

    return sorted(oid for oid in selected if not has_selected_ancestor(oid))


def _stream_of(oid: str, objects: dict[str, dict]) -> str:
    """Name of the stream (transport) the object belongs to; empty for the stream itself."""
    seen, current = set(), objects.get(oid)
    while current and current.get("parentID") and current["parentID"] not in seen:
        seen.add(current["parentID"])
        parent = objects.get(current["parentID"])
        if parent is None:
            break
        current = parent
    if current is None or current.get("monitoringObjectID") == oid:
        return ""
    return current.get("name") or ""


async def build(job: Job, token: str, time_zone: str) -> AlarmReport:
    p = job.params
    tz = ZoneInfo(time_zone)
    begin = datetime.fromisoformat(p["period_utc"][0])
    end = datetime.fromisoformat(p["period_utc"][1])
    selected = set(p["object_ids"])
    alarm_filter = set(p["alarm_ids"]) if p.get("alarm_ids") else None

    all_objects = {o["monitoringObjectID"]: o for o in await mp_client.monitoring_objects(token, p["node_id"])}
    catalog = await mp_client.alarm_catalog(token)
    request_ids = _top_most(selected, {k: v.get("parentID") for k, v in all_objects.items()})

    windows = _windows(begin, end)
    job.total_steps = len(windows)
    raw: dict[tuple, dict] = {}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def fetch(window: tuple[str, str]) -> None:
        async with sem:
            items = await mp_client.alarm_log_window(token, request_ids, *window)
        for r in items:
            if r.get("monitoringObjectID") not in selected:
                continue
            if alarm_filter is not None and r.get("alarmID") not in alarm_filter:
                continue
            # One event comes back once per view (stream, service, the object itself) and once
            # per window it overlaps; this key identifies the event itself.
            key = (r.get("objectAnalyzerLogID"), r.get("monitoringObjectID"), r.get("alarmID"), r.get("alarmBegin"))
            known = raw.get(key)
            # Windows are fetched at slightly different moments; prefer the copy that has ended.
            if known is None or (not known.get("alarmEnd") and r.get("alarmEnd")):
                raw[key] = r
        job.done_steps += 1
        job.rows_fetched = len(raw)
        if len(raw) > MAX_EVENTS:
            raise RuntimeError(f"More than {MAX_EVENTS} events — narrow the period or the objects.")

    await asyncio.gather(*(fetch(w) for w in windows))

    now = datetime.now(tz)
    rows = []
    for r in raw.values():
        detected = _parse(r.get("alarmBegin"), tz)
        if detected is None:
            continue
        finished = _parse(r.get("alarmEnd"), tz)
        active = bool(r.get("isActive")) or finished is None
        oid = r["monitoringObjectID"]
        obj = all_objects.get(oid, {})
        definition = catalog.get(r.get("alarmID"), {})
        rows.append(
            AlarmRow(
                key=f'{r.get("objectAnalyzerLogID")}-{oid}-{r.get("alarmID")}',
                priority=int(r.get("priority") or definition.get("priority") or 0),
                group=r.get("alarmGroupName") or definition.get("alarmGroupName") or "",
                alarm=r.get("alarmName") or definition.get("alarmName") or "",
                condition=definition.get("conditionOn") or r.get("conditionOn") or "",
                stream=_stream_of(oid, all_objects),
                object=obj.get("name") or r.get("monitoringObjectName") or "",
                object_id=oid,
                node=r.get("nodeName") or "",
                detected=detected,
                finished=None if active else finished,
                duration_sec=max(0, int(((now if active else finished) - detected).total_seconds())),
                active=active,
                confirmed=bool((r.get("confirmation") or {}).get("isConfirmed")),
            )
        )
    rows.sort(key=lambda x: x.detected, reverse=True)

    report = AlarmReport(params=p, rows=rows)
    report.summary = summarize(rows, await mp_client.alarm_groups(token))
    return report


def summarize(rows: list[AlarmRow], groups: list[dict]) -> dict[str, Any]:
    by_group = Counter(r.group for r in rows)
    order = {g["name"]: -g.get("beginPriority", 0) for g in groups}  # most severe first
    return {
        "total": len(rows),
        "active": sum(r.active for r in rows),
        "total_duration_sec": sum(r.duration_sec for r in rows),
        "by_group": sorted(by_group.items(), key=lambda kv: (order.get(kv[0], 0), kv[0])),
        "top_alarms": Counter(r.alarm for r in rows).most_common(10),
        "top_objects": Counter(r.object for r in rows).most_common(10),
    }
