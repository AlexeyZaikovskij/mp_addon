"""Scheduled exports of recordings: a period (yesterday, or a chosen one) is cut into pieces of a set length
and saved one by one as MP4/MP3 into a folder an administrator allowed.

A job is one-time (its period, from a start time on) or daily (every day at a set time: the previous day,
00:00–24:00 in the author's time zone). Each start makes a run with its pieces in the database, so a run
survives a restart of the Toolkit and continues where it stopped. Pieces are done strictly one at a time,
to keep the load on MultiProbe low, with the server's account for jobs (see storage.JOBS).
"""
import asyncio
import logging
import shutil
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.concurrency import run_in_threadpool

from . import jobs, mp_client, recordings, storage

log = logging.getLogger("mp_toolkit.scheduler")

TICK = 20  # seconds between looks at the schedule when there is nothing to do
MAX_ATTEMPTS = 3  # a piece that fails is tried again, after the others, up to this many times
RETRY_PAUSE = 30  # seconds after a failure, so a server that is away is not hammered
CHUNKS = (15, 30, 60, 120, 360, 720, 1440)  # minutes

_wake = asyncio.Event()
_conns: dict[int, mp_client.Conn] = {}
current: dict = {}  # the piece being exported: {"piece_id", "job": jobs.Job}


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def next_daily(at: str, tz: ZoneInfo, after: datetime) -> datetime:
    """The first moment after `after` when the clock in `tz` shows `at` ("HH:MM")."""
    hour, minute = (int(x) for x in at.split(":"))
    local = after.astimezone(tz)
    when = datetime.combine(local.date(), dtime(hour, minute), tz)
    if when <= local:
        when = datetime.combine(local.date() + timedelta(days=1), dtime(hour, minute), tz)
    return when.astimezone(timezone.utc)


def period(params: dict, started: datetime) -> tuple[datetime, datetime]:
    if params["kind"] == "daily":
        tz = zone(params["time_zone"])
        day = started.astimezone(tz).date() - timedelta(days=1)
        # In UTC: on the days the clock changes a day has 23 or 25 hours, and pieces are cut in real time.
        return (datetime.combine(day, dtime(0), tz).astimezone(timezone.utc),
                datetime.combine(day + timedelta(days=1), dtime(0), tz).astimezone(timezone.utc))
    return parse(params["begin"]), parse(params["end"])


def pieces(begin: datetime, end: datetime, chunk_minutes: int, formats: list[str]) -> list[tuple[str, str, str]]:
    out, step, t = [], timedelta(minutes=chunk_minutes), begin
    while t < end:
        stop = min(t + step, end)
        out += [(utc_iso(t), utc_iso(stop), f) for f in formats]
        t = stop
    return out


def start_run(job: dict, now: datetime | None = None) -> int:
    """Make a run of the job now; the next start of a daily job moves to its next day."""
    now = now or datetime.now(timezone.utc)
    p = job["params"]
    begin, end = period(p, now)
    run_id = storage.create_run(job["id"], utc_iso(begin), utc_iso(end),
                                pieces(begin, end, p["chunk_minutes"], p["formats"]))
    if p["kind"] == "daily":
        storage.update_job(job["id"], next_run=utc_iso(next_daily(p["run_at"], zone(p["time_zone"]), now)))
    elif job.get("next_run"):
        storage.update_job(job["id"], next_run=None)
    wake()
    return run_id


def wake() -> None:
    _wake.set()


# --- Doing the pieces ---------------------------------------------------------------------------------

async def _conn(server_id: int) -> mp_client.Conn:
    conn = _conns.get(server_id)
    if conn is not None:
        return conn
    srv = storage.get_server(server_id)
    if srv is None:
        raise RuntimeError("The server is no longer in the list")
    cred = storage.get_credential(storage.JOBS, server_id)
    if cred is None:
        raise RuntimeError(f"No account for jobs is set for the server {srv['name']}")

    async def renew() -> str:
        again = storage.get_credential(storage.JOBS, server_id)
        if again is None:
            raise mp_client.MPAuthError("noCredential", "No account for jobs")
        return (await mp_client.login(srv["url"], again[0], again[1], srv["verify_tls"]))[0]

    token, _ = await mp_client.login(srv["url"], cred[0], cred[1], srv["verify_tls"])
    conn = mp_client.Conn(server_id, srv["name"], srv["url"], token, srv["verify_tls"], renew=renew)
    _conns[server_id] = conn
    return conn


