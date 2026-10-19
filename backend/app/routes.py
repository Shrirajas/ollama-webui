import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from ollama_pipeline import (
    ModelNotFound,
    OllamaUnavailable,
    PipelineError,
    Registry,
    Router,
    UnsupportedDocument,
    chat_stream,
    extract_text,
    picker_models,
    select_model,
    select_tool_model,
    todays_theme,
)
from sql_connector import SqlTools

from .schemas import ChatRequest

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

router = APIRouter()


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_router(request: Request) -> Router:
    return request.app.state.router


@router.get("/health")
async def health(registry: Registry = Depends(get_registry)):
    hosts = await registry.health()
    return {"status": "ok" if any(h["ok"] for h in hosts.values()) else "degraded", "hosts": hosts}


@router.get("/models")
async def models(registry: Registry = Depends(get_registry), auto: Router = Depends(get_router)):
    default, entries = await picker_models(registry, auto)
    return {"default": default, "models": entries}


@router.get("/theme")
async def theme(registry: Registry = Depends(get_registry)):
    today = todays_theme(registry.config.themes)
    if today is None:
        raise HTTPException(404, "No themes configured in ollama/config/themes.yaml.")
    return {"title": today.title, "prompt": today.prompt}


@router.post("/files")
def extract_file(file: UploadFile):
    """Extracts the text of a PDF, DOCX or text file so it can be attached to a message."""
    name = file.filename or "file"
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over 20 MB")
    try:
        content, truncated = extract_text(name, data)
    except UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc
    return {"name": name, "content": content, "truncated": truncated}


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    auto: Router = Depends(get_router),
):
    """Streams the reply as newline-delimited JSON events: route, notice, thinking, delta,
    tool_call, tool_result, done, error.

    With `database` set the reply comes from a local model that queries that
    saved connection with the SQL connector's tools; Auto's routing is skipped.
    """
    messages = [m.model_dump() for m in body.messages]
    tools = None
    try:
        if body.database:
            connection = request.app.state.sql_store.get(body.database)
            if connection is None:
                raise HTTPException(404, f"There is no saved database connection named '{body.database}'.")
            sql = registry.config.sql
            tools = SqlTools([connection], max_rows=sql.max_rows, timeout=sql.timeout)
            entry, route = await select_tool_model(registry, body.model, sql.model)
            fallbacks = []
            system = "\n\n".join(part for part in (entry.system, tools.system_prompt()) if part)
            messages = [{"role": "system", "content": system}, *messages]
        else:
            entry, route, fallbacks = await select_model(registry, auto, body.model, messages)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        try:
            if route:
                yield json.dumps(route) + "\n"
            stream = chat_stream(
                registry, entry, messages, body.options, fallbacks,
                tools=tools.definitions if tools else None,
                run_tool=tools.run if tools else None,
                max_rounds=registry.config.sql.max_rounds,
            )
            async for event in stream:
                yield json.dumps(event, default=str) + "\n"
        except PipelineError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        # Keeps nginx and similar proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
