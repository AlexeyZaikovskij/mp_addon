"""MultiProbe report add-on: web entry point."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import i18n, jobs, mp_client, sessions
from .config import ROOT, settings
from .reports import alarms as alarm_report
from .reports import export


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await mp_client.close()


app = FastAPI(title="MultiProbe Reports", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")


class SessionExpired(Exception):
    pass


@app.exception_handler(SessionExpired)
async def _session_expired(request: Request, _: SessionExpired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "session"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(mp_client.MPError)
async def _mp_error(request: Request, exc: mp_client.MPError):
    if isinstance(exc, mp_client.MPAuthError):
        sessions.drop(request.cookies.get(sessions.COOKIE_NAME))
        return await _session_expired(request, SessionExpired())
    return JSONResponse({"error": exc.code, "message": exc.message}, status_code=502)


def current_session(request: Request) -> sessions.Session:
    session = sessions.get(request.cookies.get(sessions.COOKIE_NAME))
    if session is None:
        raise SessionExpired()
    return session


def render(request: Request, name: str, **context) -> HTMLResponse:
    lang = i18n.pick_lang(request)
    return templates.TemplateResponse(
        request, name, {"lang": lang, "t": i18n.translator(lang), **context}
    )


# --- Pages ------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html", error=None, login_name="")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    t = i18n.translator(i18n.pick_lang(request))
    try:
        token, profile = await mp_client.login(login, password)
    except mp_client.MPAuthError as exc:
        error = {
            "authErrorUserDisabled": t("err_user_disabled"),
            "authErrorUserBanned": t("err_user_banned"),
        }.get(exc.code, t("err_wrong_credentials"))
        return render(request, "login.html", error=error, login_name=login)
    except mp_client.MPError as exc:
        error = t("err_unreachable") if exc.code == "unreachable" else t("err_generic", msg=exc.message)
        return render(request, "login.html", error=error, login_name=login)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        sessions.COOKIE_NAME, sessions.create(token, profile), httponly=True, samesite="lax"
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    sessions.drop(request.cookies.get(sessions.COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(sessions.COOKIE_NAME)
    return response


@app.get("/lang/{code}")
async def set_lang(code: str, request: Request):
    response = RedirectResponse(request.headers.get("referer") or "/", status_code=303)
    if code in i18n.LANGS:
        response.set_cookie(i18n.LANG_COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
async def wizard(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "wizard.html", session=session, strings=i18n.bundle(lang))


# --- Wizard data ------------------------------------------------------------

@app.get("/api/nodes")
async def api_nodes(session: sessions.Session = Depends(current_session)):
    items = await mp_client.nodes(session.token)
    return [{"id": n["nodeID"], "name": n["name"]} for n in sorted(items, key=lambda n: n["name"])]


# Server hardware and peripherals (GPU, NIC, audio devices, script-fed external devices) are not
# streams, so they stay out of the report wizard for now.
_HARDWARE_KINDS = {"device", "deviceConnector", "externalDevice"}


def _is_reportable(obj: dict) -> bool:
    return (
        obj.get("objectKind") not in _HARDWARE_KINDS
        and not obj.get("isDisabled")
        and not str(obj.get("objectType") or "").endswith("Hidden")
    )


@app.get("/api/objects")
async def api_objects(node_id: int, session: sessions.Session = Depends(current_session)):
    """Streams and the objects inside them (services, PIDs), without hidden ones."""
    items = await mp_client.monitoring_objects(session.token, node_id)
    return [
        {
            "id": o["monitoringObjectID"],
            "parentId": o.get("parentID"),
            "name": o.get("name") or "",
            "kind": o.get("objectKind"),
            "kindName": o.get("objectKindName") or o.get("objectKind"),
            "analyzers": [a["analyzerID"] for a in o.get("analyzerItemList") or []],
        }
        for o in items
        if _is_reportable(o)
    ]


class MeasuresRequest(BaseModel):
    analyzer_ids: list[int]


@app.post("/api/measures")
async def api_measures(req: MeasuresRequest, session: sessions.Session = Depends(current_session)):
    """Measures available for the analyzers of the selected objects, grouped by analyzer."""
    catalog = await mp_client.measure_catalog(session.token)
    result = []
    for analyzer_id in sorted(set(req.analyzer_ids)):
        analyzer = catalog.get(analyzer_id)
        if not analyzer or not analyzer.get("measureItemList"):
            continue
        measures = sorted(analyzer["measureItemList"], key=lambda m: m.get("orderby") or 0)
        result.append(
            {
                "id": analyzer_id,
                "name": analyzer.get("analyzerName") or str(analyzer_id),
                "measures": [
                    {"column": m["columnName"], "name": m["name"], "unit": m.get("unit") or ""}
                    for m in measures
                ],
            }
        )
    return sorted(result, key=lambda a: a["name"])


class AlarmTypesRequest(BaseModel):
    analyzer_ids: list[int]


@app.post("/api/alarm-types")
async def api_alarm_types(req: AlarmTypesRequest, session: sessions.Session = Depends(current_session)):
    """Alarm definitions of the selected objects' analyzers, grouped by severity (most severe first)."""
    catalog = await mp_client.alarm_catalog(session.token)
    groups = await mp_client.alarm_groups(session.token)
    wanted = set(req.analyzer_ids)
    by_group: dict[str, list[dict]] = {}
    for a in catalog.values():
        if a.get("analyzerID") in wanted:
            by_group.setdefault(a.get("alarmGroupName") or "", []).append(
                {"id": a["alarmID"], "name": a.get("alarmName") or "", "priority": a.get("priority") or 0}
            )
    order = {g["name"]: -g.get("beginPriority", 0) for g in groups}
    return [
        {"name": name, "alarms": sorted(items, key=lambda x: (-x["priority"], x["name"]))}
        for name, items in sorted(by_group.items(), key=lambda kv: (order.get(kv[0], 0), kv[0]))
    ]


