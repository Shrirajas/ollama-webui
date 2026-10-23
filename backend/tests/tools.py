"""Admin Panel -> Tools: the admin routes, who may reach them, and the chat's tool loop. Run from backend/:

    .venv/bin/python tests/tools.py

No server, no model: the app as main.py builds it, on a temporary auth.db, llamacpp/config/models.yaml,
and a stand-in llama-server whose replies ask for tools. The two real clients are checked against a fake
HTTP server (httpx.MockTransport): tools go out, streamed tool calls come back in Ollama's shape.
"""
import asyncio, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from llamacpp_pipeline import Registry, load_config
from llamacpp_pipeline.client import LlamaCppClient
from ollama_pipeline import Router
from ollama_pipeline.client import OllamaClient
from app import model_settings, tools
from app.auth import current_user, init_db
from app.main import create_app, init_features

results = []
def check(name, ok):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name)


tmp = tempfile.TemporaryDirectory()
marker = Path(tmp.name) / "ran"  # tool code that touches this file proves it was exec'd
init_db(Path(tmp.name) / "auth.db")
model_settings.init()
tools.init()
tools.TIMEOUT = 1  # the hang test waits for this, not 30 s

CALC = '''"""
title: Calculator
description: Sums and the like.
"""
import threading, time
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        api_key: str = Field("", description="A secret")
        factor: int = 1

    def __init__(self):
        self.valves = self.Valves()

    def add(self, a: int, b: int) -> str:
        """
        Add two numbers.
        :param a: The first number.
        :param b: The second number.
        """
        return str((a + b) * self.valves.factor)

    async def whoami(self, __user__: dict = {}) -> str:
        """Who is asking."""
        return __user__.get("email", "nobody")

    def boom(self) -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    def slow(self) -> str:
        """Takes too long."""
        time.sleep(3)
        return "late"

    def markup(self) -> str:
        """Returns HTML."""
        return "<img src=x onerror=alert(1)>"

    def big(self) -> str:
        """A long result."""
        return "x" * 10000

    def thread(self) -> str:
        """The thread it runs in."""
        return threading.current_thread().name

    def _hidden(self) -> str:
        return "not a tool"
'''
EXEC = f'open({str(marker)!r}, "w").write("x")\nclass Tools:\n    pass\n'

config = load_config()
registry = Registry(config)
client = registry.client("llamacpp")
names = [m.model for m in config.models]
calls = []      # (messages, tools, options) of every reply asked of the stand-in
script = []     # what each next reply does: a list of tool calls, or None for a plain answer


async def tags():
    return [{"name": n, "digest": n, "size": None, "details": {}} for n in names]


