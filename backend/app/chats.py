"""Saved chats, one list per user, in the same SQLite file as the users (auth.db).

A chat is its whole message list as JSON. Everything is scoped to the signed-in user:
another user's chat id answers 404, and deleting a user deletes their chats.
Mounted at /api/chats behind the login."""
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import current_user
from .auth.db import db

router = APIRouter()
MAX_BYTES = 8 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    messages TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_user_updated ON chat (user_id, updated_at DESC);
"""


def init() -> None:
    with db() as c:
        c.executescript(_SCHEMA)


class ChatBody(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None


def _clean(messages: list[dict]) -> str:
    # shortcut: pictures are not kept (they are large); the text, file names and file text are.
    # Upgrade: store images as files and keep their names here.
    kept = [{k: v for k, v in m.items() if k != "images"} for m in messages if m.get("role") in ("user", "assistant")]
    text = json.dumps(kept, ensure_ascii=False)
    if len(text.encode()) > MAX_BYTES:
        raise HTTPException(413, "This chat is too large to save.")
    return text


def _title(messages: list[dict]) -> str:
    first = next((m.get("content", "") for m in messages if m.get("role") == "user"), "").strip().replace("\n", " ")
    return (first[:60] + "…" if len(first) > 60 else first) or "New chat"


def _mine(c, chat_id: str, user: dict):
    row = c.execute("SELECT * FROM chat WHERE id = ? AND user_id = ?", (chat_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "No such chat.")
    return row


@router.get("")
def list_chats(q: str = "", user: dict = Depends(current_user)):
    like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db() as c:
        rows = c.execute(
            "SELECT id, title, created_at, updated_at FROM chat WHERE user_id = ? "
            "AND (title LIKE ? ESCAPE '\\' OR messages LIKE ? ESCAPE '\\') ORDER BY updated_at DESC LIMIT 500",
            (user["id"], like, like),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("")
def create_chat(body: ChatBody, user: dict = Depends(current_user)):
    msgs = body.messages or []
    cid, now = str(uuid.uuid4()), int(time.time())
    with db() as c:
        c.execute("INSERT INTO chat VALUES (?,?,?,?,?,?)",
                  (cid, user["id"], (body.title or _title(msgs))[:200], _clean(msgs), now, now))
    return {"id": cid}


@router.get("/{chat_id}")
def get_chat(chat_id: str, user: dict = Depends(current_user)):
    with db() as c:
        row = _mine(c, chat_id, user)
    return {**{k: row[k] for k in ("id", "title", "created_at", "updated_at")}, "messages": json.loads(row["messages"])}


@router.post("/{chat_id}")
def save_chat(chat_id: str, body: ChatBody, user: dict = Depends(current_user)):
    with db() as c:
        row = _mine(c, chat_id, user)
        title = (body.title.strip()[:200] if body.title else None) or row["title"]
        text = _clean(body.messages) if body.messages is not None else row["messages"]
        c.execute("UPDATE chat SET title = ?, messages = ?, updated_at = ? WHERE id = ?", (title, text, int(time.time()), chat_id))
    return {"id": chat_id, "title": title}


# POST, not DELETE: main.py's CORS setting allows only GET and POST.
@router.post("/{chat_id}/delete")
def delete_chat(chat_id: str, user: dict = Depends(current_user)):
    with db() as c:
        _mine(c, chat_id, user)
        c.execute("DELETE FROM chat WHERE id = ?", (chat_id,))
    return {"ok": True}
