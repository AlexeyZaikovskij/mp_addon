"""Patterns in measurements: hours of the day when a channel's value is regularly well below (or above) its
own usual level, and how the level drifts over the period. One measure (e.g. service bitrate) for every
channel of the shown servers that has it.

MultiProbe keeps measurements only a few days by default (Administration -> Cleanup, "Days to expire" 3):
the analysis uses what is there and says how many days that is; raising the expiry for just the bitrate
tables gives it weeks.
"""
import asyncio
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import charts, mp_client
from .jobs import Job
from .reports import units

BUCKET = {"interpolationIntervalType": "minutes", "interpolationInterval": 15}
CHUNK = 10  # objects per request
CONCURRENCY = 4
DEVIATION = 0.25  # an hour counts as off when it is this far from the channel's usual level ...
MIN_DAYS = 2  # ... on at least this many days
MIN_SHARE = 0.6  # ... and on this share of the days that have the hour
TREND = 0.2  # the level changed this much over the period (share of the usual level)
TREND_MIN_DAYS = 3


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


async def measures(conns: list) -> list[dict]:
    """Measures the channels of the shown servers have, grouped by analyzer, with how many channels have
    each analyzer and which measures have values in the last day."""
    count: dict[int, int] = defaultdict(int)
    objs, first = [], None
    for conn in conns:
        for node in await mp_client.nodes(conn):
            for o in await charts.objects(conn, node["nodeID"]):
                objs.append({"server_id": conn.server_id, "object_id": o["id"]})
                for a in o["analyzers"]:
                    count[a] += 1
        first = first or conn
    if not count:
        return []
    groups = await charts.measure_list(first, list(count))
    now = datetime.now(timezone.utc)
    filled = await charts.filled({c.server_id: c for c in conns}, objs, list(count), now - timedelta(hours=24), now)
    for g in groups:
        g["channels"] = count[g["id"]]
        have = set(filled.get(g["id"], []))
        g["filled"] = bool(have) or g["id"] not in filled
        for m in g["measures"]:
            m["filled"] = m["column"] in have or g["id"] not in filled
    return sorted(groups, key=lambda g: (not g["filled"], g["name"].lower()))


async def analyse(job: Job, conns: list, tz_name: str, days: int, analyzer_id: int, column: str) -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    end = datetime.now(timezone.utc)
    begin = end - timedelta(days=days)
    multi = len(conns) > 1

    # Channels having the measure's analyzer, per server.
    targets: list[tuple] = []  # (conn, [objects])
    for conn in conns:
        objs = []
        for node in await mp_client.nodes(conn):
            for o in await charts.objects(conn, node["nodeID"]):
                if analyzer_id in o["analyzers"]:
                    objs.append({**o, "server": conn.name if multi else "", "node": node.get("name") or ""})
        if objs:
            targets.append((conn, objs))
    chunks = [(conn, objs[i:i + CHUNK]) for conn, objs in targets for i in range(0, len(objs), CHUNK)]
    job.total_steps = max(1, len(chunks))
    meta = next((m for m in ((await mp_client.measure_catalog(conns[0])).get(analyzer_id) or {}).get("measureItemList") or []
                 if m.get("columnName") == column), {})
    analyzer_name = ((await mp_client.measure_catalog(conns[0])).get(analyzer_id) or {}).get("analyzerName") or ""

    sem = asyncio.Semaphore(CONCURRENCY)
    values: dict[tuple, list[tuple[datetime, float]]] = {}
    info: dict[tuple, dict] = {}

    async def fetch(conn, objs):
        async with sem:
            items = await mp_client.measure_data(conn, analyzer_id, [o["id"] for o in objs], [column],
                                                 _iso(begin), _iso(end), BUCKET)
        by_id = {it.get("monitoringObjectID"): it for it in items}
        for o in objs:
            pts = []
            for b in (by_id.get(o["id"]) or {}).get("boxPlotDataList") or []:
                v = ((b.get("valuesMap") or {}).get(column) or {}).get("average")
                if v is not None:
                    pts.append((datetime.fromisoformat(b["measureBeginTime"]).astimezone(tz), float(v)))
            key = (conn.server_id, o["id"])
            values[key] = pts
            info[key] = o
        job.done_steps += 1
        job.rows_fetched += sum(len(v) for v in values.values())

    await asyncio.gather(*(fetch(c, o) for c, o in chunks))

    all_values = [v for pts in values.values() for _, v in pts]
    unit, mult = units.choose(meta.get("unit") or "", statistics.median(map(abs, all_values)) if all_values else None)
    history = sorted({t.date() for pts in values.values() for t, _ in pts})
    findings, flat = [], 0
    for key, pts in values.items():
        res = _channel(pts)
        if res is None:
            continue
        if not res["hours"] and not res["trend"]:
            flat += 1
            continue
        o = info[key]
        findings.append({"server": o["server"], "node": o["node"], "object": o["name"], "stream": o["stream"],
                         "usual": round(res["usual"] * mult, 3), **{k: v for k, v in res.items() if k != "usual"},
                         "trend_from": round(res["trend_from"] * mult, 3) if res["trend"] else None,
                         "trend_to": round(res["trend_to"] * mult, 3) if res["trend"] else None,
                         "hours": [{**h, "value": round(h["value"] * mult, 3)} for h in res["hours"]]})
    findings.sort(key=lambda f: -max([abs(h["deviation"]) * h["days"] for h in f["hours"]] + [abs(f["trend"] or 0)]))
    # The same drift on many channels at once points at something they share (a CDN, the server's network).
    common = []
    analysed = sum(1 for p in values.values() if len(p) >= 8)
    for sign in (-1, 1):
        same = [f for f in findings if f["trend"] and (f["trend"] > 0) == (sign > 0)]
        if len(same) >= 3 and len(same) >= 0.5 * analysed:
            common.append({"sign": sign, "channels": len(same), "of": analysed,
                           "median": round(statistics.median(f["trend"] for f in same), 3)})
    return {
        "measure": meta.get("name") or column, "analyzer": analyzer_name, "unit": unit, "days": days,
        "history_days": len(history), "history_from": history[0].strftime("%d.%m.%Y") if history else "",
        "channels": len(values), "with_data": sum(1 for p in values.values() if p), "flat": flat,
        "findings": findings, "common": common,
    }