async def stream(model, messages, options=None, tools=None):
    calls.append(([dict(m) for m in messages], tools, options or {}))
    asks = script.pop(0) if script and tools else None  # a model offered no tools calls none
    if asks:
        yield {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": n, "arguments": a}} for n, a in asks]}, "done": False}
    else:
        yield {"message": {"role": "assistant", "content": "the answer"}, "done": False}
    yield {"message": {"role": "assistant", "content": ""}, "done": True}

client.list_models, client.chat_stream = tags, stream

app = create_app()  # mounted exactly as in production; no lifespan, so state is set here
app.state.registry, app.state.router = registry, Router(config.router)
init_features(app, Path(tempfile.mkdtemp()))  # databases, workspaces, chat files, skills
admin = {"id": "a", "email": "admin@test.com", "name": "A", "role": "admin"}
user = {**admin, "id": "u", "email": "user@test.com", "role": "user"}
who = {"user": admin}


def signed_in():
    if who["user"] is None:
        raise HTTPException(401, "Not signed in.")
    return who["user"]

app.dependency_overrides[current_user] = signed_in
c = TestClient(app)
A = "/api/admin/tools"


def create(tid, content=CALC, name="Calculator", description="Sums"):
    return c.post(f"{A}/create", json={"id": tid, "name": name, "content": content, "meta": {"description": description}})


def chat(tool_ids, text="what is 2+3", model="llama"):
    calls.clear()
    return c.post("/api/chat", json={"model": model, "tool_ids": tool_ids, "messages": [{"role": "user", "content": text}]})


def events(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


# --- create
for bad in ("bad-id", "../etc", "has space", "1abc", ""):
    r = create(bad)
    check(f"create: id {bad!r} refused", r.status_code in (400, 422) and (bad == "" or "alphanumeric" in r.text))
r = create("Calc")
tool = r.json() if r.status_code == 200 else {}
check("create: answers the tool", r.status_code == 200 and tool.get("id") == "calc")
check("create: id lower-cased", c.get(f"{A}/calc").status_code == 200)
check("create: id taken -> 400", create("calc").status_code == 400 and create("CALC").status_code == 400)
check("create: manifest from frontmatter", tool.get("meta", {}).get("manifest", {}).get("title") == "Calculator")
check("create: description kept", tool.get("meta", {}).get("description") == "Sums")

specs = {s["name"]: s for s in c.get(f"{A}/calc").json().get("specs", [])}
check("specs: public methods only (no _hidden, no Valves)", set(specs) == {"add", "whoami", "boom", "slow", "markup", "big", "thread"})
add = specs.get("add", {})
props = add.get("parameters", {}).get("properties", {})
check("specs: description from the docstring", add.get("description", "").startswith("Add two numbers."))
check("specs: types from hints", props.get("a", {}).get("type") == "integer" and props.get("b", {}).get("type") == "integer")
check("specs: :param text as description", props.get("a", {}).get("description") == "The first number.")
check("specs: required", sorted(add.get("parameters", {}).get("required", [])) == ["a", "b"])
check("specs: __user__ not shown to the model", "__user__" not in json.dumps(specs.get("whoami", {})))

r = create("broken", 'class Tools:\n    pass\nraise ValueError("nope at load")\n')
check("create: code that fails to load -> 400 with the error", r.status_code == 400 and "nope at load" in r.text)
check("create: nothing stored for it", c.get(f"{A}/broken").status_code == 404)
r = create("notools", "def add(a: int) -> int:\n    return a\n")
check("create: no class Tools -> 400", r.status_code == 400 and c.get(f"{A}/notools").status_code == 404)
check("create: syntax error -> 400", create("syntax", "class Tools(:\n").status_code == 400)

# the bundled slides tool is ~545,000 characters (a base64 icon table): the limit must fit it, and still be a limit
big = "# " + "x" * 600_000 + "\n" + CALC
check("create: a 600,000-character tool is accepted", create("bigone", big).status_code == 200)
check("create: over 2,000,000 characters -> 422", create("toobig", "#" * 2_000_001 + CALC).status_code == 422)
c.delete(f"{A}/bigone")

# --- list / read
r = c.get(A)
rows = {t["id"]: t for t in r.json()} if r.status_code == 200 else {}
check("GET list answers calc", r.status_code == 200 and "calc" in rows)
check("GET list: row fields, no code", {"id", "name", "meta", "enabled", "updated_at"} <= set(rows.get("calc", {}))
      and "content" not in rows.get("calc", {}))
check("GET one: has content and specs", {"content", "specs", "enabled"} <= set(c.get(f"{A}/calc").json()))
check("GET unknown -> 404", c.get(f"{A}/nope").status_code == 404)

# --- update
r = c.post(f"{A}/calc/update", json={"name": "Calc 2", "content": CALC.replace("def big", "def bigger"), "meta": {"description": "d2"}})
got = c.get(f"{A}/calc").json()
check("update: answers", r.status_code == 200)
check("update: specs recomputed", "bigger" in {s["name"] for s in got["specs"]} and "big" not in {s["name"] for s in got["specs"]})
check("update: name and description", got["name"] == "Calc 2" and got["meta"].get("description") == "d2")
r = c.post(f"{A}/calc/update", json={"name": "Calc 2", "content": "raise ValueError('bad update')", "meta": {"description": "d2"}})
check("update: bad code -> 400, old code kept", r.status_code == 400 and "def bigger" in c.get(f"{A}/calc").json()["content"])
check("update: unknown -> 404", c.post(f"{A}/nope/update", json={"name": "x", "content": CALC, "meta": {}}).status_code == 404)
c.post(f"{A}/calc/update", json={"name": "Calculator", "content": CALC, "meta": {"description": "Sums"}})

# --- valves
r = c.get(f"{A}/calc/valves/spec")
spec = r.json() if r.status_code == 200 else None
check("valves spec: JSON schema of Valves", isinstance(spec, dict) and {"api_key", "factor"} <= set(spec.get("properties", {})))
check("valves: GET answers a dict", isinstance(c.get(f"{A}/calc/valves").json(), dict))
r = c.post(f"{A}/calc/valves/update", json={"factor": 10, "api_key": "sk-SECRET-123"})
check("valves update: saved", r.status_code == 200 and c.get(f"{A}/calc/valves").json().get("factor") == 10)
check("valves update: wrong type -> 400", c.post(f"{A}/calc/valves/update", json={"factor": "lots"}).status_code == 400)
check("valves update: bad value did not replace the good one", c.get(f"{A}/calc/valves").json().get("factor") == 10)
create("plain", 'class Tools:\n    def ping(self) -> str:\n        """Ping."""\n        return "pong"\n')
r = c.get(f"{A}/plain/valves/spec")
check("valves spec: null without Valves", r.status_code == 200 and r.json() is None)

# --- who may reach what
admin_routes = [
    ("get", A, None), ("get", f"{A}/calc", None), ("post", f"{A}/create", {"id": "evil", "name": "e", "content": EXEC, "meta": {}}),
    ("post", f"{A}/calc/update", {"name": "e", "content": EXEC, "meta": {}}), ("patch", f"{A}/calc/enabled", {"enabled": False}),
    ("delete", f"{A}/calc", None), ("get", f"{A}/calc/valves", None), ("get", f"{A}/calc/valves/spec", None),
    ("post", f"{A}/calc/valves/update", {"factor": 99}),
]
for who["user"], want, label in ((user, 403, "non-admin"), (None, 401, "signed out")):
    for method, url, body in admin_routes:
        r = c.request(method.upper(), url, json=body)
        check(f"{label}: {method.upper()} {url.replace(A, '')} -> {want}", r.status_code == want)
who["user"] = None
check("signed out: GET /api/tools -> 401", c.get("/api/tools").status_code == 401)
who["user"] = admin
# CSRF: another port of this host is the same site, so the Lax cookie goes along with a no-cors fetch,
# which can only send a body without Content-Type or as text/plain. FastAPI would read either as JSON.
for ctype in (None, "text/plain"):
    r = c.post(f"{A}/create", content=json.dumps({"id": "csrf", "name": "e", "content": EXEC, "meta": {}}),
               headers={"content-type": ctype} if ctype else {})
    check(f"admin create sent as {ctype or 'no Content-Type'} (CSRF) refused", r.status_code in (400, 415, 422))
r = c.request("PATCH", f"{A}/calc/enabled", content='{"enabled": false}')
check("admin PATCH without Content-Type (CSRF) refused", r.status_code in (400, 415, 422))
check("non-admin create/update never ran the code", not marker.exists())
check("non-admin changed nothing", c.get(f"{A}/calc").json()["enabled"] is True
      and c.get(f"{A}/calc/valves").json().get("factor") == 10 and c.get(f"{A}/evil").status_code == 404)

who["user"] = user
r = c.get("/api/tools")
listed = {t["id"]: t for t in r.json()} if r.status_code == 200 else {}
check("user GET /api/tools answers", r.status_code == 200 and "calc" in listed)
check("user list: only id, name, description", all(set(t) <= {"id", "name", "description"} for t in listed.values()))
check("user list: no code, no valves, no secret", "sk-SECRET" not in r.text and "class Tools" not in r.text and "factor" not in r.text)
who["user"] = admin

# --- on/off
check("PATCH enabled: bool only", c.patch(f"{A}/calc/enabled", json={}).status_code == 422)
check("PATCH unknown -> 404", c.patch(f"{A}/nope/enabled", json={"enabled": False}).status_code == 404)
r = c.patch(f"{A}/plain/enabled", json={"enabled": False})
check("PATCH off answers", r.status_code == 200 and c.get(f"{A}/plain").json()["enabled"] is False)
check("off: hidden from users' list", "plain" not in {t["id"] for t in c.get("/api/tools").json()})
check("off: still in the admin list", "plain" in {t["id"] for t in c.get(A).json()})

# --- per-model default tools
r = c.put("/api/admin/models/llama", json={"tool_ids": ["calc", "plain"]})
check("model settings: tool_ids saved", r.status_code == 200)
llama = next((m for m in c.get("/api/models").json()["models"] if m["id"] == "llama"), {})
check("/api/models: tool_ids, enabled tools only", llama.get("tool_ids") == ["calc"])
check("model settings: tool_ids must be a list", c.put("/api/admin/models/llama", json={"tool_ids": "calc"}).status_code == 422)
r = chat([])
check("tool_ids are not sent as a model option", r.status_code == 200 and calls and "tool_ids" not in calls[0][2])

# --- chat: which tools reach the model
r = chat([])
check("no tool_ids: no tools sent", r.status_code == 200 and calls and not calls[0][1])
r = chat(["plain", "nope", "../x"])
check("off and unknown ids dropped: no tools sent", r.status_code == 200 and calls and not calls[0][1])
r = chat(["calc"])
sent = {t["function"]["name"] for t in (calls[0][1] or [])} if calls else set()
check("calc ticked: its functions sent as type function", calls and all(t.get("type") == "function" for t in calls[0][1] or [None])
      and {"add", "whoami"} <= sent)
check("more than 20 tool_ids -> 422", chat([f"t{i}" for i in range(21)]).status_code == 422)

# --- the loop
script[:] = [[("add", {"a": 2, "b": 3})]]
r = chat(["calc"])
ev = events(r)
check("loop: answers", r.status_code == 200 and any(e.get("type") == "done" for e in ev))
check("loop: two rounds", len(calls) == 2)
second = calls[1][0] if len(calls) == 2 else []
check("loop: assistant tool_calls kept in history", any(m.get("role") == "assistant" and m.get("tool_calls") for m in second))
tool_msg = next((m for m in second if m.get("role") == "tool"), {})
check("loop: tool message with the result (valve factor 10)", tool_msg.get("content") == "50" and tool_msg.get("tool_name") == "add")
check("loop: tool_call event", any(e.get("type") == "tool_call" and e.get("name") == "add" for e in ev))
check("loop: tool_result event", any(e.get("type") == "tool_result" and e.get("result") == "50" for e in ev))
check("loop: final answer streamed", any(e.get("type") == "delta" and e.get("content") == "the answer" for e in ev))

script[:] = [[("add", {"a": 1, "b": 1})]] * 10
r = chat(["calc"])
check("loop: at most 5 rounds with tools", r.status_code == 200 and len(calls) <= 6 and sum(1 for _, t, _ in calls if t) <= 5)
check("loop: the last round has no tools, and it answers", calls and not calls[-1][1] and '"done"' in r.text)
script.clear()

for fn, args, label, want in (("boom", {}, "a tool that raises", "kaput"), ("slow", {}, "a tool that hangs", None),
                              ("no_such_fn", {}, "an unknown function", None), ("add", {"a": 1}, "a missing argument", None)):
    script[:] = [[(fn, args)]]
    r = chat(["calc"])
    res = next((e.get("result", "") for e in events(r) if e.get("type") == "tool_result"), "")
    check(f"loop: {label} -> 'Error: ...' and the chat carries on",
          r.status_code == 200 and '"done"' in r.text and str(res).startswith("Error") and (want is None or want in res))
script[:] = [[("add", '{"a": 4, "b": 5}')]]
check("loop: arguments as a JSON string work too", '"90"' in chat(["calc"]).text)
script[:] = [[("thread", {})]]
res = next((e.get("result", "") for e in events(chat(["calc"])) if e.get("type") == "tool_result"), "")
check("loop: sync tools run in a pool of their own, not asyncio's (Von, invoices)", res and not res.startswith("asyncio_"))
script[:] = [[("big", {})]]
r = chat(["calc"])
res = next((e.get("result", "") for e in events(r) if e.get("type") == "tool_result"), "")
check("loop: result cut to 4000 characters", 0 < len(res) <= 4000)
script[:] = [[("whoami", {})]]
who["user"] = user
check("loop: __user__ is the signed-in user", '"user@test.com"' in chat(["calc"]).text)
script[:] = [[("whoami", {"__user__": {"email": "forged@evil", "role": "admin"}})]]
r = chat(["calc"])
check("loop: the model cannot pass __user__ itself", '"user@test.com"' in r.text and "forged@evil\"" not in r.text.split('"tool_result"')[-1])
who["user"] = admin
script[:] = [[("markup", {})]]
r = chat(["calc"])
check("loop: tool output passed as data (frontend must use textContent)", any(e.get("result") == "<img src=x onerror=alert(1)>" for e in events(r)))
check("loop: valves secret not in the stream", "sk-SECRET" not in r.text)
script.clear()

# The "New tool" template of admin-tools.html, saved as is: what the model sends must not run as code.
page = (Path(__file__).parents[2] / "frontend" / "admin-tools.html").read_text()
template = page.split("const BOILERPLATE = `", 1)[1].split("\n`;", 1)[0].replace("\\`", "`").replace("\\\\", "\\")
r = create("starter", template)
check("template: loads as a tool", r.status_code == 200)
for fn in [s["name"] for s in r.json().get("specs", [])] if r.status_code == 200 else []:
    for arg in ("__import__('os').getcwd()", "open('/etc/hostname').read()"):
        params = list(next(s for s in r.json()["specs"] if s["name"] == fn)["parameters"]["properties"])
        script[:] = [[(fn, {p: arg for p in params})]]
        res = next((e.get("result", "") for e in events(chat(["starter"])) if e.get("type") == "tool_result"), "")
        check(f"template: {fn}({arg}) runs no code", str(Path.cwd()) not in res and Path("/etc/hostname").read_text().strip() not in res)
c.delete(f"{A}/starter")
script.clear()

# --- delete
check("DELETE answers", c.delete(f"{A}/plain").status_code == 200)
check("DELETE: gone", c.get(f"{A}/plain").status_code == 404 and "plain" not in {t["id"] for t in c.get(A).json()})
check("DELETE unknown -> 404", c.delete(f"{A}/plain").status_code == 404)
r = chat(["plain"])
check("deleted id dropped from the chat", r.status_code == 200 and calls and not calls[0][1])


# --- the real clients, against a fake HTTP server
SPEC = [{"type": "function", "function": {"name": "add", "parameters": {"type": "object", "properties": {}}}}]
seen = []


def sse(*chunks):
    return "".join(f"data: {json.dumps(ch)}\n\n" for ch in chunks) + "data: [DONE]\n\n"

LLAMA_TOOL = sse(
    {"choices": [{"delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {"name": "add", "arguments": ""}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"a\": 2,"}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"b\": 3}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
)


def handler(request):
    seen.append(json.loads(request.content))
    if request.url.path == "/api/chat":
        return httpx.Response(200, text="\n".join(json.dumps(ch) for ch in (
            {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "add", "arguments": {"a": 2, "b": 3}}}]}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True})))
    return httpx.Response(200, text=LLAMA_TOOL)


