"""MultiProbe Toolkit: web entry point."""
import asyncio
from contextlib import asynccontextmanager
from functools import partial
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import auth, i18n, jobs, mp_client, overview, recordings, scheduler, server_info, sessions, storage
from .config import ROOT, settings
from .reports import alarms as alarm_report
from .reports import measures as measure_report
from .reports import scte as scte_report
from .reports import export, pdf, timeline


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init()
    worker = asyncio.create_task(scheduler.loop())
    yield
    worker.cancel()
    await recordings.close()
    await mp_client.close()


app = FastAPI(title="MultiProbe Toolkit", lifespan=lifespan, docs_url=None, redoc_url=None)
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
    if isinstance(exc, mp_client.MPAuthError) and exc.server_id is not None:
        # The server no longer accepts the token: disconnect just that one, the Toolkit session stays.
        session = sessions.get(request.cookies.get(sessions.COOKIE_NAME))
        if session:
            session.conns.pop(exc.server_id, None)
        return await _not_connected(request, sessions.NotConnected(exc.server_id))
    return JSONResponse({"error": exc.code, "message": exc.message}, status_code=502)


@app.exception_handler(sessions.NotConnected)
async def _not_connected(request: Request, exc: sessions.NotConnected):
    t = i18n.translator(i18n.pick_lang(request))
    if exc.server_id is None:
        message = t("err_no_servers")
    else:
        server = storage.get_server(exc.server_id) or {}
        message = t("err_not_connected", name=server.get("name") or f"#{exc.server_id}")
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "not_connected", "server_id": exc.server_id, "message": message}, status_code=409)
    return RedirectResponse("/servers", status_code=303)


def current_session(request: Request) -> sessions.Session:
    session = sessions.get(request.cookies.get(sessions.COOKIE_NAME))
    if session is None:
        raise SessionExpired()
    return session


def admin_session(session: sessions.Session = Depends(current_session)) -> sessions.Session:
    if not session.is_admin:
        raise HTTPException(403, "Only an administrator can do this")
    return session


def render(request: Request, name: str, **context) -> HTMLResponse:
    lang = i18n.pick_lang(request)
    return templates.TemplateResponse(
        request, name, {"lang": lang, "t": i18n.translator(lang), **context}
    )


# --- Pages ------------------------------------------------------------------

