"""Per-model answer settings an admin can change without a restart (Admin Panel -> Models).

Modelled on Open WebUI's model "params": a system prompt and sampling options stored for
each model, laid over the defaults of models.yaml for every chat reply. Stored as JSON in
auth.db. Local models only: OpenCode's cloud models take no options from here.
The same JSON holds "enabled": false for a model an admin turned off (missing = on); the chat
then leaves it out (`EnabledOnly`). Mounted at /api/admin/models (admin only)."""
import dataclasses
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from ollama_pipeline import ModelEntry, ModelNotFound

from .auth import admin_user
from .auth.db import db

router = APIRouter()

_SCHEMA = "CREATE TABLE IF NOT EXISTS model_setting (model_id TEXT PRIMARY KEY, data TEXT NOT NULL)"

# key -> (type, low, high). `system` is text, `stop` a list of text, the rest are numbers.
NUMBERS = {
    "temperature": (float, 0, 2),
    "top_p": (float, 0, 1),
    "top_k": (int, 0, 1000),
    "min_p": (float, 0, 1),
    "repeat_penalty": (float, 0, 3),
    "presence_penalty": (float, -2, 2),
    "frequency_penalty": (float, -2, 2),
    "num_predict": (int, 1, 32768),
    "seed": (int, 0, 2**31 - 1),
    "repeat_last_n": (int, -1, 4096),
    "mirostat": (int, 0, 2),
    "mirostat_tau": (float, 0, 10),
    "mirostat_eta": (float, 0, 1),
    "typical_p": (float, 0, 1),
}
FLAGS = ("think", "enabled")  # true or false


def init() -> None:
    with db() as c:
        c.execute(_SCHEMA)


def load(model_id: str) -> dict[str, Any]:
    with db() as c:
        row = c.execute("SELECT data FROM model_setting WHERE model_id = ?", (model_id,)).fetchone()
    return json.loads(row["data"]) if row else {}


def is_enabled(model_id: str) -> bool:
    return load(model_id).get("enabled", True)


def _save(model_id: str, params: dict[str, Any], enabled: bool) -> None:
    data = params if enabled else {**params, "enabled": False}  # on is the default: not stored
    with db() as c:
        if data:
            c.execute("INSERT OR REPLACE INTO model_setting (model_id, data) VALUES (?, ?)", (model_id, json.dumps(data)))
        else:
            c.execute("DELETE FROM model_setting WHERE model_id = ?", (model_id,))


def clean(raw: dict) -> dict[str, Any]:
    """Keeps known keys with a value, as the right type and inside its range; anything else is an error."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value in (None, "", []):
            continue  # empty = back to the models.yaml default
        if key == "system":
            out[key] = str(value)[:20000]
        elif key == "stop":
            if not isinstance(value, list):
                raise HTTPException(422, "stop must be a list.")
            out[key] = [str(s)[:100] for s in value if str(s)][:8]
        elif key == "tool_ids":  # the model's default tools (Admin Panel -> Tools), ticked for it in the chat
            if not isinstance(value, list) or len(value) > 20 or not all(isinstance(t, str) and t.isidentifier() for t in value):
                raise HTTPException(422, "tool_ids must be a list of up to 20 tool ids.")
            out[key] = list(dict.fromkeys(value))
        elif key in FLAGS:
            if not isinstance(value, bool):
                raise HTTPException(422, f"{key} must be true or false.")
            out[key] = value
        elif key in NUMBERS:
            kind, low, high = NUMBERS[key]
            try:
                number = kind(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{key} must be a number.") from None
            if not low <= number <= high:
                raise HTTPException(422, f"{key} must be between {low} and {high}.")
            out[key] = number
        else:
            raise HTTPException(422, f"Unknown setting {key}.")
    return out


def apply(entry: ModelEntry) -> ModelEntry:
    """The entry with its admin settings laid over models.yaml's. Cloud models are left alone."""
    if entry.provider != "ollama":
        return entry
    saved = load(entry.id)
    saved.pop("enabled", None)
    saved.pop("tool_ids", None)  # not a sampling option
    if not saved:
        return entry
    system = saved.pop("system", None)
    return dataclasses.replace(entry, system=system or entry.system, options={**entry.options, **saved})


class EnabledOnly:
    """The registry as the chat sees it: models an admin turned off are left out of the picker,
    the default and Auto's choices. Everything else is the registry's own."""

    def __init__(self, registry):
        self._registry = registry

    def __getattr__(self, name):
        return getattr(self._registry, name)

    async def list_models(self) -> list[ModelEntry]:
        return [e for e in await self._registry.list_models() if is_enabled(e.id)]

    async def resolve(self, model_id: str | None) -> ModelEntry:
        if model_id is None:  # the default model, picked among those still on
            entries = await self.list_models()
            if not entries and await self._registry.list_models():
                raise ModelNotFound("Every model is turned off by an admin.")
            # None on: the first that is on, so the registry explains why it cannot run (server down, not pulled).
            model_id = self._registry.default_id(entries) or (entries[0].id if entries else None)
        return await self._registry.resolve(model_id)


def _server() -> tuple[bool, dict, dict]:
    """llama-server's ctx-size and slots per model (model_server.py), or unavailable without it."""
    try:
        from .model_server import server_info
    except ImportError:
        return False, {}, {}
    return server_info()


async def _local(request: Request) -> list[ModelEntry]:
    return [e for e in await request.app.state.registry.list_models() if e.provider == "ollama"]


async def _known(request: Request, model_id: str) -> None:
    if model_id not in {e.id for e in await _local(request)}:
        raise HTTPException(404, "No such model.")


@router.get("")
async def list_settings(request: Request, _: dict = Depends(admin_user)):
    """Every local model with its saved settings, on/off, the models.yaml defaults they replace
    and, on llama-server, its context size and slots."""
    available, server_limits, server = _server()
    rows = []
    for e in await _local(request):
        saved = load(e.id)
        rows.append({
            "id": e.id, "label": e.label, "model": e.model, "available": e.available,
            "enabled": saved.pop("enabled", True), "description": e.description,
            "defaults": {"system": e.system, **e.options}, "saved": saved,
            "server": server.get(e.model) if available else None,
        })
    return {
        "limits": {k: [low, high] for k, (_, low, high) in NUMBERS.items()},
        "server": {"available": available, "limits": server_limits},
        "models": rows,
    }


@router.put("/{model_id}")
async def save_settings(model_id: str, body: dict, request: Request, _: dict = Depends(admin_user)):
    await _known(request, model_id)
    params = clean(body)
    enabled = params.pop("enabled", is_enabled(model_id))  # on/off kept unless sent
    _save(model_id, params, enabled)
    return {"saved": params}


@router.patch("/{model_id}/enabled")
async def set_enabled(model_id: str, body: dict, request: Request, _: dict = Depends(admin_user)):
    """Turns a model on or off for every user."""
    await _known(request, model_id)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(422, "enabled must be true or false.")
    params = load(model_id)
    params.pop("enabled", None)
    _save(model_id, params, enabled)
    return {"id": model_id, "enabled": enabled}
