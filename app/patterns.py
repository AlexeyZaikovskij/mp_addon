"""Patterns: alarms that come back at the same time of day (or of the week), alarms that flap, and when
problems start (weekday x hour), from MultiProbe's alarm log over the last weeks, with rule-based advice.

Measurements are not used: MultiProbe keeps them only a few days by default (Administration -> Cleanup,
"Days to expire" 3), while the alarm log goes back months.
"""
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import mp_client, overview
from .jobs import Job
from .reports import alarms as alarm_report

PERIODS = (7, 14, 28)  # days
LEVELS = (100, 200, 300)  # lowest priority analysed: Advisory, Caution, Warning (Log 0-99 is never used)
WINDOW_MIN = 30  # a recurring alarm starts within this many minutes of the same time of day
MIN_DAYS = 3  # ... on at least this many different days
MIN_SHARE_DAYS = 0.5  # ... and on at least this share of the days in the period
MIN_LIFT = 6  # its starts are this many times denser in the window than spread over the whole day
WEEKLY_MIN_WEEKS = 3  # a weekly time needs at least three weeks: two Fridays alike are often chance
SAME_TIME_MIN = 20  # recurring alarms of different channels this close are reported together
FLAP_PER_DAY = 20  # more starts a day than this: the alarm flaps
TOP_FLAPPING = 15


def _clock(minutes: float) -> str:
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _circ_dist(a: float, b: float) -> float:
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def _densest(events: list[tuple[float, Any]]) -> tuple[list[tuple[float, Any]], float]:
    """The events (minute of day, day key) inside the densest WINDOW_MIN window, counted in distinct
    days, wrapping around midnight; and the window's centre (minute of day)."""
    if not events:
        return [], 0.0
    ev = sorted(events, key=lambda e: e[0])
    ext = ev + [(m + 1440, d) for m, d in ev]
    best: list = []
    j = 0
    for i in range(len(ev)):
        while j < len(ext) and ext[j][0] < ext[i][0] + WINDOW_MIN:
            j += 1
        inside = ext[i:j]
        if len({d for _, d in inside}) > len({d for _, d in best}):
            best = inside
    # Centre on the circle: the mean direction of the minutes in the window.
    ang = [2 * math.pi * (m % 1440) / 1440 for m, _ in best]
    centre = (math.degrees(math.atan2(sum(map(math.sin, ang)), sum(map(math.cos, ang)))) % 360) * 4
    return [(m % 1440, d) for m, d in best], centre


def _recurrence(starts: list[datetime], days: int) -> dict | None:
    """A daily or weekly time at which these starts keep coming back, or None."""
    lift_of = lambda inside, total: (len(inside) / total) / (WINDOW_MIN / 1440)  # noqa: E731
    minute = lambda t: t.hour * 60 + t.minute + t.second / 60  # noqa: E731
    events = [(minute(t), t.date()) for t in starts]
    inside, centre = _densest(events)
    n_days = len({d for _, d in inside})
    if n_days >= max(MIN_DAYS, math.ceil(MIN_SHARE_DAYS * days)) and lift_of(inside, len(events)) >= MIN_LIFT:
        spread = max((_circ_dist(m, centre) for m, _ in inside), default=0)
        return {"kind": "daily", "time": _clock(centre), "minute": centre, "spread": round(spread), "days": n_days,
                "of_days": days, "weekdays": sorted({d.weekday() for _, d in inside})}
    if days < 7 * WEEKLY_MIN_WEEKS:
        return None
    weeks = days / 7
    for wd in range(7):
        same = [(m, d) for m, d in events if d.weekday() == wd]
        inside, centre = _densest(same)
        n_weeks = len({d for _, d in inside})
        if n_weeks >= max(WEEKLY_MIN_WEEKS, math.ceil(0.6 * weeks)) and lift_of(inside, len(same)) >= MIN_LIFT                 and len(inside) >= 0.3 * len(same):
            spread = max((_circ_dist(m, centre) for m, _ in inside), default=0)
            return {"kind": "weekly", "time": _clock(centre), "minute": centre, "spread": round(spread),
                    "weekday": wd, "days": n_weeks, "of_days": math.floor(weeks)}
    return None


