import base64
import json
from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import Any

from agent_skills import ChartTool, MemoryTools, Toolbox, wants_chart, wants_memory
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from document_engine.invoices import read_file as read_pages  # a file -> page pictures
from fastapi.responses import StreamingResponse
from ollama_pipeline import (
    ModelNotFound,
    OllamaUnavailable,
    PipelineError,
    Registry,
    Router,
    UnsupportedDocument,
    budget_for,
    chat_stream,
    cut_middle,
    estimate,
    picker_models,
    select_local_model,
    select_model,
    select_tool_model,
    todays_theme,
)
from sql_connector import Connection, SheetError, SqlTools, as_markdown, describe, import_file, is_spreadsheet
from starlette.concurrency import run_in_threadpool
from workspace_engine import ChatFile, ChatFiles, WorkspaceError, WorkspaceService, wants_whole

from .auth import current_user
from .model_settings import EnabledOnly, apply as with_settings, is_enabled, load as load_settings
from .schemas import ChatRequest
from .tools import enabled_ids as enabled_tools, get_tools
from .workspaces.routes import read_file

# A scanned PDF goes to the vision model as pictures, one per page; each costs context, so only the first pages.
MAX_SCAN_PAGES = 8
SCAN_SIDE = 1400
MIN_PDF_TEXT = 200  # as the Invoices tab's MIN_TEXT: under this a PDF's text is a header, not the document
# A spreadsheet with at most this many cells is small enough to give the model whole, as a table.
# A bigger one is queried with SQL: the model asks for sums and rows, the database does the counting.
SMALL_SHEET_CELLS = 600
TABLE_ABOUT_TOKENS = 1500  # what the model is told about one spreadsheet's tables, at most
NOTE_PROMPT_TOKENS = 600  # the note-taking instructions and the note itself, beside one part of a file

router = APIRouter()
public = APIRouter()  # no login needed: monitoring calls /api/health


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_router(request: Request) -> Router:
    return request.app.state.router


@public.get("/health")
async def health(registry: Registry = Depends(get_registry)):
    hosts = await registry.health()
    return {"status": "ok" if any(h["ok"] for h in hosts.values()) else "degraded", "hosts": hosts}


@router.get("/models")
async def models(registry: Registry = Depends(get_registry), auto: Router = Depends(get_router)):
    default, entries = await picker_models(EnabledOnly(registry), auto)  # models an admin turned off are left out
    tools_on = enabled_tools()
    for m in entries:  # each model's default tools, those still on
        m["tool_ids"] = [t for t in load_settings(m["id"]).get("tool_ids", []) if t in tools_on]
    return {"default": default, "models": entries}


@router.get("/theme")
async def theme(registry: Registry = Depends(get_registry)):
    today = todays_theme(registry.config.themes)
    if today is None:
        raise HTTPException(404, "No themes configured in ollama/config/themes.yaml.")
    return {"title": today.title, "prompt": today.prompt}


@router.post("/files")
async def add_file(request: Request, file: UploadFile):
    """Keeps a file attached in the chat on this machine and says what it is; its text is not sent back.

    The chat's messages then carry only the file's id, and each question gets the part of the
    file it needs (workspace_engine.ChatFiles). A spreadsheet is loaded into SQLite tables the
    model queries. A scanned PDF comes back as page pictures for the vision model, as before.
    """
    files: ChatFiles = request.app.state.chatfiles
    name = PurePath(file.filename or "file").name
    megabytes = request.app.state.registry.config.workspaces.max_upload_mb
    data = await file.read(megabytes * 1024 * 1024 + 1)
    if len(data) > megabytes * 1024 * 1024:
        raise HTTPException(413, f"{name} is over {megabytes} MB")
    pdf = data.startswith(b"%PDF-")
    file_id, folder = files.new()
    try:
        if is_spreadsheet(name):
            tables = await run_in_threadpool(import_file, name, data, folder / "tables.db")
            small = sum(t["rows"] * len(t["columns"]) for t in tables) <= SMALL_SHEET_CELLS
            text = await run_in_threadpool(as_markdown, folder / "tables.db", tables) if small else describe(tables, name)
            kept = await files.add(file_id, folder, name, data, [("", text)], tables=tables, query=not small,
                                   truncated=any(t["truncated"] for t in tables))
        else:
            try:
                sections, truncated, pages = await run_in_threadpool(read_file, name, data)
            except UnsupportedDocument:
                if not pdf:
                    raise
                sections = []  # no text layer: read as pictures below
            # shortcut: judged on the whole file; a long PDF with a few scanned pages among real text is read as text only.
            if pdf and len("".join("".join(text for _, text in sections).split())) < MIN_PDF_TEXT:
                files.discard(file_id)
                return await run_in_threadpool(scanned_pdf, name, data)  # only a print header or a few words
            kept = await files.add(file_id, folder, name, data, sections, truncated=truncated, pages=pages)
    except (UnsupportedDocument, SheetError) as exc:
        files.discard(file_id)
        raise HTTPException(415, str(exc)) from exc
    except ImportError as exc:
        files.discard(file_id)
        raise HTTPException(500, f"Reading {name} needs the {exc.name} package on the server: run `pip install -r requirements.txt`.") from exc
    except BaseException:
        files.discard(file_id)
        raise
    return kept.public()


