"""Measurement report: values of the selected measures over the period, per object.

MultiProbe aggregates on its side: for each time bucket it returns min / quartiles / median /
average / max, so long periods stay light. Raw points (one every few seconds) are only offered for
short periods.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import mp_client
from ..jobs import Job
from . import units

CONCURRENCY = 4
TARGET_BUCKETS = 720            # "auto" picks the finest step that stays under this per series
RAW_MAX_PERIOD = timedelta(hours=24)

# resolution -> (bucket length, MultiProbe interpolation type, interval)
STEPS: dict[str, tuple[timedelta, str, int]] = {
    "1m": (timedelta(minutes=1), "minutes", 1),
    "5m": (timedelta(minutes=5), "minutes", 5),
    "15m": (timedelta(minutes=15), "minutes", 15),
    "30m": (timedelta(minutes=30), "minutes", 30),
    "1h": (timedelta(hours=1), "hours", 1),
    "3h": (timedelta(hours=3), "hours", 3),
    "6h": (timedelta(hours=6), "hours", 6),
    "1d": (timedelta(days=1), "days", 1),
}


@dataclass
class Point:
    time: datetime          # bucket start (or the sample time for raw data), user's time zone
    min: float | None
    avg: float | None
    max: float | None


@dataclass
class Series:
    object_id: str
    object: str
    stream: str
    analyzer_id: int
    analyzer: str
    column: str
    measure: str
    unit: str
    points: list[Point] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    display_unit: str = ""   # unit shown to people, e.g. Mbit/s for a bps measure
    mult: float = 1.0        # display value = raw value × mult

    @property
    def key(self) -> str:
        return f"{self.analyzer_id}|{self.column}"

    def d(self, value: float | None) -> float | None:
        """A raw value in the display unit."""
        return None if value is None else value * self.mult


@dataclass
class MeasureReport:
    params: dict[str, Any]
    series: list[Series]
    resolution: str                       # the step actually used, e.g. "5m" or "raw"
    step: timedelta | None
    period: tuple[datetime, datetime]
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def measures(self) -> list[tuple[str, str, str]]:
        """Distinct (key, title, display unit), in the order the user's analyzers/measures come."""
        seen, out = set(), []
        for s in self.series:
            if s.key not in seen:
                seen.add(s.key)
                out.append((s.key, f"{s.measure}", s.display_unit))
        return out


def pick_resolution(requested: str, begin: datetime, end: datetime) -> str:
    if requested == "raw":
        if end - begin > RAW_MAX_PERIOD:
            raise ValueError("raw_too_long")
        return "raw"
    if requested in STEPS:
        return requested
    total = end - begin
    for name, (step, _, _) in STEPS.items():
        if total / step <= TARGET_BUCKETS:
            return name
    return "1d"


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, (dict, list, str)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stream_of(oid: str, objects: dict[str, dict]) -> str:
    seen, cur = set(), objects.get(oid)
    while cur and cur.get("parentID") and cur["parentID"] not in seen and cur["parentID"] in objects:
        seen.add(cur["parentID"])
        cur = objects[cur["parentID"]]
    return "" if cur is None or cur.get("monitoringObjectID") == oid else cur.get("name") or ""