async def _signed_in(user: dict) -> RedirectResponse:
    """Start the session and connect to the servers whose password the user asked to remember. With a
    connection the work starts on the overview, without one on the servers page."""
    storage.update_user(user["id"], last_login=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sid = sessions.create(user)
    session = sessions.get(sid)
    await _auto_connect(session)
    response = RedirectResponse("/" if session.conns else "/servers", status_code=303)
    response.set_cookie(sessions.COOKIE_NAME, sid, httponly=True, samesite="lax")
    return response


def _is_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if storage.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    return render(request, "login.html", error=None, login_name="")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    t = i18n.translator(i18n.pick_lang(request))
    wait = auth.locked_for(login)
    if wait:
        return render(request, "login.html", error=t("err_locked", min=(wait + 59) // 60), login_name=login)
    user = storage.get_user_by_login(login)
    if not auth.check(login, user, password):
        wait = auth.locked_for(login)
        error = t("err_locked", min=(wait + 59) // 60) if wait else t("err_wrong_credentials")
        return render(request, "login.html", error=error, login_name=login)
    if user["disabled"]:
        return render(request, "login.html", error=t("err_user_disabled"), login_name=login)
    return await _signed_in(user)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """First run: create the administrator. Only while there are no users, and only from this computer."""
    if storage.count_users():
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", error=None, local=_is_local(request), login_name="", admin_name="")


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(request: Request, login: str = Form(...), name: str = Form(""), password: str = Form(...),
                       password2: str = Form(...)):
    if storage.count_users():
        return RedirectResponse("/login", status_code=303)
    t = i18n.translator(i18n.pick_lang(request))
    if not _is_local(request):
        raise HTTPException(403, "The administrator can only be created on the computer running the Toolkit")
    error = _password_problem(password, password2, t) or (None if login.strip() else t("err_login_required"))
    if error:
        return render(request, "setup.html", error=error, local=True, login_name=login, admin_name=name)
    uid = storage.create_user(login, name or login, auth.hash_password(password), "admin")
    user = storage.get_user(uid)
    storage.claim_unowned(uid, user["name"])  # templates and servers from before Toolkit had users
    return await _signed_in(user)


def _password_problem(password: str, password2: str, t) -> str | None:
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        return t("err_password_short", n=auth.MIN_PASSWORD_LENGTH)
    if password != password2:
        return t("err_password_mismatch")
    return None


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


@app.get("/reports", response_class=HTMLResponse)
async def wizard(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "wizard.html", session=session, strings=i18n.bundle(lang))


# --- Wizard data ------------------------------------------------------------

@app.get("/api/nodes")
async def api_nodes(session: sessions.Session = Depends(current_session)):
    """Probe nodes of every connected server (the wizard groups them by server). A server that fails is
    reported, not fatal."""
    conns = sorted(session.conns.values(), key=lambda c: c.name.lower())
    results = await asyncio.gather(*(mp_client.nodes(c) for c in conns), return_exceptions=True)
    out, errors = [], []
    for c, res in zip(conns, results):
        if isinstance(res, Exception):
            errors.append({"server_id": c.server_id, "server": c.name, "message": str(res)})
            continue
        for n in sorted(res, key=lambda n: n["name"]):
            out.append({"server_id": c.server_id, "server": c.name, "id": n["nodeID"], "name": n["name"]})
    return {"nodes": out, "errors": errors, "multi_server": len(conns) > 1,
            "connected": bool(session.conns)}


def _is_reportable(obj: dict) -> bool:
    return overview.reportable(obj)


@app.get("/api/objects")
async def api_objects(node_id: int, server_id: int = 0, session: sessions.Session = Depends(current_session)):
    """Streams and the objects inside them (services, PIDs), without hidden ones."""
    items = await mp_client.monitoring_objects(session.conn(server_id), node_id)
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


def _catalog_conn(session: sessions.Session) -> mp_client.Conn:
    """Analyzer, measure and alarm catalogues come from a connected server (the picked one first);
    MultiProbe installations of one version share them."""
    conns = session.scoped()
    if not conns:
        raise sessions.NotConnected(None)
    return conns[0]


class MeasuresRequest(BaseModel):
    analyzer_ids: list[int]


@app.post("/api/measures")
async def api_measures(req: MeasuresRequest, session: sessions.Session = Depends(current_session)):
    """Measures available for the analyzers of the selected objects, grouped by analyzer."""
    catalog = await mp_client.measure_catalog(_catalog_conn(session))
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
    catalog = await mp_client.alarm_catalog(_catalog_conn(session))
    groups = await mp_client.alarm_groups(_catalog_conn(session))
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

class Target(BaseModel):
    """Objects of one probe node on one MultiProbe server."""
    server_id: int
    node_id: int
    object_ids: list[str] = Field(min_length=1)


class ReportRequest(BaseModel):
    """Everything the wizard collects. A report template stores exactly this."""
    type: Literal["alarms", "measures", "scte"]
    targets: list[Target] | None = None
    # Templates saved before several servers were supported: one node of the main server.
    node_id: int | None = None
    object_ids: list[str] | None = None
    alarm_exclude: list[int] = []  # alarm types the user un-ticked
    measures: dict[int, list[str]] = {}  # analyzerID -> measure column names
    period_hours: int | None = Field(None, gt=0)  # "last N hours", resolved when the report runs
    period_from: datetime | None = None  # fixed period, naive, in the user's MultiProbe time zone
    period_to: datetime | None = None
    with_chart: bool = False
    resolution: Literal["auto", "1m", "5m", "15m", "30m", "1h", "3h", "6h", "1d", "raw"] = "auto"
    # Report sections shown on the page and in the PDF; None = all (CSV/XML always hold all data)
    sections: list[Literal["summary", "timeline", "availability", "top", "list", "charts", "values", "missing"]] | None = None
    scte_include_null: bool = False  # SCTE-35: also list splice_null (heartbeat) messages


ALARM_SECTIONS = ["summary", "timeline", "availability", "top", "list"]
MEASURE_SECTIONS = ["charts", "summary", "values"]
SCTE_SECTIONS = ["summary", "timeline", "missing", "list"]
SECTIONS = {"alarms": ALARM_SECTIONS, "measures": MEASURE_SECTIONS, "scte": SCTE_SECTIONS}


def report_sections(params: dict) -> set[str]:
    sections = params.get("sections")
    if sections is None:  # templates saved before sections existed
        if params.get("type") == "measures":
            return {"summary", "values"} | ({"charts"} if params.get("with_chart") else set())
        return set(SECTIONS.get(params.get("type"), ALARM_SECTIONS))
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


def _targets(req: ReportRequest) -> list[Target]:
    if req.targets:
        return req.targets
    default = storage.default_server_id()
    if req.node_id is not None and req.object_ids and default is not None:
        return [Target(server_id=default, node_id=req.node_id, object_ids=req.object_ids)]
    raise HTTPException(422, "No objects selected")


def _start_report(req: ReportRequest, session: sessions.Session, template_name: str = "") -> jobs.Job:
    tz = _user_tz(session)
    begin, end = _period(req, tz)
    targets = _targets(req)
    conns = {t.server_id: session.conn(t.server_id) for t in targets}  # NotConnected -> "connect first"
    if req.sections is not None:
        allowed = SECTIONS[req.type]
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
        **req.model_dump(mode="json", exclude={"node_id", "object_ids"}),
        "targets": [{**t.model_dump(), "server": conns[t.server_id].name} for t in targets],
        "object_count": sum(len(t.object_ids) for t in targets),
        "period_utc": [begin.isoformat(), end.isoformat()],
        "time_zone": str(tz),
        "user": session.user_name,
        "template": template_name,
    }

    async def work(job: jobs.Job):
        # Node names for the report header: "Main · Office", "Site 2 · Probe A"
        names = {}
        for sid, conn in conns.items():
            names |= {(sid, n["nodeID"]): n["name"] for n in await mp_client.nodes(conn)}
        for t in job.params["targets"]:
            t["node"] = names.get((t["server_id"], t["node_id"]), str(t["node_id"]))
        multi_server = len(conns) > 1
        job.params["server"] = ", ".join(dict.fromkeys(
            f'{t["server"]} · {t["node"]}' if multi_server else t["node"] for t in job.params["targets"]))
        builder = {"measures": measure_report.build, "scte": scte_report.build}.get(req.type, alarm_report.build)
        return await builder(job, conns, str(tz))

    return jobs.start(session.user_id, params, work)


@app.post("/api/report")
async def api_report_start(req: ReportRequest, session: sessions.Session = Depends(current_session)):
    return {"id": _start_report(req, session).id}


class ObjectsOverride(BaseModel):
    """Run a template (or a quick report) for other objects, e.g. a channel clicked on the overview."""
    server_id: int
    node_id: int
    object_ids: list[str] = Field(min_length=1)
    expand: bool = True  # also take everything inside these objects, as ticking them in the wizard does


async def _expand(session: sessions.Session, o: ObjectsOverride) -> list[str]:
    if not o.expand:
        return o.object_ids
    objects = {x["monitoringObjectID"]: x for x in await mp_client.monitoring_objects(session.conn(o.server_id), o.node_id)}
    ids: list[str] = []
    for oid in o.object_ids:
        ids += [i for i in overview.subtree(oid, objects) if i not in ids]
    if not ids:
        raise HTTPException(404, "Objects not found")
    return ids


@app.post("/api/report/quick")
async def api_report_quick(body: ObjectsOverride, kind: Literal["alarms"] = "alarms", hours: int = 24,
                           session: sessions.Session = Depends(current_session)):
    """One-click alarm report for a channel: all sections, last `hours`."""
    req = ReportRequest(type=kind, targets=[Target(server_id=body.server_id, node_id=body.node_id,
                                                   object_ids=await _expand(session, body))],
                        period_hours=max(1, min(hours, 24 * 31)))
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
        "objects": sum(len(t.get("object_ids") or []) for t in p.get("targets") or [])
                   or len(p.get("object_ids") or []),
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
async def api_template_run(template_id: int, body: ObjectsOverride | None = None,
                           session: sessions.Session = Depends(current_session)):
    """Run a template as saved, or with its objects replaced (the template's settings, another channel)."""
    tpl = _template(template_id, session)
    params = dict(tpl["params"])
    name = tpl["name"]
    if body is not None:
        params.pop("node_id", None)
        params.pop("object_ids", None)
        params["targets"] = [{"server_id": body.server_id, "node_id": body.node_id,
                              "object_ids": await _expand(session, body)}]
    req = ReportRequest.model_validate(params)
    return {"id": _start_report(req, session, name).id}


# --- Overview -------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def overview_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "overview.html", session=session, strings=i18n.bundle(lang))


@app.get("/api/overview/state")
async def api_overview_state(session: sessions.Session = Depends(current_session)):
    return await overview.current_state(session.scoped(), _user_tz(session))


@app.get("/api/overview/servers")
async def api_overview_servers(session: sessions.Session = Depends(current_session)):
    """Load, objects and licences of the shown servers (MultiProbe's Administration -> Nodes)."""
    return await server_info.servers(session.scoped(), _user_tz(session))


@app.get("/api/overview/top")
async def api_overview_top(hours: int = 24, session: sessions.Session = Depends(current_session)):
    if hours not in (1, 24, 168):
        raise HTTPException(422, "hours must be 1, 24 or 168")
    return await overview.top_channels(session.scoped(), session.user_id, hours, str(_user_tz(session)))


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "templates.html", session=session, strings=i18n.bundle(lang))


# --- Recordings ---------------------------------------------------------------

@app.get("/recordings", response_class=HTMLResponse)
async def recordings_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "recordings.html", session=session, strings=i18n.bundle(lang),
                  max_minutes=settings.rec_max_minutes)


@app.get("/api/rec/tasks")
async def api_rec_tasks(session: sessions.Session = Depends(current_session)):
    """Recording tasks of the picked server (or of all connected servers)."""
    conns = session.scoped()
    results = await asyncio.gather(*(recordings.tasks(c) for c in conns), return_exceptions=True)
    out, errors = [], []
    for c, res in zip(conns, results):
        if isinstance(res, Exception):
            errors.append({"server_id": c.server_id, "server": c.name, "message": str(res)})
        else:
            out += res
    return {"tasks": out, "errors": errors, "multi_server": len(conns) > 1, "connected": bool(session.conns),
            "servers": [{"id": c.server_id, "name": c.name}
                        for c in sorted(session.conns.values(), key=lambda c: c.name.lower())],
            "scope": session.scope,
            "now": datetime.now(_user_tz(session)).strftime("%Y-%m-%dT%H:%M:%S")}


@app.get("/api/rec/timeline")
async def api_rec_timeline(server_id: int, task_id: str, start: datetime, seconds: int = Query(86400, ge=60, le=2 * 86400),
                           session: sessions.Session = Depends(current_session)):
    """Recording intervals in a window of the timeline: `start` naive in the user's time zone, `seconds` long.
    Positions are seconds from the start of the window."""
    begin = _to_utc(start, _user_tz(session))
    end = begin + timedelta(seconds=seconds)
    rows = await recordings.timeline(session.conn(server_id), task_id, begin, end)
    sec = lambda v: round((v - begin).total_seconds(), 1)
    now = datetime.now(timezone.utc)
    return {"intervals": [{"from": sec(r["begin"]), "to": sec(r["end"]), "status": r["status"], "media": r["media"],
                           "storage": r["storage"]} for r in rows],
            "now": sec(now) if begin <= now < end else None}


@app.get("/api/rec/services")
async def api_rec_services(server_id: int, node_id: int, object_id: str,
                           session: sessions.Session = Depends(current_session)):
    """Channels in a recording (one for a service, all of them for a whole stream) with their PIDs."""
    return await recordings.services(session.conn(server_id), node_id, object_id)


class RecPeriod(BaseModel):
    server_id: int
    task_id: str
    name: str = ""
    begin: datetime  # naive, in the user's time zone
    end: datetime


def _rec_period(req: RecPeriod, session: sessions.Session) -> tuple[datetime, datetime]:
    tz = _user_tz(session)
    begin, end = _to_utc(req.begin, tz), _to_utc(req.end, tz)
    t = i18n.translator("en")
    if end <= begin:
        raise HTTPException(422, t("err_period"))
    if end - begin > timedelta(minutes=settings.rec_max_minutes):
        raise HTTPException(422, t("rec_err_too_long", n=settings.rec_max_minutes))
    if begin >= datetime.now(timezone.utc):
        raise HTTPException(422, t("rec_err_future"))
    return begin, end


@app.post("/api/rec/play")
async def api_rec_play(req: RecPeriod, session: sessions.Session = Depends(current_session)):
    begin, end = _rec_period(req, session)
    piece = await recordings.create(session.user_id, session.conn(req.server_id), req.task_id, req.name, begin, end)
    return {"id": piece.id}


def _piece(piece_id: str, session: sessions.Session) -> recordings.Piece:
    piece = recordings.get(piece_id, session.user_id)
    if piece is None:
        raise HTTPException(404, "Playback not found or expired")
    return piece


@app.get("/api/rec/play/{piece_id}")
async def api_rec_play_status(piece_id: str, session: sessions.Session = Depends(current_session)):
    return await recordings.status(_piece(piece_id, session))


@app.get("/api/rec/hls/{piece_id}/{name}")
async def api_rec_hls(piece_id: str, name: str, session: sessions.Session = Depends(current_session)):
    status, body, kind = await recordings.fetch(_piece(piece_id, session), name)
    return Response(body, status_code=status, media_type=kind, headers={"Cache-Control": "no-store"})


@app.delete("/api/rec/play/{piece_id}")
async def api_rec_play_stop(piece_id: str, session: sessions.Session = Depends(current_session)):
    await recordings.drop(_piece(piece_id, session))
    return {"ok": True}


class RecExport(RecPeriod):
    format: Literal["mp4", "mp3"]
    pids: list[int] | None = None  # video and audio PIDs of the channel (a recording of a whole stream)
    audio_pid: int | None = None  # audio track for MP3


def _file_name(name: str, begin: datetime, end: datetime, tz: ZoneInfo, ext: str) -> str:
    b, e = begin.astimezone(tz), end.astimezone(tz)
    span = f"{b:%Y-%m-%d %H-%M-%S} - " + (f"{e:%H-%M-%S}" if b.date() == e.date() else f"{e:%Y-%m-%d %H-%M-%S}")
    clean = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip() or "recording"
    return f"{clean} {span}.{ext}"


@app.post("/api/rec/export")
async def api_rec_export(req: RecExport, session: sessions.Session = Depends(current_session)):
    begin, end = _rec_period(req, session)
    conn = session.conn(req.server_id)
    params = {"format": req.format, "phase": "cutting",
              "file_name": _file_name(req.name, begin, end, _user_tz(session), req.format)}

    async def work(job: jobs.Job):
        return await recordings.export(job, conn, req.task_id, begin, end, req.format, req.pids, req.audio_pid)

    return {"id": jobs.start(session.user_id, params, work).id}


@app.get("/api/rec/export/{job_id}")
async def api_rec_export_status(job_id: str, session: sessions.Session = Depends(current_session)):
    job = _job(job_id, session)
    return {**job.progress(), "phase": job.params.get("phase"), "file_name": job.params.get("file_name"),
            "size": (job.result or {}).get("size")}


@app.get("/rec/export/{job_id}/file")
async def rec_export_file(job_id: str, session: sessions.Session = Depends(current_session)):
    job = _job(job_id, session)
    path = recordings.export_path(job)
    if job.state != "done" or not path.exists():
        raise HTTPException(404, "File not found or expired")
    media = "video/mp4" if job.params["format"] == "mp4" else "audio/mpeg"
    return FileResponse(path, media_type=media, filename=job.params["file_name"])


# --- Scheduled exports ----------------------------------------------------------

@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "schedule.html", session=session, strings=i18n.bundle(lang))


