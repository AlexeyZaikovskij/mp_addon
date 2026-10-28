"""Toolkit users, report templates, the list of MultiProbe servers and the MultiProbe accounts the Toolkit
keeps, in a small SQLite file (data/addon.db)."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import vault
from .config import settings

JOBS = "@jobs"  # owner of the account a server's scheduled jobs use (instead of a Toolkit user id)

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
    created_by  TEXT NOT NULL,      -- Toolkit user id
    created_by_name TEXT NOT NULL,
    created     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_user (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT NOT NULL,
    password    TEXT NOT NULL,      -- scrypt hash, see auth.py
    role        TEXT NOT NULL DEFAULT 'user',   -- admin | user
    time_zone   TEXT NOT NULL DEFAULT '',
    disabled    INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL,
    last_login  TEXT
);
CREATE TABLE IF NOT EXISTS mp_credential (
    owner       TEXT NOT NULL,      -- Toolkit user id, or '@jobs' for the server's scheduled jobs
    server_id   INTEGER NOT NULL,
    login       TEXT NOT NULL,
    secret      BLOB NOT NULL,      -- password encrypted with DPAPI, see vault.py
    updated     TEXT NOT NULL,
    PRIMARY KEY (owner, server_id)
);
CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
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
    _migrate_main_server()


def get_kv(key: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_kv(key: str, value: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def default_server_id() -> int | None:
    """The server from config.env (MP_API_URL). Templates saved before every server was in the list
    (server id 0, or just node_id/object_ids) refer to it."""
    value = get_kv("default_server_id")
    return int(value) if value else None


def _migrate_main_server() -> None:
    """Once: put the config.env server into the server list and point old templates at it."""
    if get_kv("default_server_id") is not None or not settings.mp_api_url:
        return
    existing = next((s for s in list_servers() if s["url"] == settings.mp_api_url), None)
    sid = existing["id"] if existing else create_server(settings.main_server_name, settings.mp_api_url, True, "", "")
    with closing(_connect()) as conn, conn:
        for row in conn.execute("SELECT id, params FROM report_template").fetchall():
            params = json.loads(row["params"])
            if params.get("targets"):
                for t in params["targets"]:
                    if t.get("server_id") == 0:
                        t["server_id"] = sid
            elif params.get("node_id") is not None:
                params["targets"] = [{"server_id": sid, "node_id": params.pop("node_id"),
                                      "object_ids": params.pop("object_ids", [])}]
            conn.execute("UPDATE report_template SET params = ? WHERE id = ?", (json.dumps(params), row["id"]))
        conn.execute("INSERT INTO kv (key, value) VALUES ('default_server_id', ?)", (str(sid),))


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
        conn.execute("DELETE FROM mp_credential WHERE server_id = ?", (server_id,))
        return conn.execute("DELETE FROM server WHERE id = ?", (server_id,)).rowcount == 1


# --- MultiProbe accounts the Toolkit keeps ("remember the password", the account for jobs) ---------

def save_credential(owner: str, server_id: int, login: str, password: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO mp_credential (owner, server_id, login, secret, updated) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(owner, server_id) DO UPDATE SET login = excluded.login, secret = excluded.secret,"
            " updated = excluded.updated",
            (owner, server_id, login, vault.encrypt(password), _now()),
        )


def get_credential(owner: str, server_id: int) -> tuple[str, str] | None:
    """(login, password), or None when nothing is kept or it cannot be decrypted on this computer."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT login, secret FROM mp_credential WHERE owner = ? AND server_id = ?",
                           (owner, server_id)).fetchone()
    if row is None:
        return None
    try:
        return row["login"], vault.decrypt(row["secret"])
    except vault.VaultError:
        return None


def credential_logins(owner: str) -> dict[int, str]:
    """server id -> login of the accounts kept for this owner."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT server_id, login FROM mp_credential WHERE owner = ?", (owner,)).fetchall()
    return {r["server_id"]: r["login"] for r in rows}


def delete_credential(owner: str, server_id: int | None = None) -> None:
    with closing(_connect()) as conn, conn:
        if server_id is None:
            conn.execute("DELETE FROM mp_credential WHERE owner = ?", (owner,))
        else:
            conn.execute("DELETE FROM mp_credential WHERE owner = ? AND server_id = ?", (owner, server_id))


# --- Toolkit users ----------------------------------------------------------------------------

def _user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["disabled"] = bool(d["disabled"])
    return d


def count_users() -> int:
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM app_user").fetchone()[0]


def list_users() -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM app_user ORDER BY login COLLATE NOCASE").fetchall()
    return [_user(r) for r in rows]


def get_user(user_id: int) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        return _user(conn.execute("SELECT * FROM app_user WHERE id = ?", (user_id,)).fetchone())


def get_user_by_login(login: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        return _user(conn.execute("SELECT * FROM app_user WHERE login = ? COLLATE NOCASE", (login.strip(),)).fetchone())


def create_user(login: str, name: str, password_hash: str, role: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("INSERT INTO app_user (login, name, password, role, created) VALUES (?, ?, ?, ?, ?)",
                           (login.strip(), name.strip(), password_hash, role, _now()))
        return int(cur.lastrowid)


def update_user(user_id: int, **fields: Any) -> None:
    allowed = {"name", "role", "disabled", "time_zone", "password", "last_login"}
    sets = {k: (int(v) if k == "disabled" else v) for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with closing(_connect()) as conn, conn:
        conn.execute(f"UPDATE app_user SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                     (*sets.values(), user_id))


def delete_user(user_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM mp_credential WHERE owner = ?", (str(user_id),))
        conn.execute("DELETE FROM app_user WHERE id = ?", (user_id,))


def claim_unowned(user_id: int, user_name: str) -> None:
    """The first administrator takes over templates and servers created before Toolkit had its own
    users (they belonged to MultiProbe user ids)."""
    ids = [str(u["id"]) for u in list_users()]
    marks = ",".join("?" * len(ids))
    with closing(_connect()) as conn, conn:
        conn.execute(f"UPDATE report_template SET owner_id = ?, owner_name = ? WHERE owner_id NOT IN ({marks})",
                     (str(user_id), user_name, *ids))
        conn.execute(f"UPDATE server SET created_by = ?, created_by_name = ? WHERE created_by NOT IN ({marks})",
                     (str(user_id), user_name, *ids))
