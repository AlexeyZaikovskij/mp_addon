"""Recordings (MultiRec): what is recorded, a piece of it in the browser, and a piece as MP4 or MP3.

Playing: MultiProbe cuts a piece of a recording into HLS on request (a "stream" download task) and publishes
it on the node web server, reachable through the API server at /fromNode/<node>/Streams/<task>/. The Toolkit
proxies that HLS to the browser, so the MultiProbe address and token stay here. Saving: see export() below.
Details: docs/MP_API_NOTES.md, "Recordings".
"""
import asyncio
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import av
from fastapi.concurrency import run_in_threadpool

from . import jobs, mp_client
from .mp_client import Conn

PIECE_IDLE_TTL = 3600  # a piece nobody played or saved for this long is dropped (with its MultiProbe task)
PIECES_PER_USER = 3  # older pieces of the same user are dropped first
STALL_TIMEOUT = 180  # seconds without progress before giving up on MultiProbe
MEDIA_STATES = {"data", "datawithAD", "reservesourcedata"}
_FILE_NAME = re.compile(r"^[\w.-]+\.(m3u8|ts|aac|m4s|mp4)$")
EXPORT_DIR = Path(tempfile.gettempdir()) / "mp_toolkit_exports"
# A cut starts in the middle of a GOP, and a GOP can be long (Alaska: ~11 s). With the default 5 s of
# analysis the video parameters (frame size) stay unknown and the MP4 header cannot be written.
_PROBE = {"analyzeduration": str(30_000_000), "probesize": str(100_000_000)}


# --- What is recorded ---------------------------------------------------------------------------------

async def tasks(conn: Conn) -> list[dict]:
    items = await mp_client.call(conn, "POST", "/api/Recording/Task/Find", {})
    return sorted(
        ({
            "server_id": conn.server_id, "server": conn.name, "task_id": t["taskID"],
            "name": t.get("monitoringObjectName") or t["taskID"], "kind": t.get("objectKind"),
            "object_id": t.get("monitoringObjectID"),
            "node_id": t.get("nodeID"), "node": t.get("nodeName") or "", "disabled": bool(t.get("isDisabled")),
        } for t in items or []),
        key=lambda t: t["name"].lower(),
    )


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(timezone.utc) if value else None


async def timeline(conn: Conn, task_id: str, begin: datetime, end: datetime) -> list[dict]:
    """Recording intervals that overlap [begin, end], clipped to it, oldest first. `media` = has content."""
    rows = await mp_client.call(conn, "POST", "/api/Recording/TimeLine/Find", {
        "taskID": task_id, "timeBegin": begin.isoformat(), "timeEnd": end.isoformat(),
    })
    now = datetime.now(timezone.utc)
    out = []
    for r in rows or []:
        b, e = _parse(r.get("timeBegin")), _parse(r.get("timeEnd")) or now  # the current interval is open
        if b is None or e <= begin or b >= end:
            continue
        out.append({"begin": max(b, begin), "end": min(e, end), "status": r.get("streamStatusType") or "",
                    "media": r.get("streamStatusType") in MEDIA_STATES, "storage": r.get("storageName") or ""})
    return sorted(out, key=lambda r: r["begin"])


_PID = re.compile(r"PID:\s*(\d+)")


async def services(conn: Conn, node_id: int, object_id: str) -> list[dict]:
    """Channels in a recording and their PIDs. A task on a service gives that one service; a task on a
    whole stream (multiplex) gives every service inside it. PIDs come from the decoder names MultiProbe
    gives ("Alaska; PID: 257; Audio")."""
    objs = await mp_client.monitoring_objects(conn, node_id)
    children: dict[str, list[dict]] = {}
    for o in objs:
        children.setdefault(o.get("parentID") or "", []).append(o)
    obj = next((o for o in objs if o["monitoringObjectID"] == object_id), None)
    if obj is None:
        return []
    found = [obj] if obj.get("objectKind") == "mpegService" else [
        o for o in children.get(object_id, []) if o.get("objectKind") == "mpegService"]
    out = []
    for svc in found:
        video, audio = [], []
        for d in children.get(svc["monitoringObjectID"], []):
            name, kind = d.get("name") or "", (d.get("typeName") or "").lower()
            m = _PID.search(name)
            if d.get("objectKind") != "mpegDecoder" or not m:
                continue
            pid = int(m.group(1))
            if "video" in kind or name.endswith("; Video"):
                video.append(pid)
            elif "audio" in kind or name.endswith("; Audio"):
                audio.append({"pid": pid, "name": f"PID {pid}"})
        out.append({"id": svc["monitoringObjectID"], "name": svc.get("name") or "",
                    "service_id": svc.get("serviceID"), "video": sorted(set(video)),
                    "audio": sorted({a["pid"]: a for a in audio}.values(), key=lambda a: a["pid"])})
    try:
        return sorted(out, key=lambda x: int(x["service_id"] or 0))
    except ValueError:
        return out


