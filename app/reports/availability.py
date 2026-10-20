"""Problem time per monitoring object: alarm intervals merged per object, clipped to the period.

Rows follow the object tree (stream → service → PID). A service row also covers the alarms of
its PIDs; streams and transports only count their own alarms, so a transport problem shows up as
its own row instead of being charged to every service.
"""
from dataclasses import dataclass, field
from datetime import datetime

SERVICE_KINDS = {"mpegService", "signalService"}

Interval = tuple[datetime, datetime]


@dataclass
class Span:
    begin: datetime
    end: datetime
    level: str  # "outage" | "degraded"
    alarms: list[str]


@dataclass
class AvailabilityRow:
    object_id: str
    name: str
    kind: str
    kind_name: str
    depth: int
    outage_sec: int = 0
    degraded_sec: int = 0
    availability: float = 100.0
    incidents: int = 0
    longest_sec: int = 0
    spans: list[Span] = field(default_factory=list)


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[list[datetime]] = []
    for b, e in sorted(intervals):
        if out and b <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([b, e])
    return [(b, e) for b, e in out]


def subtract(intervals: list[Interval], cut: list[Interval]) -> list[Interval]:
    """Parts of `intervals` (merged) not covered by `cut` (merged)."""
    out = []
    for b, e in intervals:
        cursor = b
        for cb, ce in cut:
            if ce <= cursor or cb >= e:
                continue
            if cb > cursor:
                out.append((cursor, cb))
            cursor = max(cursor, ce)
        if cursor < e:
            out.append((cursor, e))
    return out


def _seconds(intervals: list[Interval]) -> int:
    return int(sum((e - b).total_seconds() for b, e in intervals))


def compute(
    rows: list,  # AlarmRow
    objects: dict[str, dict],
    selected: set[str],
    begin: datetime,
    end: datetime,
    now: datetime,
    outage_min: int,
    degraded_min: int,
) -> list[AvailabilityRow]:
    end = min(end, now)
    period_sec = max((end - begin).total_seconds(), 1)

    # Alarm intervals per object, clipped to the period: (begin, end, level, alarm name)
    own: dict[str, list[tuple[datetime, datetime, str, str]]] = {}
    for r in rows:
        if r.priority >= outage_min:
            level = "outage"
        elif r.priority >= degraded_min:
            level = "degraded"
        else:
            continue
        b = max(r.detected, begin)
        e = min(r.finished or now, end)
        if e > b:
            own.setdefault(r.object_id, []).append((b, e, level, r.alarm))

    # Object tree restricted to the selected objects, in display order.
    children: dict[str | None, list[str]] = {}
    for oid in selected:
        parent = objects.get(oid, {}).get("parentID")
        while parent and parent not in selected:  # attach to the nearest selected ancestor
            parent = objects.get(parent, {}).get("parentID")
        children.setdefault(parent, []).append(oid)
    for ids in children.values():
        ids.sort(key=lambda i: (objects.get(i, {}).get("name") or "").lower())

    result: list[AvailabilityRow] = []

    def walk(oid: str, depth: int) -> None:
        obj = objects.get(oid, {})
        kind = obj.get("objectKind") or ""
        intervals = list(own.get(oid, []))
        if kind in SERVICE_KINDS:
            for child in _descendants(oid, children):
                intervals += own.get(child, [])
        result.append(_row(oid, obj, depth, intervals, period_sec))
        for child in children.get(oid, []):
            walk(child, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)
    return result


def _descendants(oid: str, children: dict[str | None, list[str]]) -> list[str]:
    out, stack = [], list(children.get(oid, []))
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def _row(oid: str, obj: dict, depth: int, intervals: list, period_sec: float) -> AvailabilityRow:
    outage = merge([(b, e) for b, e, lvl, _ in intervals if lvl == "outage"])
    degraded = subtract(merge([(b, e) for b, e, lvl, _ in intervals if lvl == "degraded"]), outage)

    def names(b: datetime, e: datetime, level: str) -> list[str]:
        return sorted({a for ib, ie, lvl, a in intervals if lvl == level and ib < e and ie > b})

    spans = [Span(b, e, "outage", names(b, e, "outage")) for b, e in outage]
    spans += [Span(b, e, "degraded", names(b, e, "degraded")) for b, e in degraded]
    spans.sort(key=lambda s: s.begin)

    outage_sec = _seconds(outage)
    return AvailabilityRow(
        object_id=oid,
        name=obj.get("name") or oid,
        kind=obj.get("objectKind") or "",
        kind_name=obj.get("objectKindName") or obj.get("objectKind") or "",
        depth=depth,
        outage_sec=outage_sec,
        degraded_sec=_seconds(degraded),
        availability=round(100 * (1 - outage_sec / period_sec), 2),
        incidents=len(outage),
        longest_sec=int(max(((e - b).total_seconds() for b, e in outage), default=0)),
        spans=spans,
    )