def _local(value: str | None, tz: ZoneInfo) -> str | None:
    return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%d %H:%M") if value else None


def _run_out(run: dict, tz: ZoneInfo) -> dict:
    counts: dict[str, int] = {}
    for p in run["pieces"]:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    progress = None
    if scheduler.current and any(p["id"] == scheduler.current.get("piece_id") for p in run["pieces"]):
        progress = scheduler.current["job"].progress()["percent"]
    return {
        "id": run["id"], "state": run["state"], "begin": _local(run["period_begin"], tz),
        "end": _local(run["period_end"], tz), "created": _local(run["created"], tz),
        "finished": _local(run["finished"], tz), "counts": counts, "total": len(run["pieces"]), "progress": progress,
        "pieces": [{"id": p["id"], "begin": _local(p["begin"], tz), "end": _local(p["end"], tz), "format": p["format"],
                    "state": p["state"], "attempts": p["attempts"], "file": p["file"], "size": p["size"],
                    "error": p["error"]} for p in run["pieces"]],
    }


def _job_out(job: dict, tz: ZoneInfo, folders: dict[int, dict], with_runs: int) -> dict:
    p = job["params"]
    runs = storage.list_runs(job["id"], with_runs)
    return {
        "id": job["id"], "name": job["name"], "owner": job["owner_name"], "enabled": job["enabled"],
        "next_run": _local(job["next_run"], tz), "params": {
            k: p.get(k) for k in ("kind", "server_id", "server", "task_name", "channel", "formats", "chunk_minutes",
                                  "run_at", "time_zone")},
        "period": [_local(p.get("begin"), tz), _local(p.get("end"), tz)] if p["kind"] == "once" else None,
        "folder": (folders.get(p["folder_id"]) or {}).get("name"),
        "runs": [_run_out(r, tz) for r in runs],
    }