def scanned_pdf(name: str, data: bytes) -> dict:
    """No text in the PDF: send its pages as pictures (base64 JPEG) for the vision model to read."""
    try:
        read = read_pages(name, data, MAX_SCAN_PAGES, SCAN_SIDE)
    except UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc
    pictures = [base64.b64encode(p.image).decode() for p in read.pages if p.image]
    if not pictures:
        raise HTTPException(415, f"{name} has no readable pages")
    note = f"{name} is a scan: its pages are read as pictures by the vision model."
    if read.total > len(pictures):
        note += f" Only the first {len(pictures)} of {read.total} pages are attached."
    return {"name": name, "content": "", "truncated": False, "images": pictures, "note": note}


def _files(store: ChatFiles, messages: list[dict[str, Any]]) -> tuple[list[ChatFile], list[str]]:
    """The conversation's files kept here, the newest turn's first; and the names of those no longer kept."""
    kept, gone, seen = [], [], set()
    for message in reversed(messages):
        for ref in message.get("files") or []:
            if not ref.get("id") or ref["id"] in seen:
                continue
            seen.add(ref["id"])
            file = store.get(ref["id"])
            if file:
                kept.append(file)
            else:
                gone.append(ref.get("name") or "a file")
    return kept, gone


def _without_file_text(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages with each kept file reduced to a mention: its text comes with the question, as much as fits.
    Files with their text in the message (saved chats from before) stay as they are."""
    out = []
    for message in messages:
        refs = message.get("files") or []
        names = [f["name"] for f in refs if f.get("id")]
        message = {**message, "files": [f for f in refs if not f.get("id")]}
        if names:
            message["content"] = (message.get("content") or "") + ("\n\n" if message.get("content") else "") \
                + "[Attached: " + ", ".join(names) + "]"
        out.append(message)
    return out


def _about(file: ChatFile) -> str:
    """What the model is told about a spreadsheet's tables, kept short."""
    text = describe(file.tables, file.name)
    if estimate(text) > TABLE_ABOUT_TOKENS:
        text = describe([{**t, "sample": []} for t in file.tables], file.name)
    return cut_middle(text, TABLE_ABOUT_TOKENS)


def _shares(prompt: int, workspace: bool, files: bool) -> tuple[int, int]:
    """Tokens for the workspace's passages and for the files' text; the conversation gets the rest."""
    if workspace and files:
        return int(prompt * 0.25), int(prompt * 0.4)
    if workspace:
        return int(prompt * 0.45), 0
    if files:
        return 0, int(prompt * 0.6)
    return 0, 0


def _assemble(messages: list[dict[str, Any]], entry, stable: list[str], turn: list[str]) -> list[dict[str, Any]]:
    """The messages as sent: what stays the same from turn to turn in the system message (llama-server
    reuses that part of its cache), and what belongs to this question with the question itself."""
    out = [dict(m) for m in messages]
    if turn:
        last = max(i for i, m in enumerate(out) if m["role"] == "user")
        out[last]["content"] = "<context>\n" + "\n\n".join(turn) + "\n</context>\n\n" + (out[last].get("content") or "")
    if stable:
        given = [m.get("content") or "" for m in out if m["role"] == "system"] or [entry.system]
        system = "\n\n".join(part for part in (*given, *stable) if part)
        out = [{"role": "system", "content": system}, *[m for m in out if m["role"] != "system"]]
    return out


def _as_tools(toolbox: Toolbox) -> dict[str, dict]:
    """The built-in skills in the shape chat_stream takes, beside the admin's Tools."""
    def call(name: str):
        async def run(**arguments):
            return await toolbox.run(name, arguments)
        return run
    return {d["function"]["name"]: {"spec": d["function"], "call": call(d["function"]["name"])} for d in toolbox.definitions}


@router.post("/chat")
async def chat(
    body: ChatRequest, request: Request, registry: Registry = Depends(get_registry), auto: Router = Depends(get_router),
    user: dict = Depends(current_user),
):
    """Streams the reply as newline-delimited JSON events: route, sources, notice, thinking, delta,
    tool_call, tool_progress, tool_result, done, error.

    Everything that comes with a question is fitted to what the model can read at once: a
    workspace's passages, the attached files (whole when they fit, else the passages that match,
    or notes on the whole file when a summary is asked for), and big spreadsheets as SQL tables
    the model queries. Built-in skills an admin switched on are offered when the message needs
    them. Workspaces, files, databases and memories keep the reply on a local model.
    """
    state = request.app.state
    messages = [m.model_dump() for m in body.messages]
    if body.model and not is_enabled(body.model):
        raise HTTPException(403, "This model is turned off by an admin.")
    models = EnabledOnly(registry)
    skills = state.skills.get()
    asked = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    searched = WorkspaceService.question(messages)  # a short follow-up is searched with the question before it
    current = {f.get("id") for f in (messages[-1].get("files") or []) if f.get("id")}
    attached, gone = _files(state.chatfiles, messages)
    tables = [f for f in attached if f.query]
    texts = [f for f in attached if not f.query]
    try:
        parts, connections, about = [], [], {}
        if body.database:
            if not skills["sql"]:
                raise HTTPException(403, "Answering from databases is turned off by an admin.")
            connection = state.sql_store.get(body.database)
            if connection is None:
                raise HTTPException(404, f"There is no saved database connection named '{body.database}'.")
            connections.append(connection)
        for f in tables:
            db_id, n = f.db_id, 2
            while any(c.id == db_id for c in connections):
                db_id, n = f"{f.db_id} ({n})", n + 1
            connections.append(Connection(id=db_id, engine="sqlite", path=str(f.tables_path)))
            about[db_id] = _about(f)
        sql = registry.config.sql
        if connections:
            parts.append(SqlTools(connections, max_rows=sql.max_rows, timeout=sql.timeout, about=about))
        if skills["charts"] and (body.database or tables or wants_chart(asked)):
            parts.append(ChartTool())
        if skills["memory"] and skills["memory_from_chat"] and wants_memory(asked):
            parts.append(MemoryTools(state.memory, user["id"]))
        builtin = Toolbox(parts)
        tools = {**get_tools(body.tool_ids, user), **_as_tools(builtin)}  # only tools that exist and are on

        fallbacks = []
        if builtin:
            entry, route = await select_tool_model(models, body.model, sql.model, "database" if body.database else "skills")
        elif body.workspace or attached:
            entry, route = await select_local_model(models, auto, body.model, messages)
        else:
            entry, route, fallbacks = await select_model(models, auto, body.model, messages)
        entry, fallbacks = with_settings(entry), [with_settings(f) for f in fallbacks]

        # What comes with the question, in the room the model has.
        budget = budget_for(entry.budget, body.options)
        ws_share, file_share = _shares(budget.prompt, bool(body.workspace), bool(texts))
        stable, turn, sources, start = [], [], [], 1
        if body.workspace:
            try:
                rules, passages, event = await state.workspaces.context(body.workspace, messages, ws_share, start,
                                                                          bool(connections))
            except WorkspaceError as exc:
                raise HTTPException(404, str(exc)) from exc
            if rules:
                stable.append(rules)
            if passages:
                turn.append(passages)
            if event:
                sources.append(event)
                start += len(event["items"])
        if builtin:
            stable.append(builtin.system_prompt())
        # Memories go to a model on this machine only (a local model never falls back to the cloud).
        if skills["memory"] and entry.local:
            remembered = state.memory.prompt(user["id"])
            if remembered:
                stable.append(remembered)

        # A summary of a file too long to read at once: it is read whole, in parts, first (in events()).
        reading: list[ChatFile] = []
        if texts and wants_whole(asked):
            big = [f for f in texts if f.tokens > file_share]
            reading = ([f for f in big if f.id in current] or big[:1])[:2]
        others = [f for f in texts if f not in reading]
        if others:
            block, event = await state.chatfiles.context(others, searched, file_share // (3 if reading else 1), start)
            if block:
                turn.append(block)
            if event:
                sources.append(event)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OllamaUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc
    messages = _without_file_text(messages)
    part_tokens = max(1500, min(6000, budget.context - NOTE_PROMPT_TOKENS - 400))

    async def events() -> AsyncIterator[str]:
        try:
            if route:
                yield json.dumps(route) + "\n"
            for event in sources:
                yield json.dumps(event) + "\n"
            if gone:
                it = "it" if len(gone) == 1 else "them"
                yield json.dumps({"type": "notice", "message": (
                    f"{', '.join(gone)}: no longer on the server (attached files are kept for "
                    f"{registry.config.documents.keep_hours} hours). Attach {it} again to ask about {it}.")}) + "\n"
            for file in reading:
                yield json.dumps({"type": "tool_call", "name": "read_document", "arguments": {"file": file.name}}) + "\n"
                notes = None
                async for step in state.chatfiles.read_whole(file, entry, part_tokens, file_share // len(reading)):
                    if step["type"] == "progress":
                        yield json.dumps({"type": "tool_progress", "name": "read_document",
                                          "message": f"Reading {file.name}: {step['done']} of {step['total']} parts read"}) + "\n"
                    else:
                        notes = step
                yield json.dumps({"type": "tool_result", "name": "read_document", "file": file.name,
                                  "result": f"Read {notes['parts']} parts", "parts": notes["parts"],
                                  "skipped": notes["skipped"]}) + "\n"
                turn.append(notes["text"])
            stream = chat_stream(registry, entry, _assemble(messages, entry, stable, turn), body.options, fallbacks,
                                 tools=tools, max_rounds=registry.config.sql.max_rounds if builtin else 5)
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
