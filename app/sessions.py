"""Server-side sessions. The browser only holds a random session id; the MultiProbe tokens stay here.

A session belongs to a Toolkit user. Within it the user connects to any number of MultiProbe servers
with accounts of those servers. Sessions live in memory, so a restart of the Toolkit signs everybody
out — acceptable for this tool.
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .mp_client import Conn

COOKIE_NAME = "mpr_session"


@dataclass
class Session:
    user: dict[str, Any]                          # the Toolkit user (storage.app_user row, no password)
    conns: dict[int, Conn] = field(default_factory=dict)
    logins: dict[int, str] = field(default_factory=dict)  # server id -> MultiProbe login used there
    scope: int | None = None  # server picked in the header switcher; None = all connected servers
    last_seen: float = field(default_factory=time.monotonic)

    def scoped(self) -> list[Conn]:
        """Connections the overview and the wizard work with: the picked server, or all of them."""
        if self.scope is not None and self.scope not in self.conns:
            self.scope = None  # the picked server was disconnected
        conns = [self.conns[self.scope]] if self.scope is not None else list(self.conns.values())
        return sorted(conns, key=lambda c: c.name.lower())

    def conn(self, server_id: int) -> Conn:
        c = self.conns.get(server_id)
        if c is None:
            raise NotConnected(server_id)
        return c

    @property
    def user_id(self) -> str:
        return str(self.user["id"])

    @property
    def user_name(self) -> str:
        return self.user.get("name") or self.user.get("login") or ""

    @property
    def is_admin(self) -> bool:
        return self.user.get("role") == "admin"

    @property
    def time_zone(self) -> str:
        return self.user.get("time_zone") or settings.default_time_zone


class NotConnected(Exception):
    def __init__(self, server_id: int | None):
        super().__init__(f"not connected to server {server_id}")
        self.server_id = server_id  # None = not connected to any server


_sessions: dict[str, Session] = {}


def create(user: dict[str, Any]) -> str:
    _purge()
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = Session({k: v for k, v in user.items() if k != "password"})
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


def drop_user(user_id: int) -> None:
    """Sign a user out everywhere (disabled, deleted or password reset by an administrator)."""
    for sid in [s for s, v in _sessions.items() if v.user.get("id") == user_id]:
        _sessions.pop(sid, None)


def update_user(user: dict[str, Any]) -> None:
    """Refresh the user record (name, role, time zone) in that user's open sessions."""
    for v in _sessions.values():
        if v.user.get("id") == user["id"]:
            v.user = {k: val for k, val in user.items() if k != "password"}


def _purge() -> None:
    limit = settings.session_idle_minutes * 60
    now = time.monotonic()
    for sid in [s for s, v in _sessions.items() if now - v.last_seen > limit]:
        _sessions.pop(sid, None)