async def analyse(job: Job, conns: list, tz_name: str, days: int, level: int) -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    end = datetime.now(timezone.utc)
    begin = end - timedelta(days=days)
    windows = alarm_report._windows(begin, end)
    now = datetime.now(tz)
    multi = len(conns) > 1

    targets = []
    for conn in conns:
        for node in await mp_client.nodes(conn):
            targets.append((conn, node))
    job.total_steps = max(1, len(windows) * len(targets))

    rows: list[alarm_report.AlarmRow] = []
    for conn, node in targets:
        objects = await mp_client.monitoring_objects(conn, node["nodeID"])
        ids = [o["monitoringObjectID"] for o in objects if overview.reportable(o)]
        t = {"server_id": conn.server_id, "node_id": node["nodeID"], "object_ids": ids}
        t_rows, _, _ = await alarm_report._fetch_target(job, conn, t, {}, windows, tz, now)
        for r in t_rows:
            r.server = conn.name if multi else ""
        rows += [r for r in t_rows if r.priority >= level]

    # --- Per channel and alarm ------------------------------------------------------------------------
    groups: dict[tuple, list[alarm_report.AlarmRow]] = defaultdict(list)
    for r in rows:
        if r.detected >= begin.astimezone(tz):  # events that began before the period only overlap it
            groups[(r.server, r.node, r.object_id, r.alarm)].append(r)

    recurring, flapping = [], []
    for (server, node, oid, alarm), items in groups.items():
        first = items[0]
        base = {"server": server, "node": node, "object_id": oid, "object": first.object, "stream": first.stream,
                "alarm": alarm, "priority": max(i.priority for i in items), "group": first.group, "count": len(items)}
        per_day = len(items) / days
        if per_day >= FLAP_PER_DAY:
            flapping.append({**base, "per_day": round(per_day, 1),
                             "median_sec": round(statistics.median(i.duration_sec for i in items))})
            continue
        rec = _recurrence([i.detected for i in items], days)
        if rec:
            near = [i for i in items if _circ_dist(i.detected.hour * 60 + i.detected.minute, rec["minute"]) <= WINDOW_MIN]
            recurring.append({**base, **rec, "median_sec": round(statistics.median(i.duration_sec for i in near or items)),
                              "last": max(i.detected for i in items).strftime("%d.%m %H:%M")})

    # --- When problems start: weekday x hour, without the flapping alarms (listed apart, they would fill
    # every hour) ---------------------------------------------------------------------------------------
    flappers = {(f["server"], f["node"], f["object_id"], f["alarm"]) for f in flapping}
    heat = [[0] * 24 for _ in range(7)]
    first_day = begin.astimezone(tz).date()
    dates = [first_day + timedelta(days=i) for i in range((end.astimezone(tz).date() - first_day).days + 1)]
    by_date = {d: [0] * 24 for d in dates}  # the same counts date by date, for the "by date" view
    for key, items in groups.items():
        if key not in flappers:
            for r in items:
                heat[r.detected.weekday()][r.detected.hour] += 1
                if r.detected.date() in by_date:
                    by_date[r.detected.date()][r.detected.hour] += 1

    # Recurring alarms at the same time on several channels of one stream, or on several streams of one
    # node, point above the channel: they are reported as one finding.
    findings = _join(recurring)
    flapping.sort(key=lambda f: -f["per_day"])
    hours = sorted(((sum(heat[d][h] for d in range(7)), h) for h in range(24)), reverse=True)
    return {
        "days": days, "level": level, "events": len(rows), "channels": len({(r.server, r.object_id) for r in rows}),
        "period": [begin.astimezone(tz).strftime("%d.%m.%Y %H:%M"), end.astimezone(tz).strftime("%d.%m.%Y %H:%M")],
        "heat": heat,
        "heat_dates": [{"date": d.strftime("%d.%m"), "weekday": d.weekday(), "hours": by_date[d]} for d in dates],
        "busy_hours": [h for n, h in hours[:3] if n],
        "findings": findings, "flapping": flapping[:TOP_FLAPPING], "flapping_total": len(flapping),
    }


def _join(recurring: list[dict]) -> list[dict]:
    """Recurring alarms grouped by time (and weekday for weekly ones): a group with several channels of
    one stream is a stream finding, with several streams of one node a node finding."""
    recurring.sort(key=lambda r: (r["kind"], r.get("weekday", -1), r["minute"]))
    clusters: list[list[dict]] = []
    for r in recurring:
        c = next((c for c in clusters if c[0]["kind"] == r["kind"] and c[0].get("weekday") == r.get("weekday")
                  and c[0]["server"] == r["server"] and c[0]["node"] == r["node"]
                  and _circ_dist(c[0]["minute"], r["minute"]) <= SAME_TIME_MIN), None)
        if c is None:
            clusters.append([r])
        else:
            c.append(r)
    out = []
    for c in clusters:
        channels = {x["object_id"] for x in c}
        streams = {x["stream"] or x["object"] for x in c}
        scope = "node" if len(streams) > 1 and len(channels) > 2 else "stream" if len(channels) > 1 else "channel"
        lead = max(c, key=lambda x: (x["days"], x["priority"]))
        out.append({"scope": scope, "kind": lead["kind"], "time": lead["time"], "spread": max(x["spread"] for x in c),
                    "weekday": lead.get("weekday"), "days": lead["days"], "of_days": lead["of_days"],
                    "server": lead["server"], "node": lead["node"], "streams": sorted(streams),
                    "priority": max(x["priority"] for x in c), "items": sorted(c, key=lambda x: (-x["days"], x["object"]))})
    rank = {"node": 0, "stream": 1, "channel": 2}
    out.sort(key=lambda f: (-f["days"] / max(f["of_days"], 1), rank[f["scope"]], -f["priority"]))
    return out
