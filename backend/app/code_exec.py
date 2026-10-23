"""Settings for running Python from a chat reply (Admin Panel -> Code), modelled on Open WebUI's
Admin -> Settings -> Code Execution. The code itself runs in the browser (frontend/codeexec.js);
the server only says whether it may and for how long. Stored in auth.db. Mounted at /api/code."""
from fastapi import APIRouter, Depends, HTTPException

from .auth import admin_user
from .auth.db import db

router = APIRouter()

DEFAULTS = {"enabled": True, "timeout": 60}
_SCHEMA = "CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def init() -> None:
    with db() as c:
        c.execute(_SCHEMA)


def load() -> dict:
    with db() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM app_setting WHERE key LIKE 'code.%'")}
    return {
        "enabled": rows.get("code.enabled", "1") == "1",
        "timeout": int(rows.get("code.timeout", DEFAULTS["timeout"])),
    }


@router.get("")
async def get_settings():
    """Any signed-in user: the chat page needs it to show or hide the Run button."""
    return load()


@router.put("")
async def save_settings(body: dict, _: dict = Depends(admin_user)):
    try:
        timeout = int(body.get("timeout", DEFAULTS["timeout"]))
    except (TypeError, ValueError):
        raise HTTPException(422, "timeout must be a number of seconds.") from None
    if not 1 <= timeout <= 600:
        raise HTTPException(422, "timeout must be between 1 and 600 seconds.")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO app_setting VALUES ('code.enabled', ?)", ("1" if body.get("enabled") else "0",))
        c.execute("INSERT OR REPLACE INTO app_setting VALUES ('code.timeout', ?)", (str(timeout),))
    return load()
