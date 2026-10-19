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
    select_local_model,
    select_model,
    select_tool_model,
    todays_theme,
)
from agent_skills import GENERAL, ChartTool, MemoryTools, Toolbox, wants_chart, wants_memory
from sql_connector import SqlTools
from workspace_engine import WorkspaceError

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
    """Streams the reply as newline-delimited JSON events: route, sources, notice, thinking,
    delta, tool_call, tool_result, done, error.

    With `workspace` set the reply is based on the passages of that workspace's
    documents that match the question (a `sources` event lists them). With
    `database` set a local model queries that saved connection with the SQL
    connector's tools. The agent skills an admin has switched on are offered
    when the message is about them: a chart, or something to remember. Any of
    these keeps the reply on a local model, as do the memories it is given.
    """
    messages = [m.model_dump() for m in body.messages]
    skills = request.app.state.skills.get()
    asked = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    scope = body.workspace or GENERAL
    sources, context, parts = None, [], []
    try:
        if body.workspace:
            try:
                passages, sources = await request.app.state.workspaces.context(body.workspace, messages)
            except WorkspaceError as exc:
                raise HTTPException(404, str(exc)) from exc
            if passages:
                context.append(passages)
        if body.database:
            if not skills["sql"]:
                raise HTTPException(403, "Answering from databases is turned off on the Admin panel.")
            connection = request.app.state.sql_store.get(body.database)
            if connection is None:
                raise HTTPException(404, f"There is no saved database connection named '{body.database}'.")
            sql = registry.config.sql
            parts.append(SqlTools([connection], max_rows=sql.max_rows, timeout=sql.timeout))
        if skills["charts"] and (body.database or body.workspace or wants_chart(asked)):
            parts.append(ChartTool())
        if skills["memory"] and skills["memory_from_chat"] and wants_memory(asked):
            parts.append(MemoryTools(request.app.state.memory, scope))
        tools = Toolbox(parts)

        if tools:
            source = "database" if body.database else "skills"
            entry, route = await select_tool_model(registry, body.model, registry.config.sql.model, source)
            fallbacks = []
            context.append(tools.system_prompt())
        elif body.workspace:
            entry, route = await select_local_model(registry, auto, body.model, messages)
            fallbacks = []
        else:
            entry, route, fallbacks = await select_model(registry, auto, body.model, messages)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Memories go to a model on this machine only (a local model never falls back to the cloud).
    if skills["memory"] and entry.local:
        name = request.app.state.workspaces.store.get(body.workspace).meta()["name"] if body.workspace else ""
        remembered = request.app.state.memory.prompt(scope, name)
        if remembered:
            context.append(remembered)
    if context:
        system = "\n\n".join(part for part in (entry.system, *context) if part)
        messages = [{"role": "system", "content": system}, *messages]

    async def events() -> AsyncIterator[str]:
        try:
            if route:
                yield json.dumps(route) + "\n"
            if sources:
                yield json.dumps(sources) + "\n"
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
