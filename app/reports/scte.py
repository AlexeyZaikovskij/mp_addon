"""SCTE-35 marker report: the splice commands MultiProbe recorded, ad breaks and markers missing
on some profiles of a stream.

MultiProbe keeps SCTE-35 messages as values of the "SCTE-35 Analyzer" (id 51) on a decoder object
(for HLS a virtual "PID: 8191; Scte35" per profile). The full message is the `SpliceCommand` value.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from .. import mp_client
from ..jobs import Job

SCTE_ANALYZER = 51
TICKS_PER_SECOND = 90_000  # SCTE-35 durations are in 90 kHz ticks
COMMANDS = {
    0x00: "splice_null", 0x04: "splice_schedule", 0x05: "splice_insert",
    0x06: "time_signal", 0x07: "bandwidth_reservation", 0xFF: "private_command",
}
SAME_MARKER_WINDOW = timedelta(minutes=2)  # one marker reaches the profiles within seconds of each other
MISSING_GRACE = timedelta(minutes=5)       # MultiProbe stores markers with a delay of up to a few minutes


@dataclass
class Marker:
    time: datetime
    object_id: str
    object: str           # the profile / service the decoder belongs to
    stream: str
    command: str
    command_type: int | None
    event_id: int | None
    out_of_network: bool | None
    duration_sec: float | None
    auto_return: bool | None
    cancel: bool
    immediate: bool | None
    segmentation: str     # time_signal: segmentation type ids, if present

    @property
    def direction(self) -> str:
        if self.out_of_network is None:
            return ""
        return "out" if self.out_of_network else "in"


@dataclass
class Break:
    object_id: str
    begin: datetime
    end: datetime
    event_id: int | None


@dataclass
class ScteReport:
    params: dict[str, Any]
    markers: list[Marker]
    objects: list[dict]                   # decoders that carry SCTE-35: id, name, stream
    breaks: list[Break] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    period: tuple[datetime, datetime] | None = None
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _label(oid: str, objects: dict[str, dict]) -> tuple[str, str]:
    """(profile/service name, stream name) for a decoder object."""
    obj = objects.get(oid, {})
    parent = objects.get(obj.get("parentID") or "", {})
    chain, cur, seen = [], obj, set()
    while cur and cur.get("parentID") and cur["parentID"] in objects and cur["parentID"] not in seen:
        seen.add(cur["parentID"])
        cur = objects[cur["parentID"]]
        chain.append(cur)
    stream = chain[-1].get("name", "") if chain else ""
    return parent.get("name") or obj.get("name") or oid, stream


def _segmentation(cmd: dict) -> str:
    """Segmentation type ids from time_signal descriptors (field names vary, so search loosely)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if "segmentationtypeid" in k.lower() and v is not None:
                    found.append(f"0x{int(v):02X}" if isinstance(v, int) else str(v))
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(cmd.get("spliceDescriptorTypes"))
    return ", ".join(dict.fromkeys(found))


def _marker(d: dict, oid: str, objects: dict, tz: ZoneInfo) -> Marker | None:
    v = d.get("valuesMap") or {}
    if all(x is None for x in v.values()):
        return None  # the decoder writes an empty row when it starts
    cmd = v.get("SpliceCommand") or {}
    item = cmd.get("spliceCommandItem") or {}
    ctype = v.get("splice_command_type")
    ticks = (item.get("breakDuration") or {}).get("duration")
    name, stream = _label(oid, objects)
    return Marker(
        time=datetime.fromisoformat(d["measureTime"]).astimezone(tz),
        object_id=oid,
        object=name,
        stream=stream,
        command=COMMANDS.get(ctype, f"0x{ctype:02X}" if isinstance(ctype, int) else "?"),
        command_type=ctype,
        event_id=item.get("spliceEventId"),
        out_of_network=item.get("outOfNetworkIndicator") if item.get("outOfNetworkIndicatorSpecified", True) else None,
        duration_sec=ticks / TICKS_PER_SECOND if isinstance(ticks, (int, float)) else None,
        auto_return=(item.get("breakDuration") or {}).get("autoReturn"),
        cancel=bool(item.get("spliceEventCancelIndicator")),
        immediate=item.get("spliceImmediateFlag"),
        segmentation=_segmentation(cmd),
    )


def scte_objects(selected: set[str], objects: dict[str, dict]) -> list[str]:
    """Enabled decoders with the SCTE-35 analyzer that are selected or lie inside a selected object."""
    def inside_selection(oid: str) -> bool:
        cur, seen = oid, set()
        while cur and cur not in seen:
            if cur in selected:
                return True
            seen.add(cur)
            cur = objects.get(cur, {}).get("parentID")
        return False

    return [
        oid for oid, o in objects.items()
        if not o.get("isDisabled")
        and any(a["analyzerID"] == SCTE_ANALYZER for a in o.get("analyzerItemList") or [])
        and inside_selection(oid)
    ]