# --- Pieces: a part of a recording as HLS -------------------------------------------------------------

@dataclass
class Piece:
    id: str
    owner: str
    conn: Conn
    task_id: str
    name: str
    begin: datetime
    end: datetime
    download_task_id: str
    node_id: int
    used: float = field(default_factory=time.monotonic)
    ended: bool = False  # every media playlist has #EXT-X-ENDLIST: MultiProbe has written the whole piece

    @property
    def base(self) -> str:
        return f"/fromNode/{self.node_id}/Streams/{self.download_task_id}/"


_pieces: dict[str, Piece] = {}


async def _drop(piece: Piece) -> None:
    _pieces.pop(piece.id, None)
    try:
        await mp_client.call(piece.conn, "DELETE", f"/api/Recording/DownloadTask/Delete/{piece.download_task_id}")
    except mp_client.MPError:
        pass  # already gone, or the server is away; MultiProbe cleans its streams up itself eventually


async def _purge(owner: str) -> None:
    now = time.monotonic()
    stale = [p for p in _pieces.values() if now - p.used > PIECE_IDLE_TTL]
    mine = sorted((p for p in _pieces.values() if p.owner == owner and p not in stale), key=lambda p: p.used)
    stale += mine[: max(0, len(mine) - PIECES_PER_USER + 1)]
    for p in stale:
        await _drop(p)


async def create(owner: str, conn: Conn, task_id: str, name: str, begin: datetime, end: datetime) -> Piece:
    await _purge(owner)
    dt = await mp_client.call(conn, "POST", "/api/Recording/DownloadTask/Add", {
        "taskID": task_id, "startDate": begin.isoformat(), "endDate": end.isoformat(),
        "downloadTaskType": "stream", "downloadTaskStreamOutputType": "hls",
        # Required even when nothing is transcoded, otherwise MultiProbe answers 500 (NullReference).
        "transcodingConfiguration": {"encoder": {}, "decoder": {}, "render": {}},
    })
    piece = Piece(id=secrets.token_urlsafe(12), owner=owner, conn=conn, task_id=task_id, name=name, begin=begin,
                  end=end, download_task_id=dt["downloadTaskID"], node_id=dt["nodeID"])
    _pieces[piece.id] = piece
    return piece


def get(piece_id: str, owner: str) -> Piece | None:
    piece = _pieces.get(piece_id)
    if piece is None or piece.owner != owner:
        return None
    piece.used = time.monotonic()
    return piece


async def drop(piece: Piece) -> None:
    await _drop(piece)


async def close() -> None:
    """On shutdown: remove the MultiProbe tasks of all pieces."""
    await asyncio.gather(*(_drop(p) for p in list(_pieces.values())), return_exceptions=True)


async def fetch(piece: Piece, name: str) -> tuple[int, bytes, str]:
    """A file of the piece's HLS (status, body, content type)."""
    if not _FILE_NAME.match(name):
        return 404, b"", "text/plain"
    resp = await mp_client.get_file(piece.conn, piece.base + name)
    if resp.status_code != 200:
        return resp.status_code, b"", "text/plain"
    kind = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else resp.headers.get("content-type", "video/mp2t")
    return 200, resp.content, kind


def _uris(playlist: str) -> list[str]:
    return [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith("#")]


@dataclass
class Layout:
    video: str | None  # media playlist with the video
    audio: list[dict]  # [{name, language, uri}] — separate audio renditions, or the video playlist itself


async def _layout(piece: Piece) -> Layout | None:
    status, body, _ = await fetch(piece, "playlist.m3u8")
    if status != 200:
        return None
    text = body.decode("utf-8", "replace")
    audio = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            attrs = dict(re.findall(r'([A-Z-]+)=("[^"]*"|[^,]*)', line[len("#EXT-X-MEDIA:"):]))
            attrs = {k: v.strip('"') for k, v in attrs.items()}
            if attrs.get("URI"):
                audio.append({"name": attrs.get("NAME") or f"audio {len(audio) + 1}",
                              "language": attrs.get("LANGUAGE") or "", "uri": attrs["URI"]})
    variants = _uris(text)
    video = variants[0] if variants else None
    if not audio and video:
        audio = [{"name": "audio", "language": "", "uri": video}]  # audio muxed into the video segments
    return Layout(video, audio)


async def _segments(piece: Piece, uri: str) -> tuple[list[str], bool]:
    status, body, _ = await fetch(piece, uri)
    if status != 200:
        return [], False
    text = body.decode("utf-8", "replace")
    return _uris(text), "#EXT-X-ENDLIST" in text


