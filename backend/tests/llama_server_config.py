"""llamacpp_pipeline.server_config: models.ini read, checked, written and applied. Run from backend/:

    .venv/bin/python tests/llama_server_config.py

Works on a temporary copy of llamacpp/server/models.ini; the real file and the container are not
touched: start.sh and llama-server are stand-ins.
"""
import asyncio, json, re, shutil, sys, tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from llamacpp_pipeline import load_config, server_config as sc

REAL = Path(sc.__file__).resolve().parent.parent / "server" / "models.ini"
results = []
def check(name, ok):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name)


def refused(changes):
    try:
        sc.validate(changes)
    except ValueError as exc:
        return str(exc) or True
    return False


# num_ctx follows ctx-size: no hand-kept copy in models.yaml
real = sc.read()
config = load_config()
check("read: both models", set(real) == {m.model for m in config.models})
for m in config.models:
    check(f"num_ctx of {m.id} = ctx-size in models.ini", m.options.get("num_ctx") == real[m.model]["ctx_size"])
yaml_text = (Path(sc.__file__).resolve().parent.parent / "config" / "models.yaml").read_text()
check("models.yaml no longer sets num_ctx by hand", not re.search(r"^\s*num_ctx:", yaml_text, re.M))

tmp = Path(tempfile.mkdtemp())
ini = tmp / "models.ini"
shutil.copy(REAL, ini)
original = ini.read_text()
# point every models.ini path the module keeps at the copy
for name, value in vars(sc).items():
    if isinstance(value, Path) and value.name == "models.ini":
        setattr(sc, name, ini)
    elif isinstance(value, Path) and value.name == "models.ini.bak":
        setattr(sc, name, tmp / "models.ini.bak")

check("read: copy reads the same", sc.read() == real)
check("read: values are ints", all(isinstance(v, int) for m in real.values() for v in m.values()))
check("LIMITS", sc.LIMITS == {"ctx_size": (2048, 131072), "parallel": (1, 16)})

# validate
check("validate: good change passes", refused({"llama3.1:8b": {"ctx_size": 8192, "parallel": 2}}) is False)
for bad in ({"llama3.1:8b": {"ctx_size": 1024}}, {"llama3.1:8b": {"ctx_size": 200000}}, {"llama3.1:8b": {"parallel": 0}},
            {"llama3.1:8b": {"parallel": 17}}, {"llama3.1:8b": {"ctx_size": "big"}}, {"no-such:model": {"ctx_size": 8192}},
            {"llama3.1:8b": {"n-gpu-layers": 1}}):
    msg = refused(bad)
    check(f"validate: {bad} refused", msg)
    if msg: print("   message:", msg)

# write: only the two keys change, comments and everything else kept, backup made
sc.write({"llama3.1:8b": {"ctx_size": 8192, "parallel": 2}, "gemma4:12b": {"ctx_size": 12288}})
after = sc.read()
check("write: llama changed", after["llama3.1:8b"] == {"ctx_size": 8192, "parallel": 2})
check("write: gemma ctx changed, parallel kept", after["gemma4:12b"] == {**real["gemma4:12b"], "ctx_size": 12288})
text = ini.read_text()
diff = [(a, b) for a, b in zip(original.splitlines(), text.splitlines()) if a != b]
check("write: same number of lines", len(original.splitlines()) == len(text.splitlines()))
check("write: only ctx-size/parallel lines differ", diff and all(a.split("=")[0].strip() in ("ctx-size", "parallel") for a, _ in diff))
check("write: comments kept", [l for l in original.splitlines() if l.startswith(";")] == [l for l in text.splitlines() if l.startswith(";")])
check("write: backup is the old file", (tmp / "models.ini.bak").exists() and (tmp / "models.ini.bak").read_text() == original)
try:
    sc.write({"llama3.1:8b": {"ctx_size": 1}})
    check("write: refuses a bad value", False)
except ValueError:
    check("write: refuses a bad value", sc.read() == after)

# restart(): start.sh and llama-server are stand-ins. A model "does not fit" at ctx-size 131072.
ran, asked, fail = [], [], {"start": False, "always": False}


async def run(*args):
    ran.append(args[0])
    if args[0] == "bash":
        return (1, "docker: no such image") if fail["start"] else (0, "started")
    return 0, "llama_model_load: CUDA out of memory"