async def build(job: Job, conns: dict, time_zone: str) -> ScteReport:
    from .alarms import is_multi, target_label
    p = job.params
    tz = ZoneInfo(time_zone)
    begin = datetime.fromisoformat(p["period_utc"][0])
    end = min(datetime.fromisoformat(p["period_utc"][1]), datetime.now(timezone.utc))
    multi = is_multi(p["targets"])
    job.total_steps = len(p["targets"])
    markers: list[Marker] = []
    decoders: list[dict] = []
    for t in p["targets"]:
        conn = conns[t["server_id"]]
        objects = {o["monitoringObjectID"]: o for o in await mp_client.monitoring_objects(conn, t["node_id"])}
        oids = scte_objects(set(t["object_ids"]), objects)
        catalog = await mp_client.measure_catalog(conn)
        columns = [m["columnName"] for m in (catalog.get(SCTE_ANALYZER) or {}).get("measureItemList") or []]
        label = target_label(t, multi)
        if oids and columns:
            items = await mp_client.measure_data(
                conn, SCTE_ANALYZER, oids, columns,
                begin.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                {"forceDisableInterpolation": True},
            )
            for it in items:
                for d in it.get("dataList") or []:
                    m = _marker(d, it.get("monitoringObjectID"), objects, tz)
                    if m and (p.get("scte_include_null") or m.command_type != 0x00):
                        if label:  # keep streams of different servers apart
                            m.stream = f"{label} · {m.stream}"
                        markers.append(m)
        for o in oids:
            name, stream = _label(o, objects)
            decoders.append({"id": o, "name": name, "stream": f"{label} · {stream}" if label else stream})
        job.done_steps += 1
        job.rows_fetched = len(markers)
    markers.sort(key=lambda m: (m.time, m.object))

    report = ScteReport(
        params=p,
        markers=markers,
        objects=sorted(decoders, key=lambda x: (x["stream"].lower(), x["name"].lower())),
        period=(begin.astimezone(tz), end.astimezone(tz)),
    )
    report.breaks = _breaks(markers, end.astimezone(tz))
    report.missing = _missing(markers, report.objects, end.astimezone(tz) - MISSING_GRACE)
    report.summary = _summarize(report)
    return report


def _breaks(markers: list[Marker], end: datetime) -> list[Break]:
    """Ad breaks per object: an OUT marker with a duration, or an OUT closed by the next IN."""
    out: list[Break] = []
    open_out: dict[str, Marker] = {}
    for m in markers:
        if m.cancel or m.out_of_network is None:
            continue
        if m.out_of_network:
            if m.duration_sec:
                out.append(Break(m.object_id, m.time, m.time + timedelta(seconds=m.duration_sec), m.event_id))
            else:
                open_out[m.object_id] = m
        elif m.object_id in open_out:
            start = open_out.pop(m.object_id)
            out.append(Break(m.object_id, start.time, m.time, start.event_id))
    for oid, start in open_out.items():  # still on a break when the period ends
        out.append(Break(oid, start.time, end, start.event_id))
    return out


def _missing(markers: list[Marker], objects: list[dict], settled_before: datetime) -> list[dict]:
    """Markers that reached some profiles of a stream but not others.

    Markers with the same event id (or, without one, the same command within a short window) on
    profiles of one stream are taken as the same marker.
    """
    profiles_by_stream: dict[str, list[dict]] = defaultdict(list)
    for o in objects:
        profiles_by_stream[o["stream"]].append(o)
    groups: dict[tuple, list[Marker]] = defaultdict(list)
    for m in markers:
        if m.event_id is not None:
            key = (m.stream, "id", m.event_id, m.cancel)
        else:  # no event id (e.g. time_signal): bucket by time
            key = (m.stream, "t", m.command, int(m.time.timestamp() // SAME_MARKER_WINDOW.total_seconds()))
        groups[key].append(m)
    out = []
    for key, ms in groups.items():
        profiles = profiles_by_stream.get(key[0], [])
        if len(profiles) < 2:
            continue
        if min(m.time for m in ms) > settled_before:
            continue
        got = {m.object_id for m in ms}
        lacking = [o["name"] for o in profiles if o["id"] not in got]
        if lacking:
            first = min(ms, key=lambda m: m.time)
            out.append({
                "time": first.time, "stream": key[0], "command": first.command, "event_id": first.event_id,
                "received": sorted({m.object for m in ms}), "missing": lacking,
            })
    out.sort(key=lambda x: x["time"], reverse=True)
    return out


def _summarize(r: ScteReport) -> dict[str, Any]:
    by_cmd = Counter(m.command for m in r.markers)
    # A break reaches every profile of the stream: count it once per stream (by event id).
    stream_of = {o["id"]: o["stream"] for o in r.objects}
    distinct: dict[tuple, Break] = {}
    for b in r.breaks:
        key = (stream_of.get(b.object_id, ""), b.event_id) if b.event_id is not None else \
              (stream_of.get(b.object_id, ""), int(b.begin.timestamp() // SAME_MARKER_WINDOW.total_seconds()))
        distinct.setdefault(key, b)
    durations = [b.end - b.begin for b in distinct.values()]
    # Interval between consecutive OUT markers on the same object
    gaps = []
    last: dict[str, datetime] = {}
    for m in r.markers:
        if m.out_of_network:
            if m.object_id in last:
                gaps.append((m.time - last[m.object_id]).total_seconds())
            last[m.object_id] = m.time
    per_object = []
    for o in r.objects:
        ms = [m for m in r.markers if m.object_id == o["id"]]
        bs = [b for b in r.breaks if b.object_id == o["id"]]
        per_object.append({
            **o,
            "markers": len(ms),
            "breaks": len(bs),
            "break_sec": sum((b.end - b.begin).total_seconds() for b in bs),
            "missed": sum(1 for x in r.missing if o["name"] in x["missing"] and x["stream"] == o["stream"]),
            "last": max((m.time for m in ms), default=None),
        })
    return {
        "markers": len(r.markers),
        "by_command": by_cmd.most_common(),
        "breaks": len(distinct),
        "break_total_sec": sum(d.total_seconds() for d in durations),
        "break_avg_sec": mean(d.total_seconds() for d in durations) if durations else None,
        "interval_avg_sec": mean(gaps) if gaps else None,
        "missing": len(r.missing),
        "objects": len(r.objects),
        "per_object": per_object,
    }
