"""MultiProbe report add-on: web entry point."""
from contextlib import asynccontextmanager
from functools import partial
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import i18n, jobs, mp_client, sessions, storage
from .config import ROOT, settings
from .reports import alarms as alarm_report
from .reports import measures as measure_report
from .reports import export, pdf, timeline


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init()
    yield
    await mp_client.close()


app = FastAPI(title="MultiProbe Reports", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")
STATIC_DIR = ROOT / "app" / "static"


def _static_version() -> str:
    """Appended to CSS/JS URLs so browsers fetch fresh files whenever one of them changes."""
    return str(max(int(f.stat().st_mtime) for f in STATIC_DIR.iterdir() if f.is_file()))


templates.env.globals["static_version"] = _static_version


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
                    if not str(m.get("dataType") or "").startswith("xml")
                ],
            }
        )
    return sorted(result, key=lambda a: a["name"])


class AlarmTypesRequest(BaseModel):
    analyzer_ids: list[int]


@app.post("/api/alarm-types")
async def api_alarm_types(req: AlarmTypesRequest, session: sessions.Session = Depends(current_session)):
    """Alarm definitions of the selected objects' analyzers, grouped by severity (most severe first).
    Every MultiProbe priority group is listed, even when none of its alarms apply to the objects."""
    catalog = await mp_client.alarm_catalog(session.token)
    groups = await mp_client.alarm_groups(session.token)
    wanted = set(req.analyzer_ids)
    by_group: dict[str, list[dict]] = {g["name"]: [] for g in groups}
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
    """Everything the wizard collects. A report template stores exactly this."""
    type: Literal["alarms", "measures"]
    node_id: int
    object_ids: list[str] = Field(min_length=1)
    alarm_exclude: list[int] = []  # alarm types the user un-ticked
    measures: dict[int, list[str]] = {}  # analyzerID -> measure column names
    period_hours: int | None = Field(None, gt=0)  # "last N hours", resolved when the report runs
    period_from: datetime | None = None  # fixed period, naive, in the user's MultiProbe time zone
    period_to: datetime | None = None
    with_chart: bool = False
    resolution: Literal["auto", "1m", "5m", "15m", "30m", "1h", "3h", "6h", "1d", "raw"] = "auto"
    # Report sections shown on the page and in the PDF; None = all (CSV/XML always hold all data)
    sections: list[Literal["summary", "timeline", "availability", "top", "list", "charts", "values"]] | None = None


ALARM_SECTIONS = ["summary", "timeline", "availability", "top", "list"]
MEASURE_SECTIONS = ["charts", "summary", "values"]


def report_sections(params: dict) -> set[str]:
    sections = params.get("sections")
    if sections is None:  # templates saved before sections existed
        if params.get("type") == "measures":
            return {"summary", "values"} | ({"charts"} if params.get("with_chart") else set())
        return set(ALARM_SECTIONS)
    return set(sections)


