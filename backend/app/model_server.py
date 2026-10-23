"""llama-server's context size and slots per model (Admin Panel -> Models -> Server).

Reads and writes llamacpp/server/models.ini through `llamacpp_pipeline.server_config`
and restarts the container with it. Only when PIPELINE=llamacpp; on Ollama it says
unavailable. Mounted at /api/admin/llama-server (admin only).
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from llamacpp_pipeline import load_config, server_config

from .auth import admin_user
from .settings import settings

router = APIRouter(dependencies=[Depends(admin_user)])
_applying = asyncio.Lock()  # one restart at a time; models.ini is not written meanwhile


def server_info() -> tuple[bool, dict, dict]:
    """(available, limits, {model name: {"ctx_size", "parallel"}}) for GET /api/admin/models."""
    limits = {k: list(v) for k, v in server_config.LIMITS.items()}
    if settings.pipeline != "llamacpp":
        return False, limits, {}
    return True, limits, server_config.read()


def _available() -> None:
    if settings.pipeline != "llamacpp":
        raise HTTPException(404, "The app is not running on llama-server (PIPELINE=llamacpp).")
    if _applying.locked():
        raise HTTPException(409, "llama-server is being restarted. Try again in a minute.")


@router.get("")
async def get_server():
    available, limits, models = server_info()
    return {"available": available, "limits": limits, "models": models}


@router.put("")
async def save_server(body: dict):
    _available()
    try:
        server_config.write(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"models": server_config.read()}


@router.post("/apply")
async def apply_server(request: Request):
    _available()
    async with _applying:
        try:
            await server_config.restart()
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None
        finally:
            # num_ctx follows ctx-size: the new values on success, the restored ones on failure
            request.app.state.registry.config = load_config()
    return {"ok": True}
