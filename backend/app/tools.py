"""Tools an admin writes in Python and the chat model may call (Open WebUI's Workspace -> Tools).

A tool is a `class Tools:` whose public methods become function specs (from their type hints
and docstrings) that the model calls in the chat (native function calling, the loop is in
ollama_pipeline/pipeline.py). Copied from Open WebUI: utils/plugin.py (loader), utils/tools.py
(specs, get_tools), routers/tools.py (routes). Stored in auth.db, table `tool`.

SECURITY: the code is exec'd in this process, so it can do anything the server can. Only admins
create, change or configure it (`admin`, mounted at /api/admin/tools); users only list the
enabled tools (`router`, /api/tools) and send ids, of which only enabled ones are ever run."""
import asyncio
import functools
import inspect
import json
import logging
import re
import sys
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, get_args, get_type_hints

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, create_model

from .auth import admin_user, current_user
from .auth.db import db
from .settings import settings

log = logging.getLogger(__name__)

admin = APIRouter(dependencies=[Depends(admin_user)])  # every route: admins only (this reaches exec)
router = APIRouter()  # any signed-in user

TIMEOUT = 30  # seconds one tool call may take
MAX_RESULT = 4000  # characters of a result the model gets
# Files a tool makes for the user (a workbook, a deck), in FILES_DIR/<user id>/<name> (the tool gets the id as
# `__user__["id"]`); served by GET /api/tools/files/<name> to that user only. Deleted after KEEP_HOURS
# (main.py sets it from documents.keep_hours).
FILES_DIR = settings.documents_dir.parent / "tool_files"
KEEP_HOURS = 24


def purge_files() -> None:
    """Deletes files older than KEEP_HOURS, and user folders left empty. Anything loose in FILES_DIR (made
    before files were per user) is no longer served and goes the same way.
    shortcut: walks the whole folder on every tool chat and file download; throttle it if the folder grows."""
    if not FILES_DIR.is_dir():
        return
    cutoff = time.time() - KEEP_HOURS * 3600
    for path in sorted(FILES_DIR.rglob("*"), reverse=True):  # files before the folders that hold them
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.rmdir() if path.is_dir() else path.unlink()
        except OSError:
            pass  # a folder that is not empty yet, or a file taken away meanwhile
# Sync tools run here, not in asyncio's default pool (Von routing and invoice reading use that one):
# a tool that hangs keeps its thread, and enough of them would stall those.
_pool = ThreadPoolExecutor(8, thread_name_prefix="tool")

_SCHEMA = """CREATE TABLE IF NOT EXISTS tool (id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
specs TEXT NOT NULL, meta TEXT NOT NULL, valves TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
user_id TEXT, created_at INTEGER, updated_at INTEGER)"""

_cache: dict[str, tuple[str, Any]] = {}  # id -> (content, Tools instance)


def init() -> None:
    with db() as c:
        c.execute(_SCHEMA)


# --- loading (utils/plugin.py) ---

def extract_frontmatter(content: str) -> dict[str, str]:
    """`key: value` lines of a leading triple-quoted docstring (title, author, version, ...)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != '"""':
        return {}
    frontmatter = {}
    for line in lines[1:]:
        if '"""' in line:
            break
        match = re.match(r"^\s*([a-z_]+):\s*(.*)\s*$", line, re.IGNORECASE)
        if match:
            frontmatter[match.group(1).strip()] = match.group(2).strip()
    return frontmatter


def load_module(tool_id: str, content: str) -> Any:
    """Runs the code in a fresh module and returns its `Tools()`. Raises whatever the code raises."""
    name = f"tool_{tool_id}_{uuid.uuid4().hex}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = f"<tool {tool_id}>"
    sys.modules[name] = module  # for dataclasses / pydantic that look their module up while the class is made
    try:
        exec(compile(content, f"<tool {tool_id}>", "exec"), module.__dict__)
        if not hasattr(module, "Tools"):
            raise Exception("No Tools class found in the module")
        return module.Tools()
    finally:
        sys.modules.pop(name, None)


def _instance(tool_id: str, content: str) -> Any:
    cached = _cache.get(tool_id)
    if cached is None or cached[0] != content:
        cached = _cache[tool_id] = (content, load_module(tool_id, content))
    return cached[1]


