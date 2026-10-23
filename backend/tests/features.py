"""Workspaces, Databases, Features and Memory, chat files: who may reach what, and the chat's use of them.
Run from backend/:

    .venv/bin/python tests/features.py

No server, no model: the app as main.py builds it, with REAL logins (users in a temporary auth.db, signed in
through /api/auth/signin), a temporary data folder and a stand-in llama-server whose replies ask for tools.
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
from app.settings import BACKEND_DIR
from app.sql.routes import ConnectionForm, _connection
from sql_connector import Connection

results = []
def check(name, ok, detail=None):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok or detail is None else f"   ({detail})"))


tmp = Path(tempfile.mkdtemp())
init_db(tmp / "auth.db")
configure(b"features-test-secret", 1)
model_settings.init()
tools.init()
with db() as c:
    for who, role in (("admin", "admin"), ("ann", "user"), ("bob", "user")):
        create_user(c, f"{who}@test.com", who, "password-" + who, role)

config = load_config()
registry = Registry(config)
client = registry.client("llamacpp")
names = [m.model for m in config.models]
calls, script = [], []  # what the stand-in was asked; the tool calls each next reply makes


async def tags():
    return [{"name": n, "digest": n, "size": None, "details": {}} for n in names]


async def stream(model, messages, options=None, tools=None):
    calls.append(([dict(m) for m in messages], tools, options or {}))
    asks = script.pop(0) if script and tools else None
    if asks:
        yield {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": n, "arguments": a}} for n, a in asks]}, "done": False}
    else:
        yield {"message": {"role": "assistant", "content": "the answer"}, "done": False}
    yield {"message": {"role": "assistant", "content": ""}, "done": True}

client.list_models, client.chat_stream = tags, stream


async def no_embedding(model, texts):
    raise OllamaUnavailable("no embedding server in this test")

registry.client("embed").embed = no_embedding  # search by words, as without the embedding container

app = create_app()  # mounted exactly as in production; no lifespan, so state is set here
app.state.registry, app.state.router = registry, Router(config.router)
init_features(app, tmp / "data")


def sign_in(who):
    c = TestClient(app)
    r = c.post("/api/auth/signin", json={"email": f"{who}@test.com", "password": "password-" + who})
    assert r.status_code == 200, r.text
    return c, r.json()["token"]


anon = TestClient(app)
admin, _ = sign_in("admin")
ann, _ = sign_in("ann")
bob, _ = sign_in("bob")


def events(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def chat(c, text="hello", **extra):
    calls.clear()
    return c.post("/api/chat", json={"model": "llama", "messages": [{"role": "user", "content": text}], **extra})


def told(i=0):
    return " ".join(m["content"] for m in calls[i][0] if m["role"] == "system")


# ---------------------------------------------------------------- login on every route
OPEN = {("GET", "/api/health"), ("POST", "/api/auth/signin"), ("POST", "/api/auth/signout")}
def every_route(routes):
    # FastAPI 0.142 keeps included routers as wrappers in app.routes; walk into them
    for r in routes:
        if hasattr(r, "effective_candidates"):
            yield from every_route(r.effective_candidates())
        elif getattr(r, "methods", None):
            yield r


checked, unprotected = 0, []
for route in every_route(app.routes):
    path = route.path
    if not path.startswith("/api") or path.startswith("/api/access"):  # n8n's route has its own key (checked below)
        continue
    for method in route.methods - {"HEAD", "OPTIONS"}:
        if (method, path) in OPEN:
            continue
        checked += 1
        r = anon.request(method, path.replace("{", "").replace("}", ""), json={})
        if r.status_code != 401:
            unprotected.append((method, path, r.status_code))
check(f"login: all {checked} /api routes answer 401 without a login (except health, signin, signout)", checked > 50 and not unprotected)
for u in unprotected:
    print("     not 401:", u)
check("login: a made-up token is refused", anon.get("/api/memories", headers={"Authorization": "Bearer abc.def"}).status_code == 401)
check("login: /api/access without its key is off or refused", anon.post("/api/access/users", json={}).status_code in (401, 403, 422, 503))

# ---------------------------------------------------------------- admin-only writes
good_db = tmp / "shop.db"
con = sqlite3.connect(good_db)
con.executescript("create table sales(item text, qty int); insert into sales values ('a',1),('b',2),('c',3);")
con.commit(); con.close()
form = {"name": "Shop", "engine": "sqlite", "path": str(good_db)}

r = admin.post("/api/workspaces", json={"name": "Handbook"})
ws = r.json().get("id", "")
check("workspaces: admin creates one", r.status_code == 200 and ws)
check("workspaces: a user cannot create (403)", ann.post("/api/workspaces", json={"name": "Mine"}).status_code == 403)
check("workspaces: a user cannot rename (403)", ann.post(f"/api/workspaces/{ws}", json={"name": "Hacked"}).status_code == 403)
check("workspaces: a user cannot delete (403)", ann.delete(f"/api/workspaces/{ws}").status_code == 403)
check("workspaces: a user cannot add a document (403)",
      ann.post(f"/api/workspaces/{ws}/documents", files={"file": ("a.txt", b"hello world")}).status_code == 403)
check("workspaces: a user cannot reindex (403)", ann.post(f"/api/workspaces/{ws}/reindex").status_code == 403)
check("workspaces: a user cannot remove a document (403)", ann.delete(f"/api/workspaces/{ws}/documents/1").status_code == 403)
check("workspaces: the name is unchanged", admin.get(f"/api/workspaces/{ws}").json()["name"] == "Handbook")
check("workspaces: a user can list, open and search",
      ann.get("/api/workspaces").status_code == 200 and ann.get(f"/api/workspaces/{ws}").status_code == 200
      and ann.post(f"/api/workspaces/{ws}/search", json={"question": "x"}).status_code == 200)

paragraphs = "\n\n".join(f"Paragraph {i}. The vault code for room {i} is {i * 7919 % 10007}. " + "Filler text about nothing. " * 12
                         for i in range(400))
r = admin.post(f"/api/workspaces/{ws}/documents", files={"file": ("manual.txt", paragraphs.encode())})
check("workspaces: admin adds a document (streamed)", r.status_code == 200 and [e for e in events(r) if e.get("type") == "added"])
r = ann.post(f"/api/workspaces/{ws}/search", json={"question": "vault code room 77"})
check("workspaces: search finds passages", r.status_code == 200 and r.json()["hits"])
check("workspaces: ids that are paths are 404/405",
      ann.get("/api/workspaces/..%2f..%2fauth").status_code in (404, 405) and ann.get("/api/workspaces/../../auth").status_code in (404, 405))
r = admin.post(f"/api/workspaces/{ws}/documents", files={"file": ("../../evil.txt", b"some text for evil")})
check("workspaces: an upload name with a path is only a name",
      '"added"' in r.text and "evil.txt" not in [p.name for p in tmp.parent.iterdir()] and not (tmp / "evil.txt").exists())
r = admin.post(f"/api/workspaces/{ws}/documents", files={"file": ("pic.png", b"\x89PNG....")})
check("workspaces: a picture is refused with a message", "picture" in r.text.lower())

r = admin.post("/api/sql/connections", json=form)
check("sql: admin saves a SQLite connection", r.status_code == 200 and r.json().get("id") == "shop")
check("sql: a user cannot save (403)", ann.post("/api/sql/connections", json={**form, "name": "Mine"}).status_code == 403)
check("sql: a user cannot test (403)", ann.post("/api/sql/connections/test", json=form).status_code == 403)
check("sql: a user cannot delete (403)", ann.delete("/api/sql/connections/shop").status_code == 403)
check("sql: a user cannot list tables (403)", ann.get("/api/sql/connections/shop/tables").status_code == 403)
check("sql: the connection survived", len(admin.get("/api/sql/connections").json()["connections"]) == 1)
check("sql: admin lists tables", admin.get("/api/sql/connections/shop/tables").json().get("tables") == [{"name": "sales", "type": "table"}])
seen = ann.get("/api/sql/connections").json()["connections"][0]
check("sql: a user sees the name, no password text", seen["id"] == "shop" and isinstance(seen["password"], bool))

check("features: a user cannot read the admin switches (403)", ann.get("/api/admin/features").status_code == 403)
check("features: a user cannot change them (403)", ann.post("/api/admin/features", json={"memory": False}).status_code == 403)
r = ann.get("/api/skills")
check("features: a user reads which skills are on", r.status_code == 200 and set(r.json()) >= {"sql", "charts", "memory"})
r = admin.get("/api/admin/features").json()
check("features: admin reads switches and the tools model", "on" in r and "model" in r and "skills" in r)
before = admin.get("/api/skills").json()
check("features: an unknown field is ignored, not stored",
      admin.post("/api/admin/features", json={"sql": before["sql"], "evil": True}).status_code == 200
      and "evil" not in admin.get("/api/skills").json())
check("features: admin tools, code, llama-server stay admin-only (403)",
      all(ann.request(m, u, json={}).status_code == 403 for m, u in (
          ("GET", "/api/admin/tools"), ("POST", "/api/admin/tools/create"), ("PUT", "/api/code"),
          ("POST", "/api/admin/llama-server/apply"))))

# ---------------------------------------------------------------- SQLite files inside backend/data
data = BACKEND_DIR / "data"
link = tmp / "innocent.db"
link.symlink_to(data / "auth.db")
for label, path in (("the auth database", data / "auth.db"), ("a ../ path", BACKEND_DIR / "app" / ".." / "data" / "auth.db"),
                    ("a symlink to it", link), ("the saved connections", data / "sql" / "connections.json"),
                    ("a file under data/", data / "whatever.db")):
    r = admin.post("/api/sql/connections/test", json={**form, "path": str(path)})
    check(f"sql: {label} refused on test (400)", r.status_code == 400)
    r = admin.post("/api/sql/connections", json={**form, "name": "Sneaky", "path": str(path)})
    check(f"sql: {label} not saved", r.status_code == 400 and all(x["id"] != "sneaky" for x in admin.get("/api/sql/connections").json()["connections"]))
r = admin.post("/api/sql/connections/test", json={**form, "path": str(tmp / "missing.db")})
check("sql: a missing file is refused and not created", r.status_code == 400 and not (tmp / "missing.db").exists())
r = admin.post("/api/sql/connections/test", json={**form, "path": str(BACKEND_DIR / ".env")})
check("sql: a non-database file (.env) is refused without its content", r.status_code == 400 and "LLAMACPP" not in r.text)

# ---------------------------------------------------------------- S1: a saved password only goes to the same server and user
store = app.state.sql_store
store.save(Connection(id="pg", engine="postgresql", host="db.internal", port=5432, user="reader", password="s3cret", database="x"))

def pw(**change):
    base = dict(name="pg", engine="postgresql", host="db.internal", port=5432, user="reader", database="x", original_id="pg")
    return _connection(ConnectionForm(**{**base, **change}), store).password

check("S1: same host, port and user keeps the password", pw() == "s3cret")
check("S1: another host does not get it", pw(host="evil.example") == "")
check("S1: another user does not get it", pw(user="root") == "")
check("S1: another port does not get it", pw(port=5433) == "")
check("S1: another engine does not get it", pw(engine="mysql") == "")
check("S1: a typed password wins", pw(password="typed") == "typed")
check("S1: no original_id does not get it", pw(original_id=None) == "")
check("S1: the password never reaches the browser", "s3cret" not in json.dumps(ann.get("/api/sql/connections").json()))
store.delete("pg")

# ---------------------------------------------------------------- read-only database chat
script[:] = [[("query", {"database_id": "shop", "sql_query": "DELETE FROM sales"})],
             [("query", {"database_id": "shop", "sql_query": "SELECT SUM(qty) AS total FROM sales"})]]
r = chat(ann, "total of all sales", database="shop")
res = [e for e in events(r) if e.get("type") == "tool_result"]
check("db chat: a user may ask a saved database", r.status_code == 200 and len(res) == 2)
check("db chat: DELETE is refused with an error", res and "error" in res[0] and "read" in res[0]["error"].lower())
check("db chat: the table is untouched", sqlite3.connect(good_db).execute("select count(*) from sales").fetchone()[0] == 3)
check("db chat: SELECT returns columns and rows", len(res) == 2 and res[1].get("columns") == ["total"] and res[1].get("preview") == [[6]])
for sql in ("SELECT 1; DROP TABLE sales", "ATTACH DATABASE '/etc/passwd' AS p", "PRAGMA writable_schema=1",
            "SELECT * FROM sales -- x", "WITH x AS (DELETE FROM sales RETURNING *) SELECT * FROM x"):
    script[:] = [[("query", {"database_id": "shop", "sql_query": sql})]]
    res = [e for e in events(chat(ann, "q", database="shop")) if e.get("type") == "tool_result"]
    check(f"db chat: refused: {sql[:36]}", res and "error" in res[0])
check("db chat: the table still has 3 rows", sqlite3.connect(good_db).execute("select count(*) from sales").fetchone()[0] == 3)
check("db chat: unknown connection -> 404", chat(ann, "x", database="nope").status_code == 404)
check("db chat: a path is not a connection id -> 404", chat(ann, "x", database="../../auth").status_code == 404)
admin.post("/api/admin/features", json={"sql": False})
check("db chat: switched off by an admin -> 403", chat(ann, "x", database="shop").status_code == 403)
admin.post("/api/admin/features", json={"sql": True})

# ---------------------------------------------------------------- memories are private
a = ann.post("/api/memories", json={"text": "Ann's cat is called Tiger"}).json()
b = bob.post("/api/memories", json={"text": "Bob drives a green truck"}).json()
check("memory: a user adds and lists their own", [m["text"] for m in ann.get("/api/memories").json()["memories"]] == ["Ann's cat is called Tiger"])
check("memory: another user does not see it", [m["text"] for m in bob.get("/api/memories").json()["memories"]] == ["Bob drives a green truck"])
check("memory: the admin's list does not show others'", admin.get("/api/memories").json()["memories"] == [])
check("memory: editing another user's -> 404", bob.post(f"/api/memories/{a['id']}", json={"text": "pwned"}).status_code == 404)
check("memory: deleting another user's -> 404", bob.post(f"/api/memories/{a['id']}/delete").status_code == 404)
check("memory: the admin cannot edit another user's through the user route", admin.post(f"/api/memories/{a['id']}", json={"text": "x"}).status_code == 404)
check("memory: it is unchanged", ann.get("/api/memories").json()["memories"][0]["text"] == "Ann's cat is called Tiger")
check("memory: over 2000 characters -> 422", ann.post("/api/memories", json={"text": "x" * 2001}).status_code == 422)
check("memory: over 500 characters -> 400", ann.post("/api/memories", json={"text": "x" * 600}).status_code == 400)
check("memory: empty -> 400", ann.post("/api/memories", json={"text": "  "}).status_code == 400)
check("memory: a user id in the body is ignored", ann.post("/api/memories", json={"text": "scope test", "scope": b["id"], "user": "bob"}).status_code == 200
      and all(m["text"] != "scope test" for m in bob.get("/api/memories").json()["memories"]))
chat(ann)
check("memory: Ann's chat is told Ann's memory, not Bob's", "Tiger" in told() and "green truck" not in told())
chat(bob)
check("memory: Bob's chat is told his, not Ann's", "green truck" in told() and "Tiger" not in told())
admin.post("/api/admin/features", json={"memory": False})
chat(ann)
check("memory: switched off -> not told, and the list says so", "Tiger" not in told() and ann.get("/api/memories").json()["on"] is False)
admin.post("/api/admin/features", json={"memory": True})

# ---------------------------------------------------------------- sign-out kills the token
c, token = sign_in("bob")
bearer = {"Authorization": f"Bearer {token}"}
check("sign-out: the token works before", TestClient(app).get("/api/auth/me", headers=bearer).status_code == 200)
c.post("/api/auth/signout")
check("sign-out: the same token is refused after (as a header)", TestClient(app).get("/api/auth/me", headers=bearer).status_code == 401)
check("sign-out: and as a cookie", TestClient(app, cookies={"token": token}).get("/api/auth/me").status_code == 401)
check("sign-out: signing in again works", sign_in("bob")[0].get("/api/auth/me").status_code == 200)
check("sign-out: the user's other sessions ended too (documented)", bob.get("/api/auth/me").status_code == 401)
bob, _ = sign_in("bob")

with db() as con:
    gone = create_user(con, "temp@test.com", "temp", "password-temp", "user")
tmpc = TestClient(app)
tmpc.post("/api/auth/signin", json={"email": "temp@test.com", "password": "password-temp"})
tmpc.post("/api/memories", json={"text": "temp's secret"})
check("delete user: the memory exists", app.state.memory.list(gone["id"]) != [])
admin.post(f"/api/auth/users/{gone['id']}/delete")
check("delete user: their memories are gone", app.state.memory.list(gone["id"]) == [])
check("delete user: their token is dead", tmpc.get("/api/auth/me").status_code == 401)

with db() as con:
    mid_user = create_user(con, "mid@test.com", "mid", "password-mid", "admin")
mid = TestClient(app)
mid.post("/api/auth/signin", json={"email": "mid@test.com", "password": "password-mid"})
check("role: a second admin reaches admin routes", mid.get("/api/admin/features").status_code == 200)
admin.post(f"/api/auth/users/{mid_user['id']}", json={"role": "user"})
check("role: demoted -> the old session ends (401)", mid.get("/api/admin/features").status_code == 401)

# ---------------------------------------------------------------- chat files -> sources
r = ann.post("/api/files", files={"file": ("manual.txt", paragraphs.encode())})
info = r.json()
check("files: kept on the server, text not sent back", r.status_code == 200 and info.get("id") and "content" not in info and info["kind"] == "text")
check("files: the info fields", {"id", "name", "kind", "chars", "tokens", "pages", "truncated", "sheets", "rows", "query"} <= set(info))
calls.clear()
r = ann.post("/api/chat", json={"model": "llama", "messages": [
    {"role": "user", "content": "what is the vault code for room 77?", "files": [{"name": "manual.txt", "id": info["id"]}]}]})
ev = events(r)
src = [e for e in ev if e.get("type") == "sources"]
check("files: a big file's question gives a sources event", r.status_code == 200 and src and src[0]["items"])
check("files: sources name the file", src and src[0]["items"][0]["document"] == "manual.txt")
check("files: the passage asked about is among them", src and any("room 77" in i["text"] for i in src[0]["items"]))
check("files: the model got passages, not all 400 paragraphs", calls and len(json.dumps(calls[0][0])) < len(paragraphs))
check("files: the reply came", any("the answer" in json.dumps(e) for e in ev))
small = ann.post("/api/files", files={"file": ("note.txt", b"The meeting is at noon.")}).json()
calls.clear()
r = ann.post("/api/chat", json={"model": "llama", "messages": [{"role": "user", "content": "when?", "files": [{"name": "note.txt", "id": small["id"]}]}]})
check("files: a small file is given whole (no sources)", not [e for e in events(r) if e.get("type") == "sources"] and "meeting is at noon" in json.dumps(calls[0][0]))
r = ann.post("/api/chat", json={"model": "llama", "messages": [{"role": "user", "content": "what?", "files": [{"name": "x", "id": "../../auth"}]}]})
check("files: a path as a file id is 'no longer on the server'", r.status_code == 200 and "no longer on the server" in r.text)
r = ann.post("/api/chat", json={"model": "llama", "messages": [{"role": "user", "content": "what?", "files": [{"name": "x", "id": "A" * 24}]}]})
check("files: an unknown id gives a notice", "no longer on the server" in r.text)
check("files: .xls refused (415)", ann.post("/api/files", files={"file": ("old.xls", b"\xd0\xcf\x11\xe0" + b"0" * 100)}).status_code == 415)
check("files: a picture is refused (415)", ann.post("/api/files", files={"file": ("p.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100)}).status_code == 415)
check("files: an empty file is not a 500", ann.post("/api/files", files={"file": ("e.txt", b"")}).status_code < 500)
check("files: junk named .pdf is not a 500", ann.post("/api/files", files={"file": ("g.pdf", b"%PDF-1.4 nonsense")}).status_code < 500)
check("files: junk named .docx is not a 500", ann.post("/api/files", files={"file": ("g.docx", b"not a zip")}).status_code < 500)
check("files: junk named .xlsx is not a 500", ann.post("/api/files", files={"file": ("g.xlsx", b"PK\x03\x04junk")}).status_code < 500)

# the size limit: refused with 413, nothing kept
limit = config.workspaces.max_upload_mb
object.__setattr__(config.workspaces, "max_upload_mb", 1)
kept = len(list((tmp / "data" / "chatfiles").iterdir()))
check("files: over the size limit -> 413, nothing kept", ann.post("/api/files", files={"file": ("big.txt", b"x" * 2_000_000)}).status_code == 413
      and len(list((tmp / "data" / "chatfiles").iterdir())) == kept)
check("workspaces: over the size limit -> 413", admin.post(f"/api/workspaces/{ws}/documents", files={"file": ("big.txt", b"x" * 2_000_000)}).status_code == 413)
object.__setattr__(config.workspaces, "max_upload_mb", limit)
# shortcut: a small zip that inflates to ~200 MB (xlsx/docx are zips); must not take the server down
import io, zipfile
bomb = io.BytesIO()
with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", "<Types/>")
    z.writestr("xl/worksheets/sheet1.xml", b"<a>" + b"0" * 200_000_000 + b"</a>")
check("files: a zip bomb as .xlsx is refused or read in bounded time and memory, not a 500",
      ann.post("/api/files", files={"file": ("bomb.xlsx", bomb.getvalue())}).status_code < 500)

# a spreadsheet becomes SQL tables the model queries
rows = "\n".join(f"item{i},{i},{i * 2}" for i in range(400))
sheet = ann.post("/api/files", files={"file": ("sales.csv", f"item,qty,price\n{rows}\n".encode())}).json()
check("files: a big CSV is kept as tables to query", sheet.get("query") is True and sheet.get("rows") == 400)
table = sheet.get("id") and app.state.chatfiles.get(sheet["id"]).tables[0]["table"]
script[:] = [[("query", {"database_id": "sales.csv", "sql_query": f"SELECT SUM(qty) AS q FROM {table}"})]]
calls.clear()
r = ann.post("/api/chat", json={"model": "llama", "messages": [{"role": "user", "content": "plot qty as a chart", "files": [{"name": "sales.csv", "id": sheet["id"]}]}]})
ev = events(r)
done = [e for e in ev if e.get("type") == "tool_result" and e.get("name") == "query"]
check("files: a spreadsheet chat runs SQL on its table", r.status_code == 200 and done and done[0].get("preview") == [[sum(range(400))]])
check("files: the chart tool is offered when a chart is asked for", calls and any("create_chart" in json.dumps(t) for t in (calls[0][1] or [])))

# ---------------------------------------------------------------- workspace chat
calls.clear()
r = ann.post("/api/chat", json={"model": "llama", "workspace": ws, "messages": [{"role": "user", "content": "vault code for room 77?"}]})
src = [e for e in events(r) if e.get("type") == "sources"]
check("workspace chat: a user may use it, with a sources event", r.status_code == 200 and src and src[0]["items"])
check("workspace chat: an unknown or path id -> 404",
      all(ann.post("/api/chat", json={"model": "llama", "workspace": w, "messages": [{"role": "user", "content": "x"}]}).status_code == 404
          for w in ("nope-123", "../../etc")))

# ---------------------------------------------------------------- reading a workspace's documents (phase 2)
docs = {d["name"]: d["id"] for d in ann.get(f"/api/workspaces/{ws}").json()["documents"]}
D = f"/api/workspaces/{ws}/documents"
manual = docs.get("manual.txt")
r = ann.get(f"{D}/{manual}")
body = r.json() if r.status_code == 200 else {}
sec = body.get("sections") or []
joined = "\n".join(x.get("text", "") for x in sec)
check("doc: a user opens a document (name, kind, sections)", r.status_code == 200 and body.get("name") == "manual.txt" and "kind" in body and sec)
check("doc: the text is there, in order, without the overlap repeated",
      "Paragraph 0." in joined and "Paragraph 399." in joined and joined.index("Paragraph 5.") < joined.index("Paragraph 6.")
      and joined.count("Paragraph 77. ") == 1 and len(joined) < len(paragraphs) * 1.02)
check("doc: sections are {label, text}", all(set(x) >= {"label", "text"} for x in sec))
check("doc: admin may open it too", admin.get(f"{D}/{manual}").status_code == 200)
r = ann.get(f"{D}/{manual}/file")
check("doc: the original comes back byte for byte", r.status_code == 200 and r.content == paragraphs.encode())
disp = r.headers.get("content-disposition", "")
check("doc: a text file is a download, not shown inline", disp.startswith("attachment"), disp)
check("doc: nosniff on the original", r.headers.get("x-content-type-options", "").lower() == "nosniff", dict(r.headers))
check("doc: a text file is never served as html", "html" not in r.headers.get("content-type", "").lower())
# a PDF is shown inline, with its own type
def text_pdf(text):
    # shortcut: hand-made one-page PDF with a text layer (as tests/scan_route.py, which runs its checks on import)
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


admin.post(f"{D}", files={"file": ("policy.pdf", text_pdf("Refunds are paid within 14 days " * 20))})
docs = {d["name"]: d["id"] for d in ann.get(f"/api/workspaces/{ws}").json()["documents"]}
pdf_id = docs.get("policy.pdf")
r = ann.get(f"{D}/{pdf_id}/file") if pdf_id else None
check("doc: a PDF is served inline as application/pdf with nosniff",
      r is not None and r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf")
      and r.headers.get("content-disposition", "inline").startswith("inline") and r.headers.get("x-content-type-options", "").lower() == "nosniff"
      and r.content.startswith(b"%PDF-"))
r = ann.get(f"{D}/{pdf_id}") if pdf_id else None
check("doc: a PDF's sections are labelled 'page N'", r is not None and r.status_code == 200 and r.json()["sections"][0]["label"] == "page 1")
# a hostile file name must not break the header or the folder
nasty = 'a"b;c\u00e9 \u202e.txt'
admin.post(f"{D}", files={"file": (nasty, b"nasty name text for the test")})
nid = {d["name"]: d["id"] for d in ann.get(f"/api/workspaces/{ws}").json()["documents"]}
nid = next((i for n, i in nid.items() if n.endswith(".txt") and "nasty" not in n and "b;c" in n), None)
r = ann.get(f"{D}/{nid}/file") if nid else None
check("doc: a quote / unicode / bidi file name gives a well-formed download", r is not None and r.status_code == 200
      and "\n" not in r.headers.get("content-disposition", "") and r.content == b"nasty name text for the test")
# unknown ids and path tricks
for label, url in (("unknown document", f"{D}/99999"), ("document 0", f"{D}/0"), ("negative document", f"{D}/-1"),
                   ("text as a document id", f"{D}/abc"), ("huge document id", f"{D}/{'9' * 40}"),
                   ("path as a document id", f"{D}/..%2f..%2fauth.db"), ("encoded dotdot as the id", f"{D}/%2e%2e%2f%2e%2e%2fauth.db"),
                   ("NUL in the id", f"{D}/1%00"), ("unknown workspace", "/api/workspaces/nope-123/documents/1"),
                   ("path as the workspace", "/api/workspaces/..%2f..%2f/documents/1"),
                   ("uppercase workspace", f"/api/workspaces/{ws.upper()}/documents/{manual}")):
    for suffix in ("", "/file"):
        try:
            r = ann.get(url + suffix); status, content = r.status_code, r.content
        except Exception as exc:  # TestClient re-raises a server error: that is a 500
            status, content = 500, b""
            print("     server error:", repr(exc))
        check(f"doc: {label}{suffix} -> 404/422, nothing read", status in (404, 405, 422) and b"root:" not in content and b"SQLite format" not in content, status)
check("doc: logged-out -> 401", anon.get(f"{D}/{manual}").status_code == 401 and anon.get(f"{D}/{manual}/file").status_code == 401)
check("doc: a made-up token -> 401", anon.get(f"{D}/{manual}/file", headers={"Authorization": "Bearer x.y"}).status_code == 401)
check("doc: a user cannot delete a document (403) and it is still there", ann.delete(f"{D}/{manual}").status_code == 403 and ann.get(f"{D}/{manual}").status_code == 200)
# a document added before the change (no stored text): rebuilt from its passages, in order, overlap removed
wdir = tmp / "data" / "workspaces" / ws
import sqlite3
with sqlite3.connect(wdir / "index.db") as idx:  # the text and the original live in index.db (tables sections, originals)
    stored = idx.execute("DELETE FROM sections WHERE document_id = ?", (manual,)).rowcount
    idx.execute("DELETE FROM originals WHERE document_id = ?", (manual,))
check("doc: an old document has no original -> 404", ann.get(f"{D}/{manual}/file").status_code == 404)
r = ann.get(f"{D}/{manual}")
rebuilt = "\n".join(x["text"] for x in r.json().get("sections", [])) if r.status_code == 200 else ""
check("doc: with no stored text it is rebuilt from the passages (once, in order)", r.status_code == 200
      and "Paragraph 0." in rebuilt and "Paragraph 399." in rebuilt and rebuilt.count("Paragraph 77. ") == 1, f"removed {stored} sections")
# the chat's sources point at the document
r = ann.post("/api/chat", json={"model": "llama", "workspace": ws, "messages": [{"role": "user", "content": "vault code for room 77?"}]})
src = [e for e in events(r) if e.get("type") == "sources"]
check("doc: each source names its document id (for the viewer link)", src and all(isinstance(i.get("doc"), int) for i in src[0]["items"])
      and any(i["doc"] == manual for i in src[0]["items"]), src[0]["items"][:1] if src else None)
# deleting a document removes the original too
admin.delete(f"{D}/{docs.get('policy.pdf')}")
check("doc: deleted -> text and original are 404", ann.get(f"{D}/{pdf_id}").status_code == 404 and ann.get(f"{D}/{pdf_id}/file").status_code == 404)
with sqlite3.connect(wdir / "index.db") as idx:
    left = sum(idx.execute(f"SELECT COUNT(*) FROM {t} WHERE document_id = ?", (pdf_id,)).fetchone()[0] for t in ("originals", "sections", "chunks"))
check("doc: deleted -> its original, text and passages are gone from index.db", left == 0, left)

# ---------------------------------------------------------------- limits and clean-up
check("tool_ids: more than 20 -> 422", ann.post("/api/chat", json={"model": "llama", "tool_ids": ["a"] * 21, "messages": [{"role": "user", "content": "x"}]}).status_code == 422)
check("workspaces: a name over 80 characters -> 422", admin.post("/api/workspaces", json={"name": "x" * 81}).status_code == 422)
check("workspaces: a question over 4000 characters -> 422", ann.post(f"/api/workspaces/{ws}/search", json={"question": "x" * 4001}).status_code == 422)
check("workspaces: admin deletes it", admin.delete(f"/api/workspaces/{ws}").status_code == 200 and ann.get(f"/api/workspaces/{ws}").status_code == 404)
check("sql: admin deletes the connection", admin.delete("/api/sql/connections/shop").status_code == 200)

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
