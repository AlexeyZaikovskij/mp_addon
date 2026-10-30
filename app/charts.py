"""Charts page: each user's own board of charts, each chart one MultiProbe measure of one object.

The board (which charts, in what order and size) is kept per user in the kv table; the values always come
from MultiProbe, aggregated on its side as in the measurement report (reports/measures.py).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import mp_client, overview, storage
from .mp_client import Conn
from .reports import measures, units

PERIODS = (1, 6, 24, 168)  # hours a chart can show
MAX_OBJECTS = 8  # channels on one chart
RAW_UP_TO = 1  # hours: such short periods are shown point by point, longer ones in buckets


def _key(user_id: str) -> str:
    return f"charts:{user_id}"


def load_board(user_id: str) -> list[dict]:
    try:
        panels = json.loads(storage.get_kv(_key(user_id)) or "[]")
    except ValueError:
        return []
    for p in panels:  # boards saved before charts had several channels
        if "objects" not in p:
            oid = p.pop("object_id", None)
            p["objects"] = [{"server_id": p.get("server_id"), "node_id": p.get("node_id"), "id": oid}] if oid else []
    return panels


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


async def series(conns: dict[int, Conn], objects: list[dict], analyzer_id: int, column: str, hours: int,
                 tz: ZoneInfo) -> dict:
    """Values of one measure of several objects ({server_id, object_id}) over the last `hours`: average (and
    min / max per bucket when aggregated), one request per server. One unit for the whole chart.
    Times are the user's wall clock encoded as UTC milliseconds, as on the report pages."""
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours)
    resolution = "raw" if hours <= RAW_UP_TO else measures.pick_resolution("auto", begin, end)
    interpolation = ({"forceDisableInterpolation": True} if resolution == "raw" else
                     {"interpolationIntervalType": measures.STEPS[resolution][1],
                      "interpolationInterval": measures.STEPS[resolution][2]})
    b_iso, e_iso = begin.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")

    by_server: dict[int, list[str]] = {}
    for o in objects:
        by_server.setdefault(o["server_id"], []).append(o["object_id"])

    async def fetch(sid: int, oids: list[str]) -> dict[str, list]:
        items = await mp_client.measure_data(conns[sid], analyzer_id, oids, [column], b_iso, e_iso, interpolation)
        return {it.get("monitoringObjectID"): measures._points(it, column, tz, raw=resolution == "raw") for it in items}

    found = dict(zip(by_server, await asyncio.gather(*(fetch(sid, oids) for sid, oids in by_server.items()),
                                                     return_exceptions=True)))
    meta = next((m for m in ((await mp_client.measure_catalog(conns[objects[0]["server_id"]])).get(analyzer_id) or {})
                 .get("measureItemList") or [] if m.get("columnName") == column), {})
    out = []
    for o in objects:
        res = found[o["server_id"]]
        out.append({"server_id": o["server_id"], "object_id": o["object_id"],
                    "error": str(res)[:200] if isinstance(res, Exception) else "",
                    "points": [] if isinstance(res, Exception) else res.get(o["object_id"]) or []})
    # The unit follows the peak, so the axis labels stay under a thousand whatever dips the period has.
    peaks = [abs(v) for s in out for p in s["points"] for v in (p.max, p.avg) if v is not None]
    unit, mult = units.choose(meta.get("unit") or "", max(peaks) if peaks else None)
    ms = lambda t: round(t.replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    val = lambda v: None if v is None else v * mult  # noqa: E731
    for s in out:
        pts = s.pop("points")
        s.update(avg=[[ms(p.time), val(p.avg)] for p in pts], min=[[ms(p.time), val(p.min)] for p in pts],
                 max=[[ms(p.time), val(p.max)] for p in pts])
    return {"unit": unit, "raw": resolution == "raw", "resolution": resolution,
            "from": ms(begin.astimezone(tz)), "to": ms(end.astimezone(tz)), "series": out}