# --- specs from type hints and docstrings (utils/tools.py) ---

def parse_description(docstring: str | None) -> str:
    lines = []
    for line in (docstring or "").strip().split("\n"):
        line = line.strip()
        if line.startswith((":param", ":return")):
            break
        lines.append(line)
    return "\n".join(lines)


def parse_docstring(docstring: str | None) -> dict[str, str]:
    """`:param name: text` lines -> {name: text}; continuation lines are joined, `__x` params skipped."""
    out: dict[str, str] = {}
    current = None
    for line in (docstring or "").splitlines():
        line = line.strip()
        match = re.match(r":param (\w+):\s*(.*)", line)
        if match:
            name, text = match.groups()
            current = None if name.startswith("__") else name
            if current:
                out[current] = text
        elif line.startswith(":"):
            current = None
        elif current and line:
            out[current] = "\n".join(filter(None, [out[current], line]))
    return out


def convert_function_to_pydantic_model(func) -> type[BaseModel]:
    hints = get_type_hints(func)
    params = parse_docstring(func.__doc__)
    fields = {}
    for name, param in inspect.signature(func).parameters.items():
        default = param.default if param.default is not param.empty else ...
        hint = hints.get(name, Any)
        fields[name] = (hint, Field(default, description=params[name])) if params.get(name) else (hint, default)
    model = create_model(func.__name__, **fields)  # pydantic leaves `__user__` and the like out by itself
    model.__doc__ = parse_description(func.__doc__)
    return model


def clean_properties(schema: dict) -> None:
    if not isinstance(schema, dict):
        return
    if "anyOf" in schema:
        non_null = [t for t in schema["anyOf"] if t.get("type") != "null"]
        if len(non_null) == 1:
            schema.update(non_null[0])
            del schema["anyOf"]
        else:
            schema["anyOf"] = non_null
    if "default" in schema and schema["default"] is None:
        del schema["default"]
    if "type" not in schema and "anyOf" not in schema and "properties" not in schema:
        schema["type"] = "string"
    for prop in (schema.get("properties") or {}).values():
        clean_properties(prop)
    if "items" in schema:
        clean_properties(schema["items"])


def _drop_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _drop_titles(v) for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    return [_drop_titles(v) for v in schema] if isinstance(schema, list) else schema


def function_spec(func) -> dict:
    """OpenAI function spec, as langchain's convert_to_openai_function makes it in Open WebUI.
    shortcut: nested pydantic models stay as $defs/$ref, not inlined; inline them if a tool needs one."""
    model = convert_function_to_pydantic_model(func)
    schema = _drop_titles(model.model_json_schema())
    parameters = {"type": "object", "properties": schema.get("properties") or {}, "required": schema.get("required") or []}
    if schema.get("$defs"):
        parameters["$defs"] = schema["$defs"]
    clean_properties(parameters)
    return {"name": func.__name__, "description": model.__doc__ or "", "parameters": parameters}


def get_functions_from_tool(tool: object) -> list:
    return [
        getattr(tool, name) for name in dir(tool)
        if callable(getattr(tool, name)) and not name.startswith("_") and not inspect.isclass(getattr(tool, name))
    ]


def get_tool_specs(tool: object) -> list[dict]:
    return [function_spec(f) for f in get_functions_from_tool(tool)]


# --- calling (utils/tools.py get_tools, get_async_tool_function_and_apply_extra_params) ---

def _coerce(func, kwargs: dict) -> dict:
    """Models often send "3" for an int parameter, or 3 for a str one."""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}
    for name, value in kwargs.items():
        if name not in sig.parameters or value is None:
            continue
        annotation = hints.get(name, sig.parameters[name].annotation)
        args = set(get_args(annotation))
        if isinstance(value, str) and (annotation is int or args == {int, type(None)}):
            kwargs[name] = int(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and (annotation is str or args == {str, type(None)}):
            kwargs[name] = str(value)
    return kwargs


def _result_text(result: Any) -> str:
    if result is None or result == "":
        return ""
    text = json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, (dict, list)) else str(result)
    return text[:MAX_RESULT]