async def status(piece: Piece) -> dict:
    """How far MultiProbe is with the piece: segments written so far and whether it has finished."""
    found = await mp_client.call(piece.conn, "POST", "/api/Recording/DownloadTask/Find", {"taskID": piece.task_id})
    task = next((t for t in found or [] if t.get("downloadTaskID") == piece.download_task_id), None)
    log = (task or {}).get("downloadTaskStateLog") or {}
    state = log.get("downloadTaskState") if task else "gone"
    layout = await _layout(piece)
    seconds, tracks = 0, []
    if layout and layout.video:
        uris = [layout.video] + [a["uri"] for a in layout.audio if a["uri"] != layout.video]
        lists = await asyncio.gather(*(_segments(piece, u) for u in uris))
        seconds = len(lists[0][0])  # MultiProbe writes 1 s segments
        piece.ended = all(ended for _, ended in lists)
        tracks = [{"name": a["name"], "language": a["language"]} for a in layout.audio]
    return {
        "state": state, "percent": log.get("downloadPercent") or 0, "message": log.get("message") or "",
        "failed": state in ("failed", "stopped", "gone") and not piece.ended,
        "seconds": seconds, "total_seconds": round((piece.end - piece.begin).total_seconds()),
        "ended": piece.ended, "playable": seconds >= 3 or (piece.ended and seconds > 0), "tracks": tracks,
    }


# --- Saving a part of a recording as a file -------------------------------------------------------------
# The HLS above is a preview (MultiProbe scales it down, e.g. to 640x360). Files are made from the original
# recording instead: a "download" task cuts it as TS (H.264/AAC as recorded), the node's app manager
# publishes it at /fromAppManager/<node>/export/…, and PyAV turns it into MP4 (no re-encoding) or MP3.

def export_path(job: jobs.Job) -> Path:
    return EXPORT_DIR / f"{job.id}.{job.params['format']}"


def purge_exports() -> None:
    """Files of expired exports (their job is gone after JOB_TTL) are deleted."""
    if not EXPORT_DIR.exists():
        return
    limit = time.time() - jobs.JOB_TTL
    for f in EXPORT_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < limit:
            f.unlink(missing_ok=True)


async def export(job: jobs.Job, conn: Conn, task_id: str, begin: datetime, end: datetime, fmt: str,
                 pids: list[int] | None = None, audio_pid: int | None = None) -> dict:
    """`pids`: video and audio PIDs of the channel to keep (None = everything of a one-service recording);
    `audio_pid`: the audio track for MP3 (None = the first one)."""
    """Progress: 0–100 MultiProbe cuts the recording, 100–180 downloading, 180–200 converting."""
    job.total_steps = 200
    job.params["phase"] = "cutting"
    dt = await mp_client.call(conn, "POST", "/api/Recording/DownloadTask/Add", {
        "taskID": task_id, "startDate": begin.isoformat(), "endDate": end.isoformat(),
        "downloadTaskType": "download",
        "transcodingConfiguration": {"encoder": {}, "decoder": {}, "render": {}},  # required, see create()
    })
    dt_id = dt["downloadTaskID"]
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    purge_exports()
    source = EXPORT_DIR / f"{job.id}.src.ts"
    try:
        last, last_change, completed = None, time.monotonic(), None
        while True:
            await asyncio.sleep(2)
            found = await mp_client.call(conn, "POST", "/api/Recording/DownloadTask/Find", {"taskID": task_id})
            task = next((t for t in found or [] if t.get("downloadTaskID") == dt_id), None)
            log = (task or {}).get("downloadTaskStateLog") or {}
            state, percent = log.get("downloadTaskState"), log.get("downloadPercent") or 0
            if task is None or state in ("failed", "stopped"):
                raise RuntimeError(f"MultiProbe: {state or 'task removed'} {log.get('message') or ''}".strip())
            if state == "completed":
                if task.get("url"):
                    break
                # Seen once: "completed" came before the link to the file. Give it a little time.
                completed = completed or time.monotonic()
                if time.monotonic() - completed > 30:
                    raise RuntimeError("MultiProbe did not publish the file")
                continue
            if percent != last:
                last, last_change = percent, time.monotonic()
            elif time.monotonic() - last_change > STALL_TIMEOUT:
                raise RuntimeError("MultiProbe stopped cutting the recording")
            job.done_steps = min(99, round(percent))

        job.params["phase"] = "downloading"

        def progress(done: int, total: int) -> None:
            job.done_steps = 100 + (round(80 * done / total) if total else 40)

        await mp_client.download(conn, f"/fromAppManager/{task['nodeID']}/{quote(task['url'].lstrip('/'))}",
                                 source, progress)
        job.params["phase"] = "converting"
        job.done_steps = 180
        out = export_path(job)
        try:
            if fmt == "mp3":
                await run_in_threadpool(_encode_mp3, source, out, audio_pid)
            else:
                await run_in_threadpool(_remux_mp4, source, out, pids)
        except Exception:
            out.unlink(missing_ok=True)  # no half-written files
            raise
    finally:
        source.unlink(missing_ok=True)
        try:  # the cut file is not needed on the MultiProbe side any more
            await mp_client.call(conn, "DELETE", f"/api/Recording/DownloadTask/Delete/{dt_id}")
        except mp_client.MPError:
            pass
    job.done_steps = 200
    return {"size": out.stat().st_size}