def _channel(pts: list[tuple[datetime, float]]) -> dict | None:
    """Hours regularly off the channel's usual level (median of all its values), and the level's drift."""
    if len(pts) < 8:
        return None
    usual = statistics.median(v for _, v in pts)
    scale = abs(usual)
    if scale == 0:
        return None
    by_day_hour: dict[tuple, list[float]] = defaultdict(list)
    for t, v in pts:
        by_day_hour[(t.date(), t.hour)].append(v)
    per_hour: dict[int, list[tuple[Any, float]]] = defaultdict(list)  # hour -> [(date, median)]
    for (d, h), vs in by_day_hour.items():
        per_hour[h].append((d, statistics.median(vs)))

    off = {}
    for h, rows in per_hour.items():
        for sign in (-1, 1):
            hit = [v for _, v in rows if sign * (v - usual) / scale >= DEVIATION]
            if len(hit) >= max(MIN_DAYS, math.ceil(MIN_SHARE * len(rows))):
                off[h] = {"sign": sign, "days": len(hit), "of_days": len(rows), "value": statistics.median(hit)}
    hours = []
    for h in sorted(off):  # neighbouring hours off the same way make one range
        cur = off[h]
        if hours and hours[-1]["to"] == h and hours[-1]["sign"] == cur["sign"]:
            last = hours[-1]
            last["to"] = h + 1
            last["days"] = min(last["days"], cur["days"])
            last["of_days"] = max(last["of_days"], cur["of_days"])
            last["values"].append(cur["value"])
        else:
            hours.append({"from": h, "to": h + 1, "sign": cur["sign"], "days": cur["days"], "of_days": cur["of_days"],
                          "values": [cur["value"]]})
    for r in hours:
        r["value"] = statistics.median(r.pop("values"))
        r["deviation"] = round((r["value"] - usual) / scale, 3)

    # Drift: the least-squares line through the daily medians, from its first day to its last.
    daily = sorted((d, statistics.median(v for t, v in pts if t.date() == d)) for d in {t.date() for t, _ in pts})
    trend = trend_from = trend_to = None
    if len(daily) >= TREND_MIN_DAYS:
        xs = [(d - daily[0][0]).days for d, _ in daily]
        ys = [v for _, v in daily]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
            trend_from, trend_to = my + slope * (xs[0] - mx), my + slope * (xs[-1] - mx)
            change = (trend_to - trend_from) / scale
            trend = round(change, 3) if abs(change) >= TREND else None
    return {"usual": usual, "hours": hours, "trend": trend, "trend_from": trend_from, "trend_to": trend_to,
            "days_with_data": len(daily)}
