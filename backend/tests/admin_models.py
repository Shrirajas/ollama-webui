"""Admin Panel -> Models: saved settings, turning a model off, and what the chat does then. Run from backend/:

    .venv/bin/python tests/admin_models.py

No server, no model: the app's routes on a temporary auth.db, llamacpp/config/models.yaml, and a
stand-in llama-server that lists both models and records what each reply was asked with.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from llamacpp_pipeline import Registry, load_config
from ollama_pipeline import Router
from app import model_settings, tools
from app.auth import current_user, init_db
from app.routes import router as api

results = []
def check(name, ok):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name)


def clean(raw):
    try:
        return model_settings.clean(raw)
    except HTTPException as exc:
        return exc.status_code


# clean(): the new keys, as their type and inside their range
new = {"repeat_last_n": 64, "mirostat": 2, "mirostat_tau": 5.0, "mirostat_eta": 0.1, "typical_p": 0.9, "think": True}
check("clean: new keys kept", clean(new) == new)
r = clean({"mirostat_tau": "5"})
check("clean: mirostat_tau as float", isinstance(r, dict) and isinstance(r.get("mirostat_tau"), float))
for bad in ({"repeat_last_n": -2}, {"repeat_last_n": 4097}, {"mirostat": 3}, {"mirostat_tau": 11},
            {"mirostat_eta": 1.5}, {"typical_p": -0.1}, {"mirostat": "x"}, {"no_such_key": 1}):
    check(f"clean: {bad} refused", clean(bad) == 422)
check("clean: repeat_last_n -1 allowed", clean({"repeat_last_n": -1}) == {"repeat_last_n": -1})
check("clean: think false is kept, not dropped as empty", clean({"think": False}) == {"think": False})

tmp = tempfile.TemporaryDirectory()
init_db(Path(tmp.name) / "auth.db")
model_settings.init()
tools.init()  # /api/models lists each model's default tools

config = load_config()
registry = Registry(config)
client = registry.client("llamacpp")
names = [m.model for m in config.models]
asked = []  # (model, options) of every reply


async def tags():
    return [{"name": n, "digest": n, "size": None, "details": {}} for n in names]


async def stream(model, messages, options=None):
    asked.append((model, options or {}))
    yield {"message": {"content": "ok"}, "done": True}

client.list_models, client.chat_stream = tags, stream

app = FastAPI()
from app.main import init_features  # noqa: E402
app.state.registry, app.state.router = registry, Router(config.router)  # no warm_up: keyword rules, not Von
init_features(app, Path(tempfile.mkdtemp()))  # databases, workspaces, chat files, skills
app.include_router(api, prefix="/api")
app.include_router(model_settings.router, prefix="/api/admin/models")
admin = {"id": "a", "email": "admin@test.com", "name": "A", "role": "admin"}
user = {**admin, "id": "u", "role": "user"}
who = {"user": admin}
app.dependency_overrides[current_user] = lambda: who["user"]
c = TestClient(app)

enable = lambda mid, on: c.patch(f"/api/admin/models/{mid}/enabled", json={"enabled": on})
picker = lambda: {m["id"] for m in c.get("/api/models").json()["models"]}
chat = lambda mid, text="hey": c.post("/api/chat", json={"model": mid, "messages": [{"role": "user", "content": text}]})

# GET /api/admin/models: the contract's shape
r = c.get("/api/admin/models")
body = r.json() if r.status_code == 200 else {}
check("GET admin/models answers", r.status_code == 200)
check("GET: limits include the new keys", {"repeat_last_n", "mirostat", "typical_p"} <= set(body.get("limits", {})))
check("GET: server block", isinstance(body.get("server", {}).get("available"), bool) and "limits" in body.get("server", {}))
rows = {m["id"]: m for m in body.get("models", [])}
check("GET: both models listed", set(rows) == {"llama", "gemma"})
check("GET: row fields", all({"id", "label", "model", "available", "enabled", "description", "defaults", "saved", "server"} <= set(m) for m in rows.values()))
check("GET: enabled by default", all(m.get("enabled") is True for m in rows.values()))
check("GET: model is the server-side name", rows.get("llama", {}).get("model") == "llama3.1:8b")

# a saved setting reaches the reply
llama = next(m for m in config.models if m.id == "llama")
r = c.put("/api/admin/models/llama", json={"temperature": 0.1, "mirostat": 2, "system": "Answer in French."})
check("PUT settings saved", r.status_code == 200)
asked.clear(); r = chat("llama")
check("chat with llama answers", r.status_code == 200 and '"done"' in r.text)
check("saved temperature and mirostat sent", asked and asked[-1][1].get("temperature") == 0.1 and asked[-1][1].get("mirostat") == 2)
c.put("/api/admin/models/llama", json={})

# turn llama off
who["user"] = user
check("non-admin cannot switch a model", enable("llama", False).status_code == 403)
who["user"] = admin
r = enable("llama", False)
check("PATCH enabled answers {id, enabled}", r.status_code == 200 and r.json() == {"id": "llama", "enabled": False})
check("is_enabled follows", model_settings.is_enabled("llama") is False and model_settings.is_enabled("gemma") is True)
check("unknown model: 404", enable("nope", False).status_code == 404)
check("enabled must be a bool", c.patch("/api/admin/models/gemma/enabled", json={}).status_code == 422)
check("GET shows it off", {m["id"]: m["enabled"] for m in c.get("/api/admin/models").json()["models"]}.get("llama") is False)
check("off: not in /api/models (admin)", "llama" not in picker() and "gemma" in picker())
who["user"] = user
check("off: not in /api/models (user)", "llama" not in picker())
r = chat("llama")
check("off: chat naming it -> 403", r.status_code == 403 and "turned off by an admin" in r.text)
asked.clear(); r = chat("auto")
check("off: Auto answers with gemma", r.status_code == 200 and asked and asked[-1][0] == "gemma4:12b")
check("off: Auto's route event names gemma", '"model": "gemma"' in r.text)
asked.clear(); r = chat(None)
check("off: no model named -> not llama", not asked or asked[-1][0] != "llama3.1:8b")
who["user"] = admin

# every model off: a clear error, not a reply from a model that is off
enable("gemma", False)
asked.clear(); r = chat("auto")
check("all off: no model asked", not asked)
check("all off: clear error", r.status_code >= 400 or '"error"' in r.text)
print("   all off answered:", r.status_code, r.text[:160])

enable("llama", True); enable("gemma", True)
check("back on: in /api/models", {"llama", "gemma"} <= picker())

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