def _own_job(job_id: int, session: sessions.Session) -> dict:
    job = storage.get_job(job_id)
    if job is None or (job["owner_id"] != session.user_id and not session.is_admin):
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/schedule")
async def api_schedule(session: sessions.Session = Depends(current_session)):
    """The user's jobs (an administrator sees everybody's), the folders and which servers have a job account."""
    tz = _user_tz(session)
    folders = {f["id"]: f for f in storage.list_folders()}
    job_list = storage.list_jobs(None if session.is_admin else session.user_id)
    return {
        "jobs": [_job_out(j, tz, folders, 3) for j in job_list],
        "folders": [{"id": f["id"], "name": f["name"], **({"path": f["path"]} if session.is_admin else {})}
                    for f in folders.values()],
        "job_accounts": sorted(storage.credential_logins(storage.JOBS)),
        "is_admin": session.is_admin, "chunks": list(scheduler.CHUNKS),
        "now": datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S"), "time_zone": str(tz),
    }


@app.get("/api/schedule/jobs/{job_id}/runs")
async def api_schedule_runs(job_id: int, session: sessions.Session = Depends(current_session)):
    job = _own_job(job_id, session)
    return _job_out(job, _user_tz(session), {f["id"]: f for f in storage.list_folders()}, 20)["runs"]


class ScheduleJobIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    server_id: int
    task_id: str
    task_name: str = ""
    channel: str = ""  # the channel's name (a service of a whole-stream recording, or the recording itself)
    pids: list[int] | None = None
    audio_pid: int | None = None
    formats: list[Literal["mp4", "mp3"]] = Field(min_length=1)
    folder_id: int
    chunk_minutes: int
    kind: Literal["once", "daily"]
    begin: datetime | None = None  # once: the period, naive in the user's time zone
    end: datetime | None = None
    start_at: datetime | None = None  # once: when to start; None = right away
    run_at: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # daily: "HH:MM"


@app.post("/api/schedule/jobs")
async def api_schedule_create(body: ScheduleJobIn, request: Request,
                              session: sessions.Session = Depends(current_session)):
    t = i18n.translator(i18n.pick_lang(request))
    tz = _user_tz(session)
    srv = _server_or_404(body.server_id)
    if storage.get_folder(body.folder_id) is None:
        raise HTTPException(422, t("sch_err_folder"))
    if body.chunk_minutes not in scheduler.CHUNKS:
        raise HTTPException(422, "chunk_minutes is not one of the offered lengths")
    params = {
        **body.model_dump(exclude={"name", "begin", "end", "start_at"}),
        "formats": sorted(set(body.formats), key=["mp4", "mp3"].index),
        "server": srv["name"], "time_zone": str(tz),
    }
    now = datetime.now(timezone.utc)
    if body.kind == "once":
        if not body.begin or not body.end or body.end <= body.begin:
            raise HTTPException(422, t("err_period"))
        begin, end = _to_utc(body.begin, tz), _to_utc(body.end, tz)
        start = max(_to_utc(body.start_at, tz), now) if body.start_at else now
        if end > start:
            raise HTTPException(422, t("sch_err_future"))
        params |= {"begin": begin.isoformat(), "end": end.isoformat()}
        next_run = scheduler.utc_iso(start)
    else:
        if not body.run_at:
            raise HTTPException(422, t("sch_err_time"))
        next_run = scheduler.utc_iso(scheduler.next_daily(body.run_at, tz, now))
    job_id = storage.create_job(body.name.strip(), session.user_id, session.user_name, params, next_run)
    scheduler.wake()
    return {"id": job_id}


class ScheduleEnableIn(BaseModel):
    enabled: bool


@app.post("/api/schedule/jobs/{job_id}/enabled")
async def api_schedule_enable(job_id: int, body: ScheduleEnableIn, session: sessions.Session = Depends(current_session)):
    job = _own_job(job_id, session)
    fields: dict = {"enabled": body.enabled}
    p = job["params"]
    if body.enabled and p["kind"] == "daily":  # the next start counts from now, not from when it was paused
        fields["next_run"] = scheduler.utc_iso(scheduler.next_daily(p["run_at"], scheduler.zone(p["time_zone"]),
                                                                    datetime.now(timezone.utc)))
    storage.update_job(job_id, **fields)
    return {"ok": True}


@app.post("/api/schedule/jobs/{job_id}/run")
async def api_schedule_run_now(job_id: int, session: sessions.Session = Depends(current_session)):
    """Start the job now (a daily job exports the previous day), apart from its schedule."""
    job = _own_job(job_id, session)
    return {"run_id": scheduler.start_run(job)}


@app.delete("/api/schedule/jobs/{job_id}")
async def api_schedule_delete(job_id: int, session: sessions.Session = Depends(current_session)):
    _own_job(job_id, session)
    storage.delete_job(job_id)  # a piece being exported finishes; the files already saved stay
    return {"ok": True}


def _own_run(run_id: int, session: sessions.Session) -> dict:
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    _own_job(run["job_id"], session)
    return run


@app.post("/api/schedule/runs/{run_id}/cancel")
async def api_schedule_cancel(run_id: int, session: sessions.Session = Depends(current_session)):
    _own_run(run_id, session)
    storage.set_pieces_state(run_id, ("pending",), "cancelled")
    storage.finish_run_if_done(run_id)
    return {"ok": True}


@app.post("/api/schedule/runs/{run_id}/retry")
async def api_schedule_retry(run_id: int, session: sessions.Session = Depends(current_session)):
    _own_run(run_id, session)
    n = storage.retry_failed(run_id)
    scheduler.wake()
    return {"retried": n}


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=3, max_length=400)


@app.post("/api/schedule/folders")
async def api_folder_create(body: FolderIn, request: Request, session: sessions.Session = Depends(admin_session)):
    """A folder scheduled exports may write to. It must exist and accept a file (checked with a test file)."""
    from pathlib import Path
    t = i18n.translator(i18n.pick_lang(request))
    path = Path(body.path.strip())
    if not path.is_absolute() or not path.is_dir():
        raise HTTPException(422, t("sch_err_folder_missing"))
    probe = path / ".mp_toolkit_write_test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(422, t("sch_err_folder_write", msg=str(exc)))
    if any(f["path"].lower() == str(path).lower() for f in storage.list_folders()):
        raise HTTPException(409, t("sch_err_folder_dup"))
    return {"id": storage.create_folder(body.name.strip(), str(path))}


