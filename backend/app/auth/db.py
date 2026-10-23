"""The users database: one SQLite file, two tables (as Open WebUI splits them).

`user` is who someone is; `auth` is how they sign in (same id). `session_stamp` is
a random value inside every token: changing it signs that user out everywhere.
"""
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .security import hash_password

ROLES = ("admin", "user")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at INTEGER NOT NULL,
    last_active_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth (
    id TEXT PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    session_stamp TEXT NOT NULL
);
"""

_path: Path | None = None


def init(path: Path) -> None:
    global _path
    path.parent.mkdir(parents=True, exist_ok=True)
    _path = path
    with db() as c:
        c.executescript(_SCHEMA)


@contextmanager
def db():
    # shortcut: one connection per request, fine for an internal tool; pool it if this gets busy.
    c = sqlite3.connect(_path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def check_new(email: str, password: str, role: str) -> str:
    """Returns the clean email, or raises ValueError with a message for the admin."""
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError("That is not a valid email address.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
    if role not in ROLES:
        raise ValueError("The role must be admin or user.")
    return email


def create_user(c: sqlite3.Connection, email: str, name: str, password: str, role: str) -> dict:
    email = check_new(email, password, role)
    if c.execute("SELECT 1 FROM user WHERE email = ?", (email,)).fetchone():
        raise ValueError("That email is already registered.")
    uid, now = str(uuid.uuid4()), int(time.time())
    c.execute("INSERT INTO user VALUES (?,?,?,?,?,?)", (uid, email, name.strip() or email, role, now, now))
    c.execute("INSERT INTO auth VALUES (?,?,1,?)", (uid, hash_password(password), secrets.token_hex(16)))
    return public(c.execute("SELECT * FROM user WHERE id = ?", (uid,)).fetchone(), 1)


def public(row: sqlite3.Row, active: int) -> dict:
    return {**{k: row[k] for k in ("id", "email", "name", "role", "created_at", "last_active_at")}, "active": bool(active)}


def primary_admin_id(c: sqlite3.Connection) -> str | None:
    """The first account ever made: it cannot be deleted, demoted or disabled."""
    row = c.execute("SELECT id FROM user ORDER BY created_at, rowid LIMIT 1").fetchone()
    return row["id"] if row else None


def seed_admin(email: str, password: str, name: str = "Admin") -> bool:
    """Make the first admin from ADMIN_EMAIL / ADMIN_PASSWORD, only while there are no users."""
    if not (email and password):
        return False
    with db() as c:
        if c.execute("SELECT 1 FROM user LIMIT 1").fetchone():
            return False
        create_user(c, email, name, password, "admin")
    return True