async def run(cl, **kw):
    cl._http = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(handler))
    return [ch async for ch in cl.chat_stream("m", [{"role": "user", "content": "2+3"}], {}, **kw)]


def tool_calls(chunks):
    return [tc for ch in chunks for tc in (ch.get("message") or {}).get("tool_calls") or []]

for label, cl in (("ollama", OllamaClient("http://fake")), ("llama-server", LlamaCppClient("http://fake"))):
    seen.clear()
    got = tool_calls(asyncio.run(run(cl, tools=SPEC)))
    check(f"{label} client: tools in the request", seen and seen[0].get("tools") == SPEC)
    fn = got[0].get("function", {}) if got else {}
    check(f"{label} client: one tool call back, arguments as a dict", len(got) == 1 and fn.get("name") == "add" and fn.get("arguments") == {"a": 2, "b": 3})
    seen.clear(); asyncio.run(run(cl))
    check(f"{label} client: no tools -> no tools key", seen and "tools" not in seen[0])

# llama-server must get the tool round back in OpenAI's shape, or its chat template sees a result without a call.
seen.clear()
history = [{"role": "user", "content": "2+3"},
           {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "add", "arguments": {"a": 2, "b": 3}}}]},
           {"role": "tool", "content": "5", "tool_name": "add"}]
cl = LlamaCppClient("http://fake")
cl._http = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(handler))


async def drain(cl, msgs):
    return [ch async for ch in cl.chat_stream("m", msgs, {}, tools=SPEC)]

asyncio.run(drain(cl, history))
out = seen[0]["messages"] if seen else []
asst = next((m for m in out if m.get("role") == "assistant"), {})
args = ((asst.get("tool_calls") or [{}])[0].get("function") or {}).get("arguments")
check("llama-server: assistant tool_calls sent back", bool(asst.get("tool_calls")))
check("llama-server: their arguments as a JSON string", isinstance(args, str) and json.loads(args) == {"a": 2, "b": 3})
check("llama-server: tool message kept with its result", any(m.get("role") == "tool" and m.get("content") == "5" for m in out))
print("   tool message sent as:", next((m for m in out if m.get("role") == "tool"), None))

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