def _user_tz(session: sessions.Session) -> ZoneInfo:
    try:
        return ZoneInfo(session.time_zone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _to_utc(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(timezone.utc)


def _period(req: ReportRequest, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if req.period_hours:
        end = datetime.now(timezone.utc).replace(microsecond=0)
        return end - timedelta(hours=req.period_hours), end
    if not req.period_from or not req.period_to:
        raise HTTPException(422, "Period is not set")
    begin, end = _to_utc(req.period_from, tz), _to_utc(req.period_to, tz)
    if end <= begin:
        raise HTTPException(422, i18n.translator("en")("err_period"))
    return begin, end


def _start_report(req: ReportRequest, session: sessions.Session, template_name: str = "") -> jobs.Job:
    tz = _user_tz(session)
    begin, end = _period(req, tz)
    if req.sections is not None:
        allowed = MEASURE_SECTIONS if req.type == "measures" else ALARM_SECTIONS
        req.sections = [s for s in req.sections if s in allowed]
        if not req.sections:
            raise HTTPException(422, "No report sections selected")
        req.with_chart = req.type == "measures" and "charts" in req.sections
    if req.type == "measures":
        if not any(req.measures.values()):
            raise HTTPException(422, "No measures selected")
        if req.resolution == "raw" and end - begin > measure_report.RAW_MAX_PERIOD:
            raise HTTPException(422, i18n.translator("en")("err_raw_too_long"))
    params = {
        **req.model_dump(mode="json"),
        "period_utc": [begin.isoformat(), end.isoformat()],
        "time_zone": str(tz),
        "user": session.user_name,
        "template": template_name,
    }
    token = session.token

    async def work(job: jobs.Job):
        nodes = await mp_client.nodes(token)
        job.params["server"] = next((n["name"] for n in nodes if n["nodeID"] == req.node_id), str(req.node_id))
        if req.type == "measures":
            return await measure_report.build(job, token, str(tz))
        return await alarm_report.build(job, token, str(tz))

    return jobs.start(session.user_id, params, work)


@app.post("/api/report")
async def api_report_start(req: ReportRequest, session: sessions.Session = Depends(current_session)):
    return {"id": _start_report(req, session).id}


# --- Report templates -------------------------------------------------------

class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    shared: bool = False
    params: ReportRequest | None = None  # omitted = keep the stored report settings (rename/share only)


def _template_out(tpl: dict, session: sessions.Session) -> dict:
    p = tpl["params"]
    return {
        "id": tpl["id"],
        "name": tpl["name"],
        "shared": tpl["shared"],
        "owner": tpl["owner_name"],
        "mine": tpl["owner_id"] == session.user_id,
        "updated": tpl["updated"],
        "params": p,
        "type": p.get("type"),
        "objects": len(p.get("object_ids") or []),
        "period_hours": p.get("period_hours"),
    }


def _template(template_id: int, session: sessions.Session) -> dict:
    tpl = storage.get_visible(template_id, session.user_id)
    if tpl is None:
        raise HTTPException(404, "Template not found")
    return tpl


@app.get("/api/templates")
async def api_templates(session: sessions.Session = Depends(current_session)):
    return [_template_out(t, session) for t in storage.list_visible(session.user_id)]


@app.get("/api/templates/{template_id}")
async def api_template(template_id: int, session: sessions.Session = Depends(current_session)):
    return _template_out(_template(template_id, session), session)


@app.post("/api/templates")
async def api_template_create(body: TemplateIn, session: sessions.Session = Depends(current_session)):
    if body.params is None:
        raise HTTPException(422, "Report settings are missing")
    new_id = storage.create(
        body.name.strip(), session.user_id, session.user_name, body.shared, body.params.model_dump(mode="json")
    )
    return {"id": new_id}


@app.put("/api/templates/{template_id}")
async def api_template_update(template_id: int, body: TemplateIn, session: sessions.Session = Depends(current_session)):
    params = body.params.model_dump(mode="json") if body.params else None
    if not storage.update(template_id, session.user_id, body.name.strip(), body.shared, params):
        raise HTTPException(403, "Only the owner can change this template")
    return {"id": template_id}


class CopyIn(BaseModel):
    name: str | None = Field(None, max_length=200)


@app.post("/api/templates/{template_id}/copy")
async def api_template_copy(
    template_id: int, request: Request, body: CopyIn | None = None,
    session: sessions.Session = Depends(current_session),
):
    """Copies always belong to the user who copies; they start private."""
    tpl = _template(template_id, session)
    t = i18n.translator(i18n.pick_lang(request))
    name = (body.name or "").strip() if body else ""
    new_id = storage.create(
        name or t("copy_of", name=tpl["name"]), session.user_id, session.user_name, False, tpl["params"]
    )
    return {"id": new_id}


@app.delete("/api/templates/{template_id}")
async def api_template_delete(template_id: int, session: sessions.Session = Depends(current_session)):
    if not storage.delete(template_id, session.user_id):
        raise HTTPException(403, "Only the owner can delete this template")
    return {"ok": True}


@app.post("/api/templates/{template_id}/run")
async def api_template_run(template_id: int, session: sessions.Session = Depends(current_session)):
    tpl = _template(template_id, session)
    req = ReportRequest.model_validate(tpl["params"])
    return {"id": _start_report(req, session, tpl["name"]).id}


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "templates.html", session=session, strings=i18n.bundle(lang))


def _job(job_id: str, session: sessions.Session) -> jobs.Job:
    job = jobs.get(job_id, session.user_id)
    if job is None:
        raise HTTPException(404, "Report not found or expired")
    return job


def _done(job: jobs.Job):
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
    done = job.state == "done"
    report = job.result if done else None
    measures = job.params.get("type") == "measures"
    context = dict(
        session=session,
        job=job,
        report=report,
        period=[export.fmt_dt(p) for p in period],
        fmt_duration=export.fmt_duration,
        fmt_num=export.fmt_num,
        generated=export.fmt_dt(report.generated.astimezone(tz)) if done else "",
        strings=i18n.bundle(lang),
        sections=report_sections(job.params),
    )
    if measures:
        return render(request, "report_measures.html", **context)
    ticks, timeline_rows = [], []
    if done:
        # The timeline ends at generation time when the period reaches into the future.
        tl_end = min(report.period[1], report.generated.astimezone(tz))
        ticks, timeline_rows = timeline.build(report.availability, report.period[0], tl_end, i18n.translator(lang))
    return render(request, "report.html", ticks=ticks, timeline=timeline_rows, **context)


def _wall_ms(value: datetime) -> int:
    """Wall-clock time in the user's zone as epoch ms, for a chart axis running in UTC mode:
    the browser then shows MultiProbe-zone times whatever its own time zone is."""
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)


MAX_SERIES_PER_CHART = 8  # categorical palette size; more objects are listed in the table only


@app.get("/api/report/{job_id}/charts")
async def api_report_charts(job_id: str, session: sessions.Session = Depends(current_session)):
    """One chart per measure, one line (bucket average) per object; min–max band for a single object."""
    report = _done(_job(job_id, session))
    charts = []
    for key, title, unit in report.measures():
        series = [s for s in report.series if s.key == key]
        charts.append({
            "key": key,
            "title": title,
            "unit": unit,
            "analyzer": series[0].analyzer if series else "",
            "total": len(series),
            "series": [
                {
                    "name": s.object,
                    "stream": s.stream,
                    "avg": [[_wall_ms(p.time), s.d(p.avg)] for p in s.points],
                    "min": [[_wall_ms(p.time), s.d(p.min)] for p in s.points],
                    "max": [[_wall_ms(p.time), s.d(p.max)] for p in s.points],
                }
                for s in series[:MAX_SERIES_PER_CHART]
            ],
        })
    return {
        "raw": report.resolution == "raw",
        "step_ms": int(report.step.total_seconds() * 1000) if report.step else None,
        "from": _wall_ms(report.period[0]),
        "to": _wall_ms(report.period[1]),
        "charts": charts,
    }


@app.get("/api/report/{job_id}/points")
async def api_report_points(
    job_id: str, page: int = 1, size: int = 100, measure: str = "", q: str = "",
    session: sessions.Session = Depends(current_session),
):
    """Measurement values as a paged table (newest first)."""
    report = _done(_job(job_id, session))
    needle = q.lower()
    rows = [
        (s, p)
        for s in report.series
        if (not measure or s.key == measure) and (not needle or needle in f"{s.object}\n{s.stream}".lower())
        for p in s.points
        if p.avg is not None
    ]
    rows.sort(key=lambda sp: sp[1].time, reverse=True)
    size = max(10, min(size, 500))
    start = (max(page, 1) - 1) * size
    return {
        "total": len(rows),
        "page": max(page, 1),
        "size": size,
        "rows": [
            {
                "time": export.fmt_dt(p.time),
                "stream": s.stream,
                "object": s.object,
                "measure": s.measure,
                "unit": s.display_unit,
                "min": export.fmt_num(s.d(p.min), False, 3),
                "avg": export.fmt_num(s.d(p.avg), False, 3),
                "max": export.fmt_num(s.d(p.max), False, 3),
            }
            for s, p in rows[start : start + size]
        ],
    }


def _download(body: bytes, media: str, name: str) -> Response:
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/report/{job_id}/export-availability.csv")
async def report_export_availability(job_id: str, request: Request, session: sessions.Session = Depends(current_session)):
    report = _done(_job(job_id, session))
    t = i18n.translator(i18n.pick_lang(request))
    stamp = report.generated.astimezone(_user_tz(session)).strftime("%Y%m%d_%H%M")
    return _download(export.availability_csv(report, t, settings.csv_delimiter),
                     "text/csv; charset=utf-8", f"ServiceAvailability_{stamp}.csv")


@app.get("/report/{job_id}/export-summary.csv")
async def report_export_summary(job_id: str, request: Request, session: sessions.Session = Depends(current_session)):
    report = _done(_job(job_id, session))
    t = i18n.translator(i18n.pick_lang(request))
    stamp = report.generated.astimezone(_user_tz(session)).strftime("%Y%m%d_%H%M")
    return _download(export.measures_summary_csv(report, t, settings.csv_delimiter),
                     "text/csv; charset=utf-8", f"MeasurementSummary_{stamp}.csv")


@app.get("/report/{job_id}/export.{fmt}")
async def report_export(job_id: str, fmt: str, request: Request, session: sessions.Session = Depends(current_session)):
    job = _job(job_id, session)
    report = _done(job)
    tz = _user_tz(session)
    stamp = report.generated.astimezone(tz).strftime("%Y%m%d_%H%M")
    measures = job.params.get("type") == "measures"
    name = f"{'Measurements' if measures else 'AlarmHistory'}_{stamp}.{fmt}"
    meta = {
        "tz": tz,
        "time_zone": str(tz),
        "user": job.params.get("user", ""),
        "server": job.params.get("server", ""),
        "period": [datetime.fromisoformat(v).astimezone(tz) for v in job.params["period_utc"]],
        "outage_min": settings.outage_min_priority,
        "degraded_min": settings.degraded_min_priority,
    }
    t = i18n.translator(i18n.pick_lang(request))
    if fmt == "csv":
        body = (export.measures_csv if measures else export.alarms_csv)(report, t, settings.csv_delimiter)
        return _download(body, "text/csv; charset=utf-8", name)
    if fmt == "xml":
        body = (export.measures_xml if measures else export.alarms_xml)(report, meta)
        return _download(body, "application/xml", name)
    if fmt == "pdf":
        period = [export.fmt_dt(p) for p in meta["period"]]
        generated = export.fmt_dt(report.generated.astimezone(tz))
        if measures:
            work = partial(pdf.measures_pdf, job, report, t, period, generated, MAX_SERIES_PER_CHART,
                           report_sections(job.params))
        else:
            work = partial(pdf.alarms_pdf, job, report, t, period, generated, settings.pdf_max_rows,
                           report_sections(job.params))
        body = await run_in_threadpool(work)  # a long alarm list takes a few seconds to lay out
        return _download(body, "application/pdf", name)
    raise HTTPException(404, "Unknown format")
