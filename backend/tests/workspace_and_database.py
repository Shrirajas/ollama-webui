"""Knowledge (a workspace) and a Database picked together in one chat. Run from backend/:

    .venv/bin/python tests/workspace_and_database.py

No server, no model: the app as main.py builds it, with a stand-in llama-server that records what it is sent.
Live, gemma answered "how many orders were cancelled?" with "the documents do not cover it" without querying
the database: any workspace rule in the system message won. So with a database picked the system message has no
workspace rule; the two-source rule travels inside <context>, after the passages (measured 3/3 on gemma4).
"""
import json, sqlite3, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi.testclient import TestClient
from llamacpp_pipeline import Registry, load_config
from ollama_pipeline import OllamaUnavailable, Router
from app import model_settings, tools
from app.auth import configure, init_db
from app.auth.db import create_user, db
from app.main import create_app, init_features
from sql_connector import Connection

results = []
def check(name, ok):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name)


tmp = Path(tempfile.mkdtemp())
init_db(tmp / "auth.db")
configure(b"both-test-secret", 1)
model_settings.init()
tools.init()
with db() as c:
    create_user(c, "admin@test.com", "admin", "password-admin", "admin")

config = load_config()
registry = Registry(config)
client = registry.client("llamacpp")
calls = []


async def tags():
    return [{"name": m.model, "digest": m.model, "size": None, "details": {}} for m in config.models]


async def stream(model, messages, options=None, tools=None):
    calls.append(([dict(m) for m in messages], tools or []))
    yield {"message": {"role": "assistant", "content": "the answer"}, "done": False}
    yield {"message": {"role": "assistant", "content": ""}, "done": True}


async def no_embedding(model, texts):
    raise OllamaUnavailable("no embedding server in this test")

client.list_models, client.chat_stream = tags, stream
registry.client("embed").embed = no_embedding

app = create_app()
app.state.registry, app.state.router = registry, Router(config.router)
init_features(app, tmp / "data")
admin = TestClient(app)
assert admin.post("/api/auth/signin", json={"email": "admin@test.com", "password": "password-admin"}).status_code == 200

ws = admin.post("/api/workspaces", json={"name": "Handbook"}).json()["id"]
r = admin.post(f"/api/workspaces/{ws}/documents",
               files={"file": ("policy.txt", b"Orders can be cancelled free of charge before they are shipped.")})
assert r.status_code == 200, r.text
shop = tmp / "shop.db"
with sqlite3.connect(shop) as s:
    s.execute("CREATE TABLE orders (id INTEGER, status TEXT)")
    s.execute("INSERT INTO orders VALUES (1, 'Cancelled'), (2, 'Delivered')")
app.state.sql_store.save(Connection(id="shop", engine="sqlite", path=str(shop)))


def ask(**picked):
    calls.clear()
    r = admin.post("/api/chat", json={"model": "gemma", "messages": [
        {"role": "user", "content": "How many orders were cancelled?"}], **picked})
    assert r.status_code == 200, r.text
    messages, offered = calls[0]
    system = " ".join(m["content"] for m in messages if m["role"] == "system")
    question = messages[-1]["content"]
    return system, question, {t["function"]["name"] for t in offered}, [json.loads(l) for l in r.text.splitlines() if l.strip()]


system, question, offered, events = ask(workspace=ws, database="shop")
context = question.split("</context>")[0]
check("both: the database tools are offered", {"list_tables", "get_table_schema", "query"} <= offered)
check("both: the workspace's passages still come (Sources)", any(e["type"] == "sources" for e in events))
check("both: the two-source rule is inside <context>, after the passages",
      "[1] policy.txt" in context and context.index("[1] policy.txt") < context.index("query the database first"))
check("both: no workspace rule in the system message ('base the answer on them' stops the database calls)",
      "Base the answer on them" not in system and "query the database first" not in system)

system, question, offered, _ = ask(workspace=ws)
check("knowledge alone: the workspace rule in the system message, no database rule, no database tools",
      "Base the answer on them" in system and "query the database" not in question and not offered)
system, question, offered, _ = ask(database="shop")
check("database alone: tools offered, no workspace rule", offered and "Base the answer on them" not in system
      and "query the database" not in question and "<context>" not in question)

print(f"\n{sum(results)}/{len(results)} passed")
if all(results):
    print("workspace_and_database: all passed")
sys.exit(0 if all(results) else 1)