def _caller(func, spec: dict, extra: dict):
    allowed = set(spec["parameters"]["properties"])
    extra = {k: v for k, v in extra.items() if k in inspect.signature(func).parameters}

    async def call(**arguments) -> str:
        try:
            kwargs = _coerce(func, {k: v for k, v in arguments.items() if k in allowed})
            if inspect.iscoroutinefunction(func):
                result = await asyncio.wait_for(func(**kwargs, **extra), TIMEOUT)
            else:
                # shortcut: a sync tool that hangs keeps its thread after the timeout (Python cannot kill one);
                # 8 hung calls fill the pool and later tool calls wait, then time out. Restart to clear it.
                job = asyncio.get_running_loop().run_in_executor(_pool, functools.partial(func, **kwargs, **extra))
                result = await asyncio.wait_for(job, TIMEOUT)
        except asyncio.TimeoutError:
            return f"Error: the tool took longer than {TIMEOUT} s."
        except Exception as exc:  # the tool's own bug must not end the chat
            return f"Error: {exc}"
        return _result_text(result)

    return call


def get_tools(tool_ids: list[str], user: dict) -> dict[str, dict]:
    """{function name: {"spec", "call"}} for the ids that exist and are enabled; any other id is dropped."""
    ids = list(dict.fromkeys(i for i in tool_ids if i.isidentifier()))
    if not ids:
        return {}
    purge_files()  # a chat that may make a file is the moment to remove the old ones
    with db() as c:
        rows = c.execute(
            f"SELECT * FROM tool WHERE enabled = 1 AND id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
    user_info = {k: user.get(k) for k in ("id", "email", "name", "role")}
    tools: dict[str, dict] = {}
    for row in rows:
        try:
            instance = _instance(row["id"], row["content"])
            if hasattr(instance, "valves") and hasattr(instance, "Valves"):
                instance.valves = instance.Valves(**json.loads(row["valves"]))
        except Exception as exc:
            log.warning("Tools: %s could not be loaded, left out (%s)", row["id"], exc)
            continue
        for spec in json.loads(row["specs"]):
            func = getattr(instance, spec["name"], None)
            if not callable(func):
                continue
            params = spec["parameters"]
            params["properties"] = {k: v for k, v in params.get("properties", {}).items() if not k.startswith("__")}
            params["required"] = [k for k in params.get("required", []) if not k.startswith("__")]
            name = spec["name"]
            while name in tools:  # two tools with a function of the same name
                name = f"{row['id']}_{name}"
            spec["name"] = name
            tools[name] = {"spec": spec, "call": _caller(func, spec, {"__id__": row["id"], "__user__": user_info})}
    return tools


# --- routes (routers/tools.py) ---

class ToolForm(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)
    meta: dict = Field(default_factory=dict)


def _row(tool_id: str) -> dict:
    with db() as c:
        row = c.execute("SELECT * FROM tool WHERE id = ?", (tool_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such tool.")
    return dict(row)


def _full(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "content": row["content"], "specs": json.loads(row["specs"]),
        "meta": json.loads(row["meta"]), "enabled": bool(row["enabled"]), "updated_at": row["updated_at"],
    }


def _load(tool_id: str, form: ToolForm) -> tuple[Any, list[dict], dict]:
    """Loads the code; any failure is a 400 with its text, as Open WebUI answers."""
    try:
        instance = load_module(tool_id, form.content)
        specs = get_tool_specs(instance)
    except Exception as exc:
        log.warning("Tools: %s did not load: %s", tool_id, exc)
        raise HTTPException(400, str(exc) or type(exc).__name__) from None
    meta = {"description": str(form.meta.get("description") or "")[:2000], "manifest": extract_frontmatter(form.content)}
    return instance, specs, meta


@admin.get("")
def list_tools():
    with db() as c:
        rows = c.execute("SELECT id, name, meta, enabled, updated_at FROM tool ORDER BY updated_at DESC").fetchall()
    return [{"id": r["id"], "name": r["name"], "meta": json.loads(r["meta"]), "enabled": bool(r["enabled"]),
             "updated_at": r["updated_at"]} for r in rows]


@admin.post("/create")
def create_tool(form: ToolForm, user: dict = Depends(admin_user)):
    if not form.id.isidentifier():
        raise HTTPException(400, "Only alphanumeric characters and underscores are allowed in the id")
    tool_id = form.id.lower()
    with db() as c:
        if c.execute("SELECT 1 FROM tool WHERE id = ?", (tool_id,)).fetchone():
            raise HTTPException(400, "A tool with this id already exists.")
    instance, specs, meta = _load(tool_id, form)
    now = int(time.time())
    with db() as c:
        c.execute(
            "INSERT INTO tool (id, name, content, specs, meta, valves, enabled, user_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '{}', 1, ?, ?, ?)",
            (tool_id, form.name, form.content, json.dumps(specs), json.dumps(meta), user["id"], now, now),
        )
    _cache[tool_id] = (form.content, instance)
    return _full(_row(tool_id))


@admin.get("/{tool_id}")
def get_tool(tool_id: str):
    return _full(_row(tool_id))


@admin.post("/{tool_id}/update")
def update_tool(tool_id: str, form: ToolForm):
    _row(tool_id)
    instance, specs, meta = _load(tool_id, form)
    with db() as c:
        c.execute(
            "UPDATE tool SET name = ?, content = ?, specs = ?, meta = ?, updated_at = ? WHERE id = ?",
            (form.name, form.content, json.dumps(specs), json.dumps(meta), int(time.time()), tool_id),
        )
    _cache[tool_id] = (form.content, instance)
    return _full(_row(tool_id))


@admin.patch("/{tool_id}/enabled")
def set_enabled(tool_id: str, body: dict):
    _row(tool_id)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(422, "enabled must be true or false.")
    with db() as c:
        c.execute("UPDATE tool SET enabled = ? WHERE id = ?", (int(enabled), tool_id))
    return {"id": tool_id, "enabled": enabled}


@admin.delete("/{tool_id}")
def delete_tool(tool_id: str):
    _row(tool_id)
    with db() as c:
        c.execute("DELETE FROM tool WHERE id = ?", (tool_id,))
    _cache.pop(tool_id, None)
    return {"id": tool_id, "deleted": True}


def _valves_class(row: dict):
    try:
        return getattr(_instance(row["id"], row["content"]), "Valves", None)
    except Exception as exc:
        raise HTTPException(400, f"The tool does not load: {exc}") from None


@admin.get("/{tool_id}/valves")
def get_valves(tool_id: str):
    return json.loads(_row(tool_id)["valves"])


@admin.get("/{tool_id}/valves/spec")
def get_valves_spec(tool_id: str):
    valves = _valves_class(_row(tool_id))
    return valves.model_json_schema() if valves else None


@admin.post("/{tool_id}/valves/update")
def update_valves(tool_id: str, body: dict):
    valves = _valves_class(_row(tool_id))
    if valves is None:
        raise HTTPException(404, "This tool has no valves.")
    try:
        data = valves(**{k: v for k, v in body.items() if v is not None}).model_dump(exclude_unset=True)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from None
    with db() as c:
        c.execute("UPDATE tool SET valves = ?, updated_at = ? WHERE id = ?", (json.dumps(data), int(time.time()), tool_id))
    return data


def enabled_ids() -> set[str]:
    with db() as c:
        return {r["id"] for r in c.execute("SELECT id FROM tool WHERE enabled = 1")}


@router.get("")
def user_tools():
    """What the chat's Tools menu offers: enabled tools, without code or valves."""
    with db() as c:
        rows = c.execute("SELECT id, name, meta FROM tool WHERE enabled = 1 ORDER BY name").fetchall()
    return [{"id": r["id"], "name": r["name"], "description": json.loads(r["meta"]).get("description", "")} for r in rows]


@router.get("/files/{name}")
def tool_file(name: str, user: dict = Depends(current_user)):
    """A file a tool made for the signed-in user: FILES_DIR/<their id>/<name>. Someone else's file (or an
    admin asking for it) is 404, as if it did not exist. The name's random part is only the second lock."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}", name) or ".." in name:
        raise HTTPException(404, "No such file.")
    purge_files()
    path = FILES_DIR / user["id"] / name
    if not path.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(path, filename=name)
