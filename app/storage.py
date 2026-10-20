"""Report templates and the list of MultiProbe servers, kept in a small SQLite file (data/addon.db)."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    owner_name  TEXT NOT NULL,
    shared      INTEGER NOT NULL DEFAULT 0,
    params      TEXT NOT NULL,      -- JSON: wizard state (type, node, objects, alarms, measures, period)
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS server (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    verify_tls  INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,      -- user id on the main server
    created_by_name TEXT NOT NULL,
    created     TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "addon.db")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with closing(_connect()) as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["shared"] = bool(d["shared"])
    d["params"] = json.loads(d["params"])
    return d


def list_visible(user_id: str) -> list[dict[str, Any]]:
    """The user's own templates and the ones others shared."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM report_template WHERE owner_id = ? OR shared = 1 ORDER BY name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    return [_to_dict(r) for r in rows]


def get_visible(template_id: int, user_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM report_template WHERE id = ? AND (owner_id = ? OR shared = 1)",
            (template_id, user_id),
        ).fetchone()
    return _to_dict(row) if row else None


def create(name: str, owner_id: str, owner_name: str, shared: bool, params: dict) -> int:
    now = _now()
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO report_template (name, owner_id, owner_name, shared, params, created, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, owner_id, owner_name, int(shared), json.dumps(params), now, now),
        )
        return int(cur.lastrowid)


def update(template_id: int, owner_id: str, name: str, shared: bool, params: dict | None) -> bool:
    """Only the owner may change a template. Returns False when it is not theirs or missing."""
    with closing(_connect()) as conn, conn:
        if params is None:
            cur = conn.execute(
                "UPDATE report_template SET name = ?, shared = ?, updated = ? WHERE id = ? AND owner_id = ?",
                (name, int(shared), _now(), template_id, owner_id),
            )
        else:
            cur = conn.execute(
                "UPDATE report_template SET name = ?, shared = ?, params = ?, updated = ?"
                " WHERE id = ? AND owner_id = ?",
                (name, int(shared), json.dumps(params), _now(), template_id, owner_id),
            )
        return cur.rowcount == 1


def delete(template_id: int, owner_id: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM report_template WHERE id = ? AND owner_id = ?", (template_id, owner_id))
        return cur.rowcount == 1


# --- MultiProbe servers (shared by every user; each user signs in to them with their own account) ---

def _server(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["verify_tls"] = bool(d["verify_tls"])
    return d


def list_servers() -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM server ORDER BY name COLLATE NOCASE").fetchall()
    return [_server(r) for r in rows]


def get_server(server_id: int) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM server WHERE id = ?", (server_id,)).fetchone()
    return _server(row) if row else None


def create_server(name: str, url: str, verify_tls: bool, user_id: str, user_name: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO server (name, url, verify_tls, created_by, created_by_name, created) VALUES (?, ?, ?, ?, ?, ?)",
            (name, url, int(verify_tls), user_id, user_name, _now()),
        )
        return int(cur.lastrowid)


def update_server(server_id: int, name: str, url: str, verify_tls: bool) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("UPDATE server SET name = ?, url = ?, verify_tls = ? WHERE id = ?",
                           (name, url, int(verify_tls), server_id))
        return cur.rowcount == 1


def delete_server(server_id: int) -> bool:
    with closing(_connect()) as conn, conn:
        return conn.execute("DELETE FROM server WHERE id = ?", (server_id,)).rowcount == 1
