"""Server-side sessions. The browser only holds a random session id; the MultiProbe tokens stay here.

A session starts by signing in to the main server (config.env); the user may then connect to more
MultiProbe servers with their own accounts. Sessions live in memory, so a restart of the add-on
logs everybody out — acceptable for this tool.
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .mp_client import Conn

COOKIE_NAME = "mpr_session"
MAIN_SERVER = 0


@dataclass
class Session:
    profile: dict[str, Any]                       # the user's profile on the main server
    conns: dict[int, Conn] = field(default_factory=dict)
    logins: dict[int, str] = field(default_factory=dict)  # server id -> login used there (shown in the UI)
    scope: int | None = None  # server picked in the header switcher; None = all connected servers
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def main(self) -> Conn:
        return self.conns[MAIN_SERVER]

    @property
    def token(self) -> str:  # the main server's token
        return self.main.token

    def scoped(self) -> list[Conn]:
        """Connections the overview and the wizard work with: the picked server, or all of them."""
        if self.scope is not None and self.scope not in self.conns:
            self.scope = None  # the picked server was disconnected
        conns = [self.conns[self.scope]] if self.scope is not None else list(self.conns.values())
        return sorted(conns, key=lambda c: (c.server_id != MAIN_SERVER, c.name.lower()))

    def conn(self, server_id: int) -> Conn:
        c = self.conns.get(server_id)
        if c is None:
            raise NotConnected(server_id)
        return c

    @property
    def user_name(self) -> str:
        return self.profile.get("userName") or self.profile.get("loginName") or ""

    @property
    def user_id(self) -> str:
        return self.profile.get("userID") or self.profile.get("loginName") or ""

    @property
    def time_zone(self) -> str:
        return (self.profile.get("timeZoneSettings") or {}).get("timeZone") or "UTC"


class NotConnected(Exception):
    def __init__(self, server_id: int):
        super().__init__(f"not connected to server {server_id}")
        self.server_id = server_id


_sessions: dict[str, Session] = {}


def create(main: Conn, profile: dict[str, Any], login_name: str) -> str:
    _purge()
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = Session(profile, {MAIN_SERVER: main}, {MAIN_SERVER: login_name})
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
