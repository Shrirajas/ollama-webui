"""Files a tool makes (B3): only the person who made one may download it, and old ones are removed. Run from backend/:

    .venv/bin/python tests/tool_files.py

No server, no model, no GPU. The real bundled spreadsheet tool is installed through the admin route and
run by a chat (a stand-in model asks for it), as user A. Files go to a temporary DOCUMENTS_DIR.
"""
import json, os, re, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
TMP = tempfile.TemporaryDirectory()
os.environ["DOCUMENTS_DIR"] = str(Path(TMP.name) / "documents")  # tool files go to <this>/../tool_files

from fastapi import HTTPException
from fastapi.testclient import TestClient
from llamacpp_pipeline import Registry, load_config
from ollama_pipeline import Router
from app import model_settings, tools
from app.auth import current_user, init_db
from app.auth.db import create_user, db
from app.main import create_app, init_features

results = []
def check(name, ok, got=None):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok or got is None else f"   got: {got!r}"), flush=True)


init_db(Path(TMP.name) / "auth.db")
model_settings.init()
tools.init()
FILES = tools.FILES_DIR
KEEP_HOURS = 24  # documents.keep_hours in models.yaml

config = load_config()
registry = Registry(config)
client = registry.client("llamacpp")
names = [m.model for m in config.models]
script = []  # what the stand-in model does on its next reply: a list of (tool, arguments) or None


async def tags():
    return [{"name": n, "digest": n, "size": None, "details": {}} for n in names]


async def stream(model, messages, options=None, tools=None):
    asks = script.pop(0) if script and tools and not any(m.get("role") == "tool" for m in messages) else None
    if asks:
        yield {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": n, "arguments": a}} for n, a in asks]}, "done": False}
    else:
        tool_text = next((m["content"] for m in messages if m.get("role") == "tool"), "")
        yield {"message": {"role": "assistant", "content": tool_text or "the answer"}, "done": False}
    yield {"message": {"role": "assistant", "content": ""}, "done": True}

client.list_models, client.chat_stream = tags, stream

app = create_app()
app.state.registry, app.state.router = registry, Router(config.router)
init_features(app, Path(tempfile.mkdtemp()))  # databases, workspaces, chat files, skills
admin = {"id": "a", "email": "admin@test.com", "name": "A", "role": "admin"}
who = {"user": admin}


def signed_in():
    if who["user"] is None:
        raise HTTPException(401, "Not signed in.")
    return who["user"]

app.dependency_overrides[current_user] = signed_in
c = TestClient(app)
A = "/api/admin/tools"

with db() as conn:
    ua = create_user(conn, "alice@test.com", "Alice", "Alice-pass-123", "user")
    ub = create_user(conn, "bob@test.com", "Bob", "Bob-pass-1234", "user")
alice = {**ua, "role": "user"}
bob = {**ub, "role": "user"}

SRC = (Path(__file__).parents[2] / "tools" / "generate_spreadsheets.py").read_text()
r = c.post(f"{A}/create", json={"id": "generate_spreadsheets", "name": "Generate Spreadsheets", "content": SRC,
                                "meta": {"description": "Excel"}})
check("the bundled spreadsheet tool installs (admin)", r.status_code == 200, r.text[:200])
SPEC = json.dumps({"title": "Budget", "sheets": [{"name": "Sheet1", "kind": "table", "table": {
    "name": "T", "columns": [{"key": "x", "header": "X"}], "rows": [{"x": 1}]}}]})
LINK = re.compile(r"/api/tools/files/[A-Za-z0-9_.-]+\.xlsx")


def make_file(user):
    """`user` asks a chat for a workbook; returns the link the tool answered with."""
    script[:] = [[("generate_spreadsheet", {"content": SPEC})]]
    who["user"] = user
    body = c.post("/api/chat", json={"model": "llama", "tool_ids": ["generate_spreadsheets"],
                                     "messages": [{"role": "user", "content": "make a workbook"}]}).text
    who["user"] = admin
    m = LINK.search(body)
    return m.group(0) if m else None


link_a = make_file(alice)
check("alice's chat makes a workbook and gives its link", link_a is not None)
stored_a = link_a.rsplit("/", 1)[-1] if link_a else ""
check("the file is on disk", any(p.name == stored_a for p in FILES.rglob("*")) if link_a else False)

who["user"] = alice
check("alice downloads her own file (200)", bool(link_a) and c.get(link_a).status_code == 200)
who["user"] = bob
r = c.get(link_a) if link_a else None
check("bob GET alice's file -> 404", r is not None and r.status_code == 404, r.status_code if r else None)
who["user"] = admin
check("admin GET alice's file -> 404 too (no owner, no access)", bool(link_a) and c.get(link_a).status_code == 404)
who["user"] = None
check("signed out -> 401", bool(link_a) and c.get(link_a).status_code == 401)
who["user"] = admin

# Old files go: one made long ago is removed by the next file a tool makes.
link_old = make_file(bob)
old = next((p for p in FILES.rglob("*.xlsx") if p.name == (link_old or "").rsplit("/", 1)[-1]), None)
check("bob's file is on disk", old is not None)
if old:
    past = time.time() - (KEEP_HOURS + 1) * 3600
    os.utime(old, (past, past))
link_new = make_file(alice)
check("a file older than keep_hours is removed", old is None or not old.exists(), str(old))
check("a new file is kept", bool(link_new) and any(p.name == link_new.rsplit("/", 1)[-1] for p in FILES.rglob("*.xlsx")))
check("alice's file from just now is kept", bool(link_a) and any(p.name == stored_a for p in FILES.rglob("*.xlsx")))

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
