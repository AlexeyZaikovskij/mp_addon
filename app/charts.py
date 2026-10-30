"""Charts page: each user's own board of charts, each chart one MultiProbe measure of one object.

The board (which charts, in what order and size) is kept per user in the kv table; the values always come
from MultiProbe, aggregated on its side as in the measurement report (reports/measures.py).
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import mp_client, overview, storage
from .mp_client import Conn
from .reports import measures, units

PERIODS = (1, 6, 24, 168)  # hours a chart can show
RAW_UP_TO = 1  # hours: such short periods are shown point by point, longer ones in buckets


def _key(user_id: str) -> str:
    return f"charts:{user_id}"


def load_board(user_id: str) -> list[dict]:
    try:
        return json.loads(storage.get_kv(_key(user_id)) or "[]")
    except ValueError:
        return []


def save_board(user_id: str, panels: list[dict]) -> None:
    storage.set_kv(_key(user_id), json.dumps(panels, ensure_ascii=False))


async def objects(conn: Conn, node_id: int) -> list[dict]:
    """Objects of a node that have measures, streams first with their objects after each one."""
    items = [o for o in await mp_client.monitoring_objects(conn, node_id) if overview.reportable(o)]
    catalog = await mp_client.measure_catalog(conn)
    by_id = {o["monitoringObjectID"]: o for o in items}
    out = []
    for o in items:
        analyzers = [a["analyzerID"] for a in o.get("analyzerItemList") or []
                     if any(not str(m.get("dataType") or "").startswith("xml")
                            for m in (catalog.get(a["analyzerID"]) or {}).get("measureItemList") or [])]
        if analyzers:
            stream = measures._stream_of(o["monitoringObjectID"], by_id)
            out.append({"id": o["monitoringObjectID"], "name": o.get("name") or "", "stream": stream,
                        "kind": o.get("objectKindName") or o.get("objectKind") or "", "analyzers": analyzers})
    return sorted(out, key=lambda o: ((o["stream"] or o["name"]).lower(), bool(o["stream"]), o["name"].lower()))


async def measure_list(conn: Conn, analyzer_ids: list[int]) -> list[dict]:
    """Measures of these analyzers, grouped by analyzer, in MultiProbe's order."""
    catalog = await mp_client.measure_catalog(conn)
    out = []
    for aid in sorted(set(analyzer_ids)):
        a = catalog.get(aid) or {}
        items = [{"column": m["columnName"], "name": m["name"], "unit": m.get("unit") or ""}
                 for m in sorted(a.get("measureItemList") or [], key=lambda m: m.get("orderby") or 0)
                 if not str(m.get("dataType") or "").startswith("xml")]
        if items:
            out.append({"id": aid, "name": a.get("analyzerName") or str(aid), "measures": items})
    return sorted(out, key=lambda a: a["name"].lower())


async def series(conn: Conn, object_id: str, analyzer_id: int, column: str, hours: int, tz: ZoneInfo) -> dict:
    """Values of one measure over the last `hours`: average (and min / max per bucket when aggregated).
    Times are the user's wall clock encoded as UTC milliseconds, as on the report pages."""
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours)
    resolution = "raw" if hours <= RAW_UP_TO else measures.pick_resolution("auto", begin, end)
    interpolation = ({"forceDisableInterpolation": True} if resolution == "raw" else
                     {"interpolationIntervalType": measures.STEPS[resolution][1],
                      "interpolationInterval": measures.STEPS[resolution][2]})
    items = await mp_client.measure_data(conn, analyzer_id, [object_id], [column], begin.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         end.strftime("%Y-%m-%dT%H:%M:%SZ"), interpolation)
    item = next((it for it in items if it.get("monitoringObjectID") == object_id), items[0] if items else {})
    points = measures._points(item, column, tz, raw=resolution == "raw")

    meta = next((m for m in ((await mp_client.measure_catalog(conn)).get(analyzer_id) or {}).get("measureItemList") or []
                 if m.get("columnName") == column), {})
    # The unit follows the peak, so the axis labels stay under a thousand whatever dips the period has.
    peaks = [abs(v) for p in points for v in (p.max, p.avg) if v is not None]
    unit, mult = units.choose(meta.get("unit") or "", max(peaks) if peaks else None)
    ms = lambda t: round(t.replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    val = lambda v: None if v is None else v * mult  # noqa: E731
    return {
        "unit": unit, "raw": resolution == "raw", "resolution": resolution,
        "from": ms(begin.astimezone(tz)), "to": ms(end.astimezone(tz)),
        "avg": [[ms(p.time), val(p.avg)] for p in points],
        "min": [[ms(p.time), val(p.min)] for p in points],
        "max": [[ms(p.time), val(p.max)] for p in points],
    }
