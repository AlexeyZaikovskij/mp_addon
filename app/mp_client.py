"""Thin async client for the MultiProbe REST API. See docs/MP_API_NOTES.md."""
import time
from typing import Any

import httpx

from .config import settings

TOKEN_COOKIE = "access_token"
NO_PAGING = {"topCount": 100000, "fromIndex": 0}


class MPError(Exception):
    """MultiProbe answered with an error."""

    def __init__(self, code: str, message: str = "", status: int = 0):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class MPAuthError(MPError):
    """Wrong credentials, or the token is no longer accepted."""


_AUTH_CODES = {
    "authErrorWrongLoginPassword",
    "authErrorUserDisabled",
    "authError",
    "authErrorUserBanned",
    "authErrorUnauthorized",
}

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.mp_api_url, timeout=120)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("error") or f"http{resp.status_code}"
    message = body.get("message") or resp.text[:200]
    if resp.status_code == 401 or code in _AUTH_CODES:
        raise MPAuthError(code, message, resp.status_code)
    raise MPError(code, message, resp.status_code)


async def login(login_name: str, password: str) -> tuple[str, dict[str, Any]]:
    """Create a MultiProbe session. Returns (token, profile)."""
    try:
        resp = await client().post(
            "/api/Admin/Login/CreateLogin", json={"login": login_name, "password": password}
        )
    except httpx.HTTPError as exc:
        raise MPError("unreachable", str(exc)) from exc
    _raise_for(resp)
    token = resp.cookies.get(TOKEN_COOKIE)
    if not token:
        raise MPError("noToken", "MultiProbe did not return an access token")
    return token, resp.json().get("profile") or {}


async def call(token: str, method: str, path: str, body: Any = None) -> Any:
    try:
        resp = await client().request(
            method, path, json=body, headers={"Cookie": f"{TOKEN_COOKIE}={token}"}
        )
    except httpx.HTTPError as exc:
        raise MPError("unreachable", str(exc)) from exc
    _raise_for(resp)
    return resp.json() if resp.content else None


# --- Dictionaries -----------------------------------------------------------

async def nodes(token: str) -> list[dict]:
    """Probe nodes the user can see (the DB server pseudo-node is left out)."""
    items = await call(token, "POST", "/api/Probe/Node/Find", {})
    return [n for n in items if n.get("nodeType") != "system"]


async def monitoring_objects(token: str, node_id: int) -> list[dict]:
    return await call(token, "POST", "/api/Probe/MonitoringObject/Find", {"nodeID": node_id})


_CATALOG_TTL = 600
_catalogs: dict[str, tuple[float, Any]] = {}


async def _cached(name: str, token: str, method: str, path: str, body: Any = None) -> Any:
    """Dictionaries that are the same for every user, cached for 10 minutes."""
    hit = _catalogs.get(name)
    if hit and time.monotonic() - hit[0] < _CATALOG_TTL:
        return hit[1]
    value = await call(token, method, path, body)
    _catalogs[name] = (time.monotonic(), value)
    return value


async def measure_catalog(token: str) -> dict[int, dict]:
    """analyzerID -> {analyzerName, measureItemList}."""
    items = await _cached("measures", token, "POST", "/api/Monitor/AnalyzerMeasure/Find", {})
    return {a["analyzerID"]: a for a in items}


async def alarm_catalog(token: str) -> dict[int, dict]:
    """alarmID -> alarm definition (alarmName, alarmGroupName, priority, analyzerID, conditionOn, ...)."""
    items = await _cached("alarms", token, "POST", "/api/Monitor/Alarm/Find", {})
    return {a["alarmID"]: a for a in items}


async def alarm_groups(token: str) -> list[dict]:
    """Priority bands: Log 0-99, Advisory 100-199, Caution, Warning, Error 400-499."""
    return await _cached("alarm_groups", token, "GET", "/api/Monitor/AlarmGroup/Get")


# --- Measurements -----------------------------------------------------------

def _measure_body(analyzer_id: int, object_ids: list[str], columns: list[str], begin: str, end: str,
                  interpolation: dict) -> dict:
    return {
        "dataFilter": {
            "beginDate": begin,
            "endDate": end,
            "pagingProperty": {"topCount": 1_000_000, "fromIndex": 0},  # default page is only 20
            "interpolation": interpolation,
        },
        "analyzeMonitoringObjectList": [{"analyzerID": analyzer_id, "compoundObjectID": oid} for oid in object_ids],
        # measureNameList takes the measure *column* names, not the display names
        "analyzerMeasuresList": [{"analyzerID": analyzer_id, "measureNameList": columns}],
    }


async def measure_data(token: str, analyzer_id: int, object_ids: list[str], columns: list[str],
                       begin: str, end: str, interpolation: dict) -> list[dict]:
    """Per object: `boxPlotDataList` (aggregated buckets) or `dataList` (raw points when
    interpolation is {"forceDisableInterpolation": true})."""
    body = _measure_body(analyzer_id, object_ids, columns, begin, end, interpolation)
    resp = await call(token, "POST", "/api/Measurement/LogData/Find", body) or {}
    return resp.get("measurementResponseItemList") or []


async def measure_averages(token: str, analyzer_id: int, object_id: str, columns: list[str],
                           begin: str, end: str) -> dict[str, dict]:
    """Exact average and standard deviation over the whole period: {column: {average, standardDeviation}}.
    One object per call: the response items carry no object id, only their order."""
    body = _measure_body(analyzer_id, [object_id], columns, begin, end, {})
    resp = await call(token, "POST", "/api/Measurement/LogData/FindAverages", body) or {}
    items = resp.get("data") or []
    return (items[0].get("values") or {}) if items else {}


# --- Alarm log --------------------------------------------------------------

ALARM_PAGE = 500  # bigger pages get slower per row (1000 rows ~15 s vs 500 rows ~5 s)


async def alarm_log_window(
    token: str, object_ids: list[str], begin: str, end: str
) -> list[dict]:
    """All alarm-log rows of the objects that overlap [begin, end], fetched page by page.

    The object filter matches the *compound* (view) object, so a stream also returns the events
    of everything inside it, and one event can come back several times — callers deduplicate.
    """
    rows: list[dict] = []
    while True:
        body = {
            "dataFilter": {
                "beginDate": begin,
                "endDate": end,
                "pagingProperty": {"topCount": ALARM_PAGE, "fromIndex": len(rows)},
            },
            "analyzeMonitoringObjectList": [{"compoundObjectID": oid} for oid in object_ids],
        }
        page = (await call(token, "POST", "/api/Alarm/MonitoringObjectLog/Find", body)) or {}
        items = page.get("monitoringObjectLog") or []
        rows.extend(items)
        if len(items) < ALARM_PAGE:
            return rows
