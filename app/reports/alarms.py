"""Alarm history report: fetch the alarm log, drop duplicates, build rows and a summary."""
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import mp_client
from ..config import settings
from ..jobs import Job
from . import availability as avail_calc
from .availability import AvailabilityRow

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
    server: str = ""  # "Server · Node" when the report covers several, else empty

    @property
    def label(self) -> str:
        return f"{self.server} · {self.object}" if self.server else self.object


@dataclass
class AlarmReport:
    params: dict[str, Any]
    rows: list[AlarmRow]
    summary: dict[str, Any] = field(default_factory=dict)
    availability: list[AvailabilityRow] = field(default_factory=list)
    period: tuple[datetime, datetime] | None = None  # in the user's time zone
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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


def target_label(t: dict, multi: bool) -> str:
    """How a server/node appears in a report that covers several of them."""
    return f'{t.get("server", "")} · {t.get("node", "")}' if multi else ""


def is_multi(targets: list[dict]) -> bool:
    return len({(t["server_id"], t["node_id"]) for t in targets}) > 1


async def build(job: Job, conns: dict, time_zone: str) -> AlarmReport:
    """conns: server id -> mp_client.Conn for every server in job.params["targets"]."""
    p = job.params
    tz = ZoneInfo(time_zone)
    begin = datetime.fromisoformat(p["period_utc"][0])
    end = datetime.fromisoformat(p["period_utc"][1])
    targets = p["targets"]
    multi = is_multi(targets)
    windows = _windows(begin, end)
    job.total_steps = max(1, len(windows) * len(targets))
    now = datetime.now(tz)
    period = (begin.astimezone(tz), end.astimezone(tz))

    rows: list[AlarmRow] = []
    availability: list[AvailabilityRow] = []
    for t in targets:
        conn = conns[t["server_id"]]
        t_rows, objects, selected = await _fetch_target(job, conn, t, p, windows, tz, now)
        label = target_label(t, multi)
        for r in t_rows:
            r.server = label
        rows += t_rows
        avail = avail_calc.compute(
            t_rows, objects, selected, period[0], period[1], now,
            settings.outage_min_priority, settings.degraded_min_priority,
        )
        if multi:  # a context row per server/node, everything else one level deeper
            availability.append(AvailabilityRow(object_id=f'target:{t["server_id"]}:{t["node_id"]}',
                                                name=label, kind="target", kind_name="", depth=0))
            for a in avail:
                a.depth += 1
                a.server = label
        availability += avail
    rows.sort(key=lambda x: x.detected, reverse=True)

    report = AlarmReport(params=p, rows=rows, period=period)
    report.availability = availability
    report.summary = summarize(rows, availability, await mp_client.alarm_groups(conns[targets[0]["server_id"]]))
    return report


async def _fetch_target(job: Job, conn, t: dict, p: dict, windows, tz: ZoneInfo, now: datetime):
    """Alarm rows of one server/node: (rows, all objects of the node, selected ids)."""
    selected = set(t["object_ids"])
    # Alarms the user un-ticked. Storing exclusions (not inclusions) keeps templates working
    # when new alarm types are added to MultiProbe later.
    excluded = set(p.get("alarm_exclude") or [])
    all_objects = {o["monitoringObjectID"]: o for o in await mp_client.monitoring_objects(conn, t["node_id"])}
    catalog = await mp_client.alarm_catalog(conn)
    request_ids = _top_most(selected, {k: v.get("parentID") for k, v in all_objects.items()})
    raw: dict[tuple, dict] = {}
    sem = asyncio.Semaphore(CONCURRENCY)
    fetched_before = job.rows_fetched

    async def fetch(window: tuple[str, str]) -> None:
        async with sem:
            items = await mp_client.alarm_log_window(conn, request_ids, *window)
        for r in items:
            if r.get("monitoringObjectID") not in selected:
                continue
            if r.get("alarmID") in excluded:
                continue
            # One event comes back once per view (stream, service, the object itself) and once
            # per window it overlaps; this key identifies the event itself.
            key = (r.get("objectAnalyzerLogID"), r.get("monitoringObjectID"), r.get("alarmID"), r.get("alarmBegin"))
            known = raw.get(key)
            # Windows are fetched at slightly different moments; prefer the copy that has ended.
            if known is None or (not known.get("alarmEnd") and r.get("alarmEnd")):
                raw[key] = r
        job.done_steps += 1
        job.rows_fetched = fetched_before + len(raw)
        if job.rows_fetched > MAX_EVENTS:
            raise RuntimeError(f"More than {MAX_EVENTS} events — narrow the period or the objects.")

    if request_ids:
        await asyncio.gather(*(fetch(w) for w in windows))
    else:
        job.done_steps += len(windows)

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
    return rows, all_objects, selected


def summarize(
    rows: list[AlarmRow], avail: list[AvailabilityRow], groups: list[dict]
) -> dict[str, Any]:
    by_group = Counter(r.group for r in rows)
    order = {g["name"]: -g.get("beginPriority", 0) for g in groups}  # most severe first
    avail = [a for a in avail if a.kind != "target"]  # server/node context rows are not objects
    worst = max(avail, key=lambda a: a.outage_sec, default=None)
    return {
        "total": len(rows),
        "active": sum(r.active for r in rows),
        "objects": len(avail),
        "objects_with_outage": sum(a.outage_sec > 0 for a in avail),
        "availability": round(sum(a.availability for a in avail) / len(avail), 2) if avail else 100.0,
        "worst": (worst.label, worst.outage_sec) if worst and worst.outage_sec else None,
        "by_group": sorted(by_group.items(), key=lambda kv: (order.get(kv[0], 0), kv[0])),
        "top_alarms": Counter(r.alarm for r in rows).most_common(10),
        "top_objects": Counter(f"{r.server} · {r.object}" if r.server else r.object for r in rows).most_common(10),
    }
