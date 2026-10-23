"""Built-in skills (database queries, charts, memory): the Admin Panel's Features tab, and each
user's memories (Settings -> Personalization -> Memory in Open WebUI).

/api/skills                 any signed-in user: which skills are on (the chat hides what is off)
/api/admin/features         admins: the switches, and the model that runs the skills
/api/memories               the signed-in user's own memories; nobody sees another user's
"""
from agent_skills import SKILLS, MemoryError_
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import admin_user, current_user

router = APIRouter()  # /api/skills
admin = APIRouter(dependencies=[Depends(admin_user)])  # /api/admin/features
memories = APIRouter()  # /api/memories


class SkillChanges(BaseModel):
    sql: bool | None = None
    charts: bool | None = None
    memory: bool | None = None
    memory_from_chat: bool | None = None


class MemoryText(BaseModel):
    text: str = Field(max_length=2000)


@router.get("")
def skills_on(request: Request):
    return request.app.state.skills.get()


async def _tools_model(request: Request) -> dict:
    """The local model that runs the skills (`sql.model`), and whether it can call tools."""
    registry = request.app.state.registry
    wanted = registry.config.sql.model
    try:
        entries = await registry.list_models()
    except Exception:  # the model server is down: the panel still works
        entries = []
    entry = next((e for e in entries if e.id == wanted), None)
    if entry is None:
        entry = next((e for e in entries if e.available and e.local and e.tools), None)
    if entry is None:
        return {"label": wanted or "", "ready": False, "note": "No local model that can call tools is running."}
    ready = entry.available and entry.tools is not False
    note = "" if ready else ("Not running." if not entry.available else "This model cannot call tools.")
    return {"label": entry.label, "ready": ready, "note": note}


@admin.get("")
async def features(request: Request):
    return {"skills": SKILLS, "on": request.app.state.skills.get(), "model": await _tools_model(request)}


@admin.post("")
def set_features(changes: SkillChanges, request: Request):
    return {"on": request.app.state.skills.update(changes.model_dump(exclude_none=True))}


def _own(request: Request, user: dict, memory_id: str) -> None:
    if not any(m["id"] == memory_id for m in request.app.state.memory.list(user["id"])):
        raise HTTPException(404, "That memory is no longer there.")


@memories.get("")
def my_memories(request: Request, user: dict = Depends(current_user)):
    return {"memories": request.app.state.memory.list(user["id"]), "on": request.app.state.skills.get()["memory"]}


@memories.post("")
def add_memory(body: MemoryText, request: Request, user: dict = Depends(current_user)):
    try:
        return request.app.state.memory.add(user["id"], body.text, by="you")
    except MemoryError_ as exc:
        raise HTTPException(400, str(exc)) from exc


@memories.post("/{memory_id}")
def edit_memory(memory_id: str, body: MemoryText, request: Request, user: dict = Depends(current_user)):
    _own(request, user, memory_id)
    try:
        return request.app.state.memory.update(memory_id, body.text)
    except MemoryError_ as exc:
        raise HTTPException(400, str(exc)) from exc


# POST, not DELETE, as the other routes of this app (CORS allows GET and POST).
@memories.post("/{memory_id}/delete")
def remove_memory(memory_id: str, request: Request, user: dict = Depends(current_user)):
    _own(request, user, memory_id)
    request.app.state.memory.remove(memory_id)
    return {"ok": True}