# --- Report generation ------------------------------------------------------

class ReportRequest(BaseModel):
    type: Literal["alarms", "measures"]
    node_id: int
    object_ids: list[str] = Field(min_length=1)
    alarm_ids: list[int] | None = None  # None = every alarm
    measures: dict[int, list[str]] = {}  # analyzerID -> measure column names
    period_from: datetime  # naive, in the user's MultiProbe time zone
    period_to: datetime
    with_chart: bool = False


def _user_tz(session: sessions.Session) -> ZoneInfo:
    try:
        return ZoneInfo(session.time_zone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _to_utc(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(timezone.utc)


@app.post("/api/report")
async def api_report_start(req: ReportRequest, session: sessions.Session = Depends(current_session)):
    tz = _user_tz(session)
    begin, end = _to_utc(req.period_from, tz), _to_utc(req.period_to, tz)
    if end <= begin:
        raise HTTPException(422, i18n.translator("en")("err_period"))
    if req.type == "measures":
        raise HTTPException(501, "Measurement reports arrive in the next stage.")
    if req.alarm_ids is not None and not req.alarm_ids:
        raise HTTPException(422, "No alarms selected")

    params = {
        **req.model_dump(mode="json"),
        "period_utc": [begin.isoformat(), end.isoformat()],
        "time_zone": str(tz),
        "user": session.user_name,
    }
    token = session.token

    async def work(job: jobs.Job):
        nodes = await mp_client.nodes(token)
        job.params["server"] = next((n["name"] for n in nodes if n["nodeID"] == req.node_id), str(req.node_id))
        return await alarm_report.build(job, token, str(tz))

    job = jobs.start(session.user_id, params, work)
    return {"id": job.id}


def _job(job_id: str, session: sessions.Session) -> jobs.Job:
    job = jobs.get(job_id, session.user_id)
    if job is None:
        raise HTTPException(404, "Report not found or expired")
    return job


def _done(job: jobs.Job) -> alarm_report.AlarmReport:
    if job.state != "done":
        raise HTTPException(409, "Report is not ready")
    return job.result


@app.get("/api/report/{job_id}")
async def api_report_status(job_id: str, session: sessions.Session = Depends(current_session)):
    return _job(job_id, session).progress()


_SORT_KEYS = {
    "detected": lambda r: r.detected,
    "finished": lambda r: (r.finished is not None, r.finished or r.detected),
    "duration": lambda r: r.duration_sec,
    "priority": lambda r: r.priority,
    "alarm": lambda r: r.alarm.lower(),
    "object": lambda r: r.object.lower(),
    "stream": lambda r: r.stream.lower(),
}


@app.get("/api/report/{job_id}/rows")
async def api_report_rows(
    job_id: str,
    request: Request,
    page: int = 1,
    size: int = 100,
    sort: str = "detected",
    desc: bool = True,
    q: str = "",
    group: str = "",
    status: str = "",
    session: sessions.Session = Depends(current_session),
):
    report = _done(_job(job_id, session))
    t = i18n.translator(i18n.pick_lang(request))
    rows = report.rows
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in f"{r.alarm}\n{r.object}\n{r.stream}\n{r.condition}".lower()]
    if group:
        rows = [r for r in rows if r.group == group]
    if status in ("active", "finished"):
        rows = [r for r in rows if r.active == (status == "active")]
    rows = sorted(rows, key=_SORT_KEYS.get(sort, _SORT_KEYS["detected"]), reverse=desc)
    size = max(10, min(size, 500))
    start = (max(page, 1) - 1) * size
    return {
        "total": len(rows),
        "page": max(page, 1),
        "size": size,
        "rows": [
            {
                "group": r.group,
                "priority": r.priority,
                "alarm": r.alarm,
                "condition": r.condition,
                "stream": r.stream,
                "object": r.object,
                "node": r.node,
                "detected": export.fmt_dt(r.detected),
                "finished": export.fmt_dt(r.finished),
                "duration": export.fmt_duration(r.duration_sec),
                "active": r.active,
                "status": export.status_text(r, t),
            }
            for r in rows[start : start + size]
        ],
    }


@app.get("/report/{job_id}", response_class=HTMLResponse)
async def report_page(job_id: str, request: Request, session: sessions.Session = Depends(current_session)):
    job = _job(job_id, session)
    lang = i18n.pick_lang(request)
    tz = _user_tz(session)
    period = [datetime.fromisoformat(v).astimezone(tz) for v in job.params["period_utc"]]
    return render(
        request,
        "report.html",
        session=session,
        job=job,
        report=job.result if job.state == "done" else None,
        period=[export.fmt_dt(p) for p in period],
        fmt_duration=export.fmt_duration,
        generated=export.fmt_dt(job.result.generated.astimezone(tz)) if job.state == "done" else "",
        strings=i18n.bundle(lang),
    )


@app.get("/report/{job_id}/export.{fmt}")
async def report_export(job_id: str, fmt: str, request: Request, session: sessions.Session = Depends(current_session)):
    job = _job(job_id, session)
    report = _done(job)
    tz = _user_tz(session)
    stamp = report.generated.astimezone(tz).strftime("%Y%m%d_%H%M")
    name = f"AlarmHistory_{stamp}"
    if fmt == "csv":
        t = i18n.translator(i18n.pick_lang(request))
        body, media = export.alarms_csv(report, t, settings.csv_delimiter), "text/csv; charset=utf-8"
    elif fmt == "xml":
        meta = {
            "tz": tz,
            "time_zone": str(tz),
            "user": job.params.get("user", ""),
            "server": job.params.get("server", ""),
            "period": [datetime.fromisoformat(v).astimezone(tz) for v in job.params["period_utc"]],
        }
        body, media = export.alarms_xml(report, meta), "application/xml"
    else:
        raise HTTPException(404, "Unknown format")
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})