def _remux_mp4(source: Path, out: Path, pids: list[int] | None = None) -> None:
    """TS -> MP4 without re-encoding. Video starts at the first key frame (a cut usually starts in the
    middle of a GOP, and frames before the first key frame cannot be shown); audio is kept whole.

    A recording of a whole multiplex (a "stream" task) holds every service: `pids` picks the channel's
    video and audio (without them: the first video and audio). A source can restart its timestamps (a looped file, an encoder restart);
    MP4 needs them always growing, so after such a jump the time of that track simply continues."""
    with av.open(str(source), options=_PROBE) as src, av.open(str(out), "w", format="mp4", options={"movflags": "+faststart"}) as dst:
        videos, audios = list(src.streams.video), list(src.streams.audio)
        if pids:
            videos, audios = [v for v in videos if v.id in pids], [a for a in audios if a.id in pids]
            if not videos and not audios:
                raise RuntimeError("The selected channel is not in the recording")
        elif len(videos) > 1:
            videos, audios = videos[:1], audios[:1]
        if any(s.codec_context.width == 0 for s in videos):
            raise RuntimeError("No key frame in the piece: make it longer")
        mapping = {s.index: dst.add_stream_from_template(s) for s in videos + audios}
        if not mapping:
            raise RuntimeError("No audio or video in the recording")
        video_started = {s.index: False for s in videos}
        # Timestamps in TS are 90 kHz for every track, so one shift keeps audio and video together: the track
        # that jumps first sets it, the others take the same shift when they jump, and their few packets that
        # would overlap the previous part are dropped.
        common = 0
        shift: dict[int, int] = {}  # stream index -> ticks added to its timestamps
        last: dict[int, tuple[int, int]] = {}  # stream index -> (last dts after the shift, usual step)
        for packet in src.demux(*videos, *audios):
            if packet.dts is None or not packet.size:
                continue
            idx = packet.stream.index
            if idx in video_started and not video_started[idx]:
                if not packet.is_keyframe:
                    continue
                video_started[idx] = True
            dts = packet.dts + shift.get(idx, 0)
            if idx in last:
                prev, step = last[idx]
                second = round(1 / packet.time_base)
                if prev - second < dts <= prev and shift.get(idx, 0) == common:
                    continue  # still overlapping the end of the previous part
                if dts <= prev or dts - prev > 10 * second:  # restart (or a jump over 10 s)
                    own = prev + step - packet.dts  # the shift this track alone would need
                    if common > shift.get(idx, 0) and packet.dts + common > prev - second:
                        shift[idx] = common  # follow the track that jumped first
                    else:
                        shift[idx] = common = own
                    dts = packet.dts + shift[idx]
                    if dts <= prev:
                        continue  # overlaps the end of the previous part
                else:
                    step = dts - prev
                last[idx] = (dts, step)
            else:
                last[idx] = (dts, packet.duration or 1)
            delta = dts - packet.dts
            if delta:
                packet.pts = packet.pts + delta if packet.pts is not None else None
                packet.dts = dts
            packet.stream = mapping[idx]
            dst.mux(packet)


def _encode_mp3(source: Path, out: Path, audio_pid: int | None = None) -> None:
    with av.open(str(source), options=_PROBE) as src, av.open(str(out), "w", format="mp3") as dst:
        if not src.streams.audio:
            raise RuntimeError("No audio in the recording")
        s = src.streams.audio[0]
        if audio_pid is not None:
            s = next((a for a in src.streams.audio if a.id == audio_pid), None)
            if s is None:
                raise RuntimeError(f"Audio PID {audio_pid} is not in the recording")
        layout = "mono" if s.codec_context.layout.nb_channels == 1 else "stereo"
        o = dst.add_stream("libmp3lame", rate=s.codec_context.sample_rate or 48000, layout=layout)
        o.bit_rate = 192_000
        for packet in src.demux(s):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                continue  # a damaged packet at the cut
            for frame in frames:
                frame.pts = None  # PyAV regroups samples into MP3 frames; timestamps follow from the samples
                for p in o.encode(frame):
                    dst.mux(p)
        for p in o.encode(None):
            dst.mux(p)