@app.delete("/api/schedule/folders/{folder_id}")
async def api_folder_delete(folder_id: int, session: sessions.Session = Depends(admin_session)):
    storage.delete_folder(folder_id)
    return {"ok": True}


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
    "object": lambda r: r.label.lower(),
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
        rows = [r for r in rows if needle in f"{r.alarm}\n{r.label}\n{r.stream}\n{r.condition}".lower()]
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
                "object": r.label,
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
    if job.params.get("type") == "scte":
        ticks, rows = (scte_timeline(report, lang) if done else ([], []))
        return render(request, "report_scte.html", ticks=ticks, timeline=rows, **context)
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
                    "name": s.label,
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
        if (not measure or s.key == measure) and (not needle or needle in f"{s.label}\n{s.stream}".lower())
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
                "object": s.label,
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
    kind = job.params.get("type") or "alarms"
    name = f"{ {'alarms': 'AlarmHistory', 'measures': 'Measurements', 'scte': 'SCTE35Markers'}[kind] }_{stamp}.{fmt}"
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
        csv_writer = {"alarms": export.alarms_csv, "measures": export.measures_csv, "scte": export.scte_csv}[kind]
        return _download(csv_writer(report, t, settings.csv_delimiter), "text/csv; charset=utf-8", name)
    if fmt == "xml":
        xml_writer = {"alarms": export.alarms_xml, "measures": export.measures_xml, "scte": export.scte_xml}[kind]
        return _download(xml_writer(report, meta), "application/xml", name)
    if fmt == "pdf":
        period = [export.fmt_dt(p) for p in meta["period"]]
        generated = export.fmt_dt(report.generated.astimezone(tz))
        sections = report_sections(job.params)
        if kind == "measures":
            work = partial(pdf.measures_pdf, job, report, t, period, generated, MAX_SERIES_PER_CHART, sections)
        elif kind == "scte":
            work = partial(pdf.scte_pdf, job, report, t, period, generated, settings.pdf_max_rows, sections,
                           scte_timeline(report, i18n.pick_lang(request)))
        else:
            work = partial(pdf.alarms_pdf, job, report, t, period, generated, settings.pdf_max_rows, sections)
        body = await run_in_threadpool(work)  # a long alarm list takes a few seconds to lay out
        return _download(body, "application/pdf", name)
    raise HTTPException(404, "Unknown format")


# --- SCTE-35 report ---------------------------------------------------------------------

def scte_timeline(report, lang: str):
    """Ad breaks per profile on a timeline: one context row per stream, then its profiles."""
    t = i18n.translator(lang)
    begin, end = report.period
    end = min(end, report.generated.astimezone(begin.tzinfo))
    total = max((end - begin).total_seconds(), 1)
    rows, last_stream = [], None
    for o in report.objects:
        if o["stream"] != last_stream:
            last_stream = o["stream"]
            rows.append(timeline.TimelineRow(SimpleNamespace(name=o["stream"] or "—", depth=0, kind_name=""), [], context=True))
        bars = []
        for b in (x for x in report.breaks if x.object_id == o["id"]):
            left = 100 * (b.begin - begin).total_seconds() / total
            width = max(100 * (b.end - b.begin).total_seconds() / total, timeline.MIN_WIDTH_PCT)
            tip = (f"{o['name']}\n{t('scte_break')}: {b.begin:%d.%m %H:%M:%S} – {b.end:%H:%M:%S} "
                   f"({export.fmt_duration(int((b.end - b.begin).total_seconds()))})"
                   + (f"\nEvent ID {b.event_id}" if b.event_id is not None else ""))
            bars.append(timeline.Bar(round(left, 3), round(min(width, 100 - left), 3), "break", tip))
        rows.append(timeline.TimelineRow(SimpleNamespace(name=o["name"], depth=1, kind_name=""), bars))
    return timeline._ticks(begin, end), rows


@app.get("/api/report/{job_id}/markers")
async def api_report_markers(
    job_id: str, request: Request, page: int = 1, size: int = 100, q: str = "", command: str = "", obj: str = "",
    session: sessions.Session = Depends(current_session),
):
    report = _done(_job(job_id, session))
    t = i18n.translator(i18n.pick_lang(request))
    rows = [
        m for m in reversed(report.markers)  # newest first
        if (not command or m.command == command) and (not obj or m.object_id == obj)
        and (not q or q.lower() in f"{m.object}\n{m.stream}\n{m.event_id}\n{m.segmentation}".lower())
    ]
    size = max(10, min(size, 500))
    start = (max(page, 1) - 1) * size
    return {
        "total": len(rows),
        "page": max(page, 1),
        "size": size,
        "rows": [export.scte_row(m, t) for m in rows[start : start + size]],
    }


# --- MultiProbe servers -----------------------------------------------------------------------

def _server_out(srv: dict, session: sessions.Session, kept: dict[int, str], jobs_kept: dict[int, str]) -> dict:
    conn = session.conns.get(srv["id"])
    out = {
        "id": srv["id"], "name": srv["name"], "url": srv["url"], "verify_tls": srv["verify_tls"],
        "connected": conn is not None, "login": session.logins.get(srv["id"]) if conn else None,
        "added_by": srv.get("created_by_name", ""),
        "remembered": kept.get(srv["id"]),  # login whose password is kept for this user
        "auto_error": session.auto_errors.get(srv["id"]),
    }
    if session.is_admin:
        out["job_login"] = jobs_kept.get(srv["id"])
    return out


@app.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request, session: sessions.Session = Depends(current_session)):
    lang = i18n.pick_lang(request)
    return render(request, "servers.html", session=session, strings=i18n.bundle(lang))


