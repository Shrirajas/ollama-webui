"""/api/sql/... : the saved database connections of the Databases page.

The chat side is in `app/routes.py` (`/api/chat` with `database` set). Every signed-in user
sees the names (to pick one in the chat); only admins add, test, change or remove one:
a connection holds a password, and a SQLite one can open any file the server can read.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sql_connector import ENGINES, ConnectionStore, DatabaseError, check_fields, connector, missing_driver, same_server
from starlette.concurrency import run_in_threadpool

from ..auth import admin_user
from ..settings import BACKEND_DIR

router = APIRouter()
_DATA = (BACKEND_DIR / "data").resolve()


class ConnectionForm(BaseModel):
    """The form on the Databases page. `original_id` is set when an existing connection is changed."""

    name: str
    engine: str
    host: str = ""
    port: int | str | None = None
    user: str = ""
    password: str = ""
    database: str = ""
    db_schema: str = Field(default="", alias="schema")  # "schema" itself is taken by pydantic
    ssl: bool = False
    path: str = ""
    original_id: str | None = None


def get_store(request: Request) -> ConnectionStore:
    return request.app.state.sql_store


def _connection(form: ConnectionForm, store: ConnectionStore):
    try:
        connection = ConnectionStore.build(form.model_dump(by_alias=True))
        check_fields(connection)
    except DatabaseError as exc:
        raise HTTPException(400, str(exc)) from exc
    # The app's own data (users and password hashes, saved connections) is never a database to chat with.
    if connection.engine == "sqlite" and Path(connection.path).expanduser().resolve().is_relative_to(_DATA):
        raise HTTPException(400, "That file belongs to this app itself and cannot be added as a database.")
    # A changed connection whose password was left empty keeps the saved one.
    # Only to the same server and user: otherwise a changed host would be sent the saved password.
    old = store.get(form.original_id) if form.original_id else None
    if old and not connection.password and same_server(old, connection):
        connection.password = old.password
    return connection


async def _validate(request: Request, connection) -> None:
    timeout = min(15, request.app.state.registry.config.sql.timeout)
    try:
        await run_in_threadpool(connector(connection, timeout).validate)
    except DatabaseError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/connections")
def connections(request: Request) -> dict[str, Any]:
    try:
        saved = get_store(request).all()
    except DatabaseError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "connections": [c.public() for c in saved],
        "engines": [{"id": key, "label": label, "missing": missing_driver(key)} for key, label in ENGINES.items()],
    }


@router.post("/connections/test", dependencies=[Depends(admin_user)])
async def test(form: ConnectionForm, request: Request):
    """Connects and runs SELECT 1, without saving."""
    connection = _connection(form, get_store(request))
    await _validate(request, connection)
    return {"ok": True, "about": connection.describe()}


@router.post("/connections", dependencies=[Depends(admin_user)])
async def save(form: ConnectionForm, request: Request):
    """Saves a connection, after checking that it works (as AnythingLLM does)."""
    store = get_store(request)
    connection = _connection(form, store)
    await _validate(request, connection)
    try:
        saved = store.save(connection, form.original_id)
    except DatabaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    return saved.public()


@router.delete("/connections/{connection_id}", dependencies=[Depends(admin_user)])
def delete(connection_id: str, request: Request):
    if not get_store(request).delete(connection_id):
        raise HTTPException(404, f"There is no connection named '{connection_id}'.")
    return {"ok": True}


@router.get("/connections/{connection_id}/tables", dependencies=[Depends(admin_user)])
async def tables(connection_id: str, request: Request):
    """The tables of one connection, for the page to show that it works."""
    connection = get_store(request).get(connection_id)
    if connection is None:
        raise HTTPException(404, f"There is no connection named '{connection_id}'.")
    try:
        result = await run_in_threadpool(connector(connection, request.app.state.registry.config.sql.timeout).list_tables)
    except DatabaseError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"tables": [{"name": row[0], "type": row[1]} for row in result.rows]}