def _safe(name: str) -> str:
    return "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip().rstrip(".") or "recording"


def destination(folder: str, params: dict, begin: datetime, end: datetime, fmt: str) -> Path:
    """<folder>/<channel>/<date>/<channel> <date> HH-MM-SS - HH-MM-SS.<fmt>, in the job's time zone."""
    tz = zone(params["time_zone"])
    b, e = begin.astimezone(tz), end.astimezone(tz)
    channel = _safe(params.get("channel") or params.get("task_name") or "recording")
    stop = f"{e:%H-%M-%S}" if (e - timedelta(seconds=1)).date() == b.date() else f"{e:%Y-%m-%d %H-%M-%S}"
    if e.time() == dtime(0) and (e - b) <= timedelta(days=1) and e.date() != b.date():
        stop = "24-00-00"  # a piece up to midnight: "23-00-00 - 24-00-00", not the next day's date
    again = " (2)" if b.fold else ""  # the hour that repeats when the clock goes back
    return Path(folder) / channel / f"{b:%Y-%m-%d}" / f"{channel} {b:%Y-%m-%d %H-%M-%S} - {stop}{again}.{fmt}"


async def _do_piece(piece: dict) -> None:
    run_id = piece["run_id"]
    job = storage.get_job(piece["job_id"])
    if job is None:
        storage.update_piece(piece["id"], state="cancelled", error="The job was deleted")
        storage.finish_run_if_done(run_id)
        return
    p = job["params"]
    attempts = piece["attempts"] + 1
    storage.update_piece(piece["id"], state="running", attempts=attempts, error=None)
    begin, end, fmt = parse(piece["begin"]), parse(piece["end"]), piece["format"]
    work = jobs.Job(id=f"sched-{piece['id']}", owner="scheduler", params={"format": fmt})
    current.update(piece_id=piece["id"], job=work)
    try:
        folder = storage.get_folder(p["folder_id"])
        if folder is None:
            raise RuntimeError("The folder for the files was removed by an administrator")
        conn = await _conn(p["server_id"])
        if not any(r["media"] for r in await recordings.timeline(conn, p["task_id"], begin, end)):
            storage.update_piece(piece["id"], state="skipped", error="no recording")
            return
        await recordings.export(work, conn, p["task_id"], begin, end, fmt,
                                p.get("pids") if fmt == "mp4" else None, p.get("audio_pid"))
        source = recordings.export_path(work)
        target = destination(folder["path"], p, begin, end, fmt)
        await run_in_threadpool(_move, source, target)
        storage.update_piece(piece["id"], state="done", file=str(target), size=target.stat().st_size)
    except Exception as exc:  # reported on the jobs page; the piece is tried again later
        if isinstance(exc, mp_client.MPAuthError):
            _conns.pop(p["server_id"], None)
        message = getattr(exc, "message", "") or str(exc) or exc.__class__.__name__
        storage.update_piece(piece["id"], state="pending" if attempts < MAX_ATTEMPTS else "failed", error=message)
        log.warning("export piece %s failed (attempt %s): %s", piece["id"], attempts, message)
        await asyncio.sleep(RETRY_PAUSE)
    finally:
        current.clear()
        storage.finish_run_if_done(run_id)


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()  # a piece done again replaces its file
    shutil.move(str(source), str(target))


async def loop() -> None:
    """Runs for the lifetime of the Toolkit (started in main.lifespan)."""
    storage.reset_running_pieces()
    while True:
        try:
            now = datetime.now(timezone.utc)
            for job in storage.due_jobs(utc_iso(now)):
                start_run(job, now)
            piece = storage.next_piece()
            if piece is not None:
                await _do_piece(piece)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), TICK)
        except asyncio.TimeoutError:
            pass