@app.get("/api/servers")
async def api_servers(session: sessions.Session = Depends(current_session)):
    kept, jobs_kept = storage.credential_logins(session.user_id), storage.credential_logins(storage.JOBS)
    return {"servers": [_server_out(s, session, kept, jobs_kept) for s in storage.list_servers()],
            "is_admin": session.is_admin}


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=300)
    verify_tls: bool = True


def _clean_url(url: str) -> str:
    """scheme://host:port only. The MultiProbe API lives at the root (/api/...), so a link to a page of
    the web interface (http://host:2020/conf/presets/137) is cut down to http://host:2020."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "The address must start with http:// or https://")
    return f"{parts.scheme.lower()}://{parts.netloc}"


@app.post("/api/servers")
async def api_server_create(body: ServerIn, session: sessions.Session = Depends(admin_session)):
    url = _clean_url(body.url)
    if any(s["url"] == url for s in storage.list_servers()):
        raise HTTPException(409, "This server is already in the list")
    return {"id": storage.create_server(body.name.strip(), url, body.verify_tls, session.user_id, session.user_name)}


@app.put("/api/servers/{server_id}")
async def api_server_update(server_id: int, body: ServerIn, session: sessions.Session = Depends(admin_session)):
    if not storage.update_server(server_id, body.name.strip(), _clean_url(body.url), body.verify_tls):
        raise HTTPException(404, "Server not found")
    session.conns.pop(server_id, None)  # the address may have changed: sign in again
    return {"id": server_id}


@app.delete("/api/servers/{server_id}")
async def api_server_delete(server_id: int, session: sessions.Session = Depends(admin_session)):
    if storage.get_server(server_id) is None:
        raise HTTPException(404, "Server not found")
    storage.delete_server(server_id)
    session.conns.pop(server_id, None)
    return {"ok": True}


class ConnectIn(BaseModel):
    login: str = Field(min_length=1)
    password: str
    remember: bool = False  # keep the password (encrypted) and connect by itself from now on


def _renewer(owner: str, srv: dict):
    """Signs in again with the kept password when MultiProbe stops accepting the token."""
    async def renew() -> str:
        cred = storage.get_credential(owner, srv["id"])
        if cred is None:
            raise mp_client.MPAuthError("noCredential", "No password is kept")
        token, _ = await mp_client.login(srv["url"], cred[0], cred[1], srv["verify_tls"])
        return token
    return renew


def _attach(session: sessions.Session, srv: dict, login: str, token: str, profile: dict) -> None:
    session.conns[srv["id"]] = mp_client.Conn(srv["id"], srv["name"], srv["url"], token, srv["verify_tls"],
                                              renew=_renewer(session.user_id, srv))
    session.logins[srv["id"]] = login
    session.auto_errors.pop(srv["id"], None)
    # No time zone chosen yet: take the one this MultiProbe account uses.
    mp_tz = (profile.get("timeZoneSettings") or {}).get("timeZone")
    if not session.user.get("time_zone") and mp_tz:
        storage.update_user(session.user["id"], time_zone=mp_tz)
        sessions.update_user(storage.get_user(session.user["id"]))


AUTO_CONNECT_TIMEOUT = 10  # seconds per server; a server that is away must not hold up the sign-in


async def _auto_connect(session: sessions.Session) -> None:
    servers = {s["id"]: s for s in storage.list_servers()}

    async def one(server_id: int) -> None:
        srv, cred = servers.get(server_id), storage.get_credential(session.user_id, server_id)
        if srv is None or cred is None:
            return
        try:
            token, profile = await asyncio.wait_for(
                mp_client.login(srv["url"], cred[0], cred[1], srv["verify_tls"]), AUTO_CONNECT_TIMEOUT)
            _attach(session, srv, cred[0], token, profile)
        except (mp_client.MPError, asyncio.TimeoutError) as exc:
            session.auto_errors[server_id] = getattr(exc, "message", "") or exc.__class__.__name__

    await asyncio.gather(*(one(sid) for sid in storage.credential_logins(session.user_id)))


async def _check_login(srv: dict, body: ConnectIn, t) -> tuple[str, dict]:
    try:
        return await mp_client.login(srv["url"], body.login, body.password, srv["verify_tls"])
    except mp_client.MPAuthError as exc:
        msg = {"authErrorUserDisabled": t("err_user_disabled"), "authErrorUserBanned": t("err_user_banned")}.get(
            exc.code, t("err_wrong_credentials"))
        # Not 401: the pages read 401 as "the Toolkit session expired" and go to the Toolkit sign-in.
        raise HTTPException(400, msg)
    except mp_client.MPError as exc:
        if exc.status in (404, 405) and "<html" in exc.message.lower():
            # A web server answered, not the MultiProbe API (e.g. nginx serving the web interface).
            raise HTTPException(502, t("err_not_api", url=srv["url"]))
        raise HTTPException(502, t("err_server_unreachable", msg=exc.message[:200]))


def _server_or_404(server_id: int) -> dict:
    srv = storage.get_server(server_id)
    if srv is None:
        raise HTTPException(404, "Server not found")
    return srv


@app.post("/api/servers/{server_id}/connect")
async def api_server_connect(server_id: int, body: ConnectIn, request: Request,
                             session: sessions.Session = Depends(current_session)):
    """Sign in to a MultiProbe server with an account of that server. The password is kept (encrypted)
    only when the user asks for it."""
    srv = _server_or_404(server_id)
    token, profile = await _check_login(srv, body, i18n.translator(i18n.pick_lang(request)))
    _attach(session, srv, body.login, token, profile)
    if body.remember:
        storage.save_credential(session.user_id, server_id, body.login, body.password)
    else:
        storage.delete_credential(session.user_id, server_id)
    return {"ok": True}


@app.post("/api/servers/{server_id}/forget")
async def api_server_forget(server_id: int, session: sessions.Session = Depends(current_session)):
    """Stop keeping this user's password for the server (the current connection stays)."""
    storage.delete_credential(session.user_id, server_id)
    return {"ok": True}