async def build(job: Job, token: str, time_zone: str) -> MeasureReport:
    p = job.params
    tz = ZoneInfo(time_zone)
    begin = datetime.fromisoformat(p["period_utc"][0])
    end = min(datetime.fromisoformat(p["period_utc"][1]), datetime.now(timezone.utc))
    resolution = pick_resolution(p.get("resolution") or "auto", begin, end)
    step = STEPS[resolution][0] if resolution in STEPS else None
    interpolation = (
        {"forceDisableInterpolation": True}
        if resolution == "raw"
        else {"interpolationIntervalType": STEPS[resolution][1], "interpolationInterval": STEPS[resolution][2]}
    )
    b_iso, e_iso = begin.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")

    objects = {o["monitoringObjectID"]: o for o in await mp_client.monitoring_objects(token, p["node_id"])}
    catalog = await mp_client.measure_catalog(token)
    selected = [oid for oid in p["object_ids"] if oid in objects]

    # One request per analyzer, with every selected object that has that analyzer.
    work: list[tuple[int, list[str], list[str]]] = []
    for aid_str, columns in (p.get("measures") or {}).items():
        aid = int(aid_str)
        oids = [o for o in selected if any(a["analyzerID"] == aid for a in objects[o].get("analyzerItemList") or [])]
        if oids and columns:
            work.append((aid, oids, list(columns)))
    job.total_steps = max(1, sum(1 + len(oids) for _, oids, _ in work))  # data + averages per object

    series: list[Series] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def limited(coro):
        async with sem:
            return await coro

    async def fetch(aid: int, oids: list[str], columns: list[str]) -> None:
        analyzer = catalog.get(aid, {})
        meta = {m["columnName"]: m for m in analyzer.get("measureItemList") or []}
        items = await limited(mp_client.measure_data(token, aid, oids, columns, b_iso, e_iso, interpolation))
        job.done_steps += 1
        by_object = {it.get("monitoringObjectID"): it for it in items}
        averages = await asyncio.gather(
            *(limited(mp_client.measure_averages(token, aid, oid, columns, b_iso, e_iso)) for oid in oids)
        )
        job.done_steps += len(oids)
        for oid, avg in zip(oids, averages):
            item = by_object.get(oid, {})
            for col in columns:
                m = meta.get(col, {})
                s = Series(
                    object_id=oid,
                    object=objects[oid].get("name") or oid,
                    stream=_stream_of(oid, objects),
                    analyzer_id=aid,
                    analyzer=analyzer.get("analyzerName") or str(aid),
                    column=col,
                    measure=m.get("name") or col,
                    unit=m.get("unit") or "",
                )
                s.points = _points(item, col, tz, raw=resolution == "raw")
                job.rows_fetched += len(s.points)
                s.stats = _stats(s.points, avg.get(col) or {}, begin, end, step)
                series.append(s)

    await asyncio.gather(*(fetch(*w) for w in work))

    order = {oid: i for i, oid in enumerate(p["object_ids"])}
    measure_order = {f"{aid}|{c}": (i, j) for i, (aid, cols) in enumerate((p.get("measures") or {}).items())
                     for j, c in enumerate(cols)}
    series.sort(key=lambda s: (measure_order.get(s.key, (99, 99)), s.stream.lower(), s.object.lower(), order.get(s.object_id, 0)))
    _assign_display_units(series)
    return MeasureReport(
        params=p, series=series, resolution=resolution, step=step,
        period=(begin.astimezone(tz), end.astimezone(tz)),
    )


def _assign_display_units(series: list[Series]) -> None:
    """One display unit per measurement, from the largest typical (average) value of its objects."""
    by_key: dict[str, list[Series]] = {}
    for s in series:
        by_key.setdefault(s.key, []).append(s)
    for group in by_key.values():
        values = [abs(v) for s in group for v in (s.stats.get("avg"), s.stats.get("max")) if v is not None]
        avgs = [abs(s.stats["avg"]) for s in group if s.stats.get("avg") is not None]
        magnitude = max(avgs) if avgs and max(avgs) > 0 else (max(values) if values else None)
        label, mult = units.choose(group[0].unit, magnitude)
        for s in group:
            s.display_unit, s.mult = label, mult


def _points(item: dict, column: str, tz: ZoneInfo, raw: bool) -> list[Point]:
    out = []
    if raw:
        for d in item.get("dataList") or []:
            v = _num((d.get("valuesMap") or {}).get(column))
            out.append(Point(datetime.fromisoformat(d["measureTime"]).astimezone(tz), v, v, v))
    else:
        for b in item.get("boxPlotDataList") or []:
            v = (b.get("valuesMap") or {}).get(column) or {}
            out.append(Point(
                datetime.fromisoformat(b["measureBeginTime"]).astimezone(tz),
                _num(v.get("minimumScore")), _num(v.get("average")), _num(v.get("maximumScore")),
            ))
    out.sort(key=lambda p: p.time)
    return out


def _stats(points: list[Point], exact: dict, begin: datetime, end: datetime, step: timedelta | None) -> dict:
    with_data = [p for p in points if p.avg is not None]
    mins = [p.min for p in with_data if p.min is not None]
    maxs = [p.max for p in with_data if p.max is not None]
    if step:
        expected = max(1, round((end - begin) / step))
        coverage = min(100.0, 100 * len(with_data) / expected)
    else:
        coverage = None  # raw data: the sampling interval is not known up front
    avg = _num(exact.get("average"))
    if avg is None and with_data:
        avg = sum(p.avg for p in with_data) / len(with_data)
    return {
        "min": min(mins) if mins else None,
        "max": max(maxs) if maxs else None,
        "avg": avg,
        "std": _num(exact.get("standardDeviation")),
        "points": len(with_data),
        "coverage": coverage,
    }
