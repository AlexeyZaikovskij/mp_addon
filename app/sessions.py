"""Server-side sessions. The browser only holds a random session id; the MultiProbe token stays here.

Sessions live in memory, so a restart of the add-on logs everybody out — acceptable for a report tool.
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings

COOKIE_NAME = "mpr_session"


@dataclass
class Session:
    token: str
    profile: dict[str, Any]
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def user_name(self) -> str:
        return self.profile.get("userName") or self.profile.get("loginName") or ""

    @property
    def user_id(self) -> str:
        return self.profile.get("userID") or self.profile.get("loginName") or ""

    @property
    def time_zone(self) -> str:
        return (self.profile.get("timeZoneSettings") or {}).get("timeZone") or "UTC"


_sessions: dict[str, Session] = {}


def create(token: str, profile: dict[str, Any]) -> str:
    _purge()
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = Session(token, profile)
    return sid


def get(sid: str | None) -> Session | None:
    if not sid:
        return None
    session = _sessions.get(sid)
    if session is None:
        return None
    if time.monotonic() - session.last_seen > settings.session_idle_minutes * 60:
        _sessions.pop(sid, None)
        return None
    session.last_seen = time.monotonic()
    return session


def drop(sid: str | None) -> None:
    if sid:
        _sessions.pop(sid, None)


def _purge() -> None:
    limit = settings.session_idle_minutes * 60
    now = time.monotonic()
    for sid in [s for s, v in _sessions.items() if now - v.last_seen > limit]:
        _sessions.pop(sid, None)