@app.put("/api/servers/{server_id}/job-account")
async def api_server_job_account(server_id: int, body: ConnectIn, request: Request,
                                 session: sessions.Session = Depends(admin_session)):
    """The MultiProbe account scheduled jobs use on this server. Checked by signing in before it is kept."""
    srv = _server_or_404(server_id)
    await _check_login(srv, body, i18n.translator(i18n.pick_lang(request)))
    storage.save_credential(storage.JOBS, server_id, body.login, body.password)
    return {"ok": True}


@app.delete("/api/servers/{server_id}/job-account")
async def api_server_job_account_delete(server_id: int, session: sessions.Session = Depends(admin_session)):
    storage.delete_credential(storage.JOBS, server_id)
    return {"ok": True}


class ScopeIn(BaseModel):
    server_id: int | None = None  # None = all connected servers


@app.post("/api/scope")
async def api_scope(body: ScopeIn, session: sessions.Session = Depends(current_session)):
    """The server switcher of the overview, recordings and jobs pages: which connected server they show."""
    if body.server_id is not None:
        session.conn(body.server_id)  # must be connected
    session.scope = body.server_id
    return {"scope": session.scope}


@app.post("/api/servers/{server_id}/disconnect")
async def api_server_disconnect(server_id: int, session: sessions.Session = Depends(current_session)):
    session.conns.pop(server_id, None)
    session.logins.pop(server_id, None)
    return {"ok": True}


# --- Toolkit users ------------------------------------------------------------------------------

def _user_out(u: dict) -> dict:
    return {k: u[k] for k in ("id", "login", "name", "role", "disabled", "time_zone", "created", "last_login")}


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, session: sessions.Session = Depends(admin_session)):
    return render(request, "users.html", session=session, strings=i18n.bundle(i18n.pick_lang(request)))


@app.get("/api/users")
async def api_users(session: sessions.Session = Depends(admin_session)):
    return {"users": [_user_out(u) for u in storage.list_users()], "me": session.user["id"]}


class UserIn(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    name: str = Field("", max_length=100)
    role: Literal["admin", "user"] = "user"
    password: str = ""


class UserUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "user"]
    disabled: bool = False
    password: str = ""  # set = reset the password


def _admins_left(excluding: int) -> int:
    return sum(1 for u in storage.list_users() if u["role"] == "admin" and not u["disabled"] and u["id"] != excluding)


@app.post("/api/users")
async def api_user_create(body: UserIn, request: Request, session: sessions.Session = Depends(admin_session)):
    t = i18n.translator(i18n.pick_lang(request))
    problem = _password_problem(body.password, body.password, t)
    if problem:
        raise HTTPException(422, problem)
    if storage.get_user_by_login(body.login):
        raise HTTPException(409, t("err_login_taken"))
    uid = storage.create_user(body.login, body.name or body.login, auth.hash_password(body.password), body.role)
    return {"id": uid}


@app.put("/api/users/{user_id}")
async def api_user_update(user_id: int, body: UserUpdate, request: Request,
                          session: sessions.Session = Depends(admin_session)):
    t = i18n.translator(i18n.pick_lang(request))
    user = storage.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if (body.role != "admin" or body.disabled) and user["role"] == "admin" and not _admins_left(user_id):
        raise HTTPException(422, t("err_last_admin"))
    fields = {"name": body.name, "role": body.role, "disabled": body.disabled}
    if body.password:
        problem = _password_problem(body.password, body.password, t)
        if problem:
            raise HTTPException(422, problem)
        fields["password"] = auth.hash_password(body.password)
    storage.update_user(user_id, **fields)
    if body.disabled or body.password:
        sessions.drop_user(user_id)  # sign them out everywhere
    else:
        sessions.update_user(storage.get_user(user_id))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
async def api_user_delete(user_id: int, request: Request, session: sessions.Session = Depends(admin_session)):
    t = i18n.translator(i18n.pick_lang(request))
    if user_id == session.user["id"]:
        raise HTTPException(422, t("err_delete_self"))
    user = storage.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user["role"] == "admin" and not _admins_left(user_id):
        raise HTTPException(422, t("err_last_admin"))
    storage.delete_user(user_id)
    sessions.drop_user(user_id)
    return {"ok": True}


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, session: sessions.Session = Depends(current_session)):
    from zoneinfo import available_timezones
    zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    return render(request, "account.html", session=session, zones=["UTC"] + zones,
                  strings=i18n.bundle(i18n.pick_lang(request)))


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    time_zone: str = Field(max_length=64)


@app.put("/api/account")
async def api_account(body: AccountIn, session: sessions.Session = Depends(current_session)):
    try:
        ZoneInfo(body.time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown time zone")
    storage.update_user(session.user["id"], name=body.name.strip(), time_zone=body.time_zone)
    sessions.update_user(storage.get_user(session.user["id"]))
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str
    new2: str


@app.post("/api/account/password")
async def api_account_password(body: PasswordIn, request: Request,
                               session: sessions.Session = Depends(current_session)):
    t = i18n.translator(i18n.pick_lang(request))
    user = storage.get_user(session.user["id"])
    if not auth.verify_password(body.current, user["password"]):
        raise HTTPException(422, t("err_current_password"))
    problem = _password_problem(body.new, body.new2, t)
    if problem:
        raise HTTPException(422, problem)
    storage.update_user(user["id"], password=auth.hash_password(body.new))
    return {"ok": True}