def handler(req):
    if req.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if req.url.path == "/v1/models":
        return httpx.Response(200, json={"data": [{"id": n} for n in sc.read()]})
    model = json.loads(req.content)["model"]
    asked.append(model)
    if fail["always"] or sc.read()[model]["ctx_size"] == 131072:
        return httpx.Response(500, text=f"failed to load {model}")
    return httpx.Response(200, json={"choices": []})

sc._run = run
RealClient = httpx.AsyncClient
httpx.AsyncClient = lambda **kw: RealClient(transport=httpx.MockTransport(handler), **kw)


def restart():
    ran.clear(); asked.clear()
    try:
        asyncio.run(sc.restart(timeout=5))
    except RuntimeError as exc:
        return str(exc)

check("restart: ok", restart() is None and ran == ["bash"] and sorted(asked) == sorted(after))
check("restart: ok -> backup is the file that worked", (tmp / "models.ini.bak").read_text() == ini.read_text())

good = ini.read_text()
sc.write({"llama3.1:8b": {"ctx_size": 131072}})
msg = restart()
check("restart: model fails -> error names it", msg and "llama3.1:8b" in msg)
check("restart: error has the server log", msg and "CUDA out of memory" in msg)
check("restart: previous models.ini put back", ini.read_text() == good)
check("restart: started again with it", ran.count("bash") == 2 and msg and "restored" in msg)
print("   message:", (msg or "").replace("\n", " | ")[:300])

fail["always"] = True
sc.write({"gemma4:12b": {"parallel": 3}})
msg = restart()
check("restart: both fail -> says so", msg and "failed too" in msg)
fail["always"] = False

fail["start"] = True
msg = restart()
check("restart: start.sh fails -> its output in the error", msg and "no such image" in msg)
fail["start"] = False

# HTTP: /api/admin/llama-server (backend/app/model_server.py) on the same copy and stand-in server
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import model_server
from app.auth import current_user

app = FastAPI()
app.state.registry = SimpleNamespace(config=None)
app.include_router(model_server.router, prefix="/api/admin/llama-server")
who = {"role": "admin"}
app.dependency_overrides[current_user] = lambda: {"id": "a", "email": "a@test.com", "name": "A", **who}
model_server.settings = SimpleNamespace(pipeline="llamacpp")
c = TestClient(app)
U = "/api/admin/llama-server"
num_ctx = lambda: next(m for m in app.state.registry.config.models if m.model == "llama3.1:8b").options["num_ctx"]

r = c.get(U).json()
check("GET: available, limits, models", r["available"] is True and r["models"] == sc.read() and r["limits"]["parallel"] == [1, 16])
before = ini.read_text()
r = c.put(U, json={"llama3.1:8b": {"ctx_size": 999999}})
check("PUT bad value: 422 with the reason, file unchanged", r.status_code == 422 and "between" in r.text and ini.read_text() == before)
check("PUT good value", c.put(U, json={"llama3.1:8b": {"ctx_size": 8192}}).status_code == 200 and sc.read()["llama3.1:8b"]["ctx_size"] == 8192)
r = c.post(U + "/apply")
check("apply ok", r.status_code == 200 and r.json() == {"ok": True})
check("apply ok: the app's num_ctx follows", num_ctx() == 8192)
c.put(U, json={"llama3.1:8b": {"ctx_size": 131072}})
r = c.post(U + "/apply")
check("apply fails: 502 naming the model", r.status_code == 502 and "llama3.1:8b" in r.json()["detail"])
check("apply fails: models.ini and num_ctx back to what worked", sc.read()["llama3.1:8b"]["ctx_size"] == 8192 and num_ctx() == 8192)
model_server._applying.locked = lambda: True
check("while a restart runs: PUT and apply 409", c.put(U, json={"llama3.1:8b": {"parallel": 2}}).status_code == 409 and c.post(U + "/apply").status_code == 409)
del model_server._applying.locked
who["role"] = "user"
check("non-admin: 403", c.get(U).status_code == 403 and c.post(U + "/apply").status_code == 403)
who["role"] = "admin"
model_server.settings = SimpleNamespace(pipeline="ollama")
check("on Ollama: unavailable, PUT 404", c.get(U).json()["available"] is False and c.put(U, json={"llama3.1:8b": {"parallel": 2}}).status_code == 404)
httpx.AsyncClient = RealClient
check("real models.ini untouched", REAL.read_text() == original)

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
