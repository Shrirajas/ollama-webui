"""Sign in, and admin-only user management. Mounted at /api/auth.

`current_user` and `admin_user` are what other routes depend on to require a login."""
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import db as store
from .security import DUMMY_HASH, hash_password, make_token, read_token, verify_password

router = APIRouter()
COOKIE = "token"
_cfg = {"secret": b"", "hours": 168.0}


def configure(secret: bytes, hours: float) -> None:
    _cfg.update(secret=secret, hours=hours)


def load_secret(env_value: str, path: Path) -> bytes:
    """AUTH_SECRET from .env, else a random one kept in the data folder so tokens survive restarts."""
    if env_value:
        return env_value.encode()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(__import__("secrets").token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def current_user(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get(COOKIE, "")
    data = read_token(_cfg["secret"], token) if token else None
    if data:
        with store.db() as c:
            row = c.execute(
                "SELECT u.*, a.active, a.session_stamp FROM user u JOIN auth a ON a.id = u.id WHERE u.id = ?",
                (data["id"],),
            ).fetchone()
            if row and row["active"] and row["session_stamp"] == data["stamp"]:
                c.execute("UPDATE user SET last_active_at = strftime('%s','now') WHERE id = ?", (row["id"],))
                return store.public(row, row["active"])
    raise HTTPException(401, "Not signed in.")


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only.")
    return user


class SignIn(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    name: str
    email: str
    password: str
    role: str = "user"


class PasswordChange(BaseModel):
    current: str
    new: str


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    password: str | None = None
    active: bool | None = None


@router.post("/signin")
def signin(form: SignIn, response: Response):
    with store.db() as c:
        row = c.execute(
            "SELECT u.id, a.password_hash, a.active, a.session_stamp FROM user u JOIN auth a ON a.id = u.id WHERE u.email = ?",
            (form.email.strip().lower(),),
        ).fetchone()
        ok = verify_password(form.password, row["password_hash"] if row else DUMMY_HASH)
        if not (row and ok and row["active"]):
            raise HTTPException(401, "The email or password is incorrect.")
        token = make_token(_cfg["secret"], row["id"], row["session_stamp"], _cfg["hours"])
        user = store.public(c.execute("SELECT * FROM user WHERE id = ?", (row["id"],)).fetchone(), 1)
    # The cookie lets plain links (downloads) carry the login; the token is also returned for fetch().
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=int(_cfg["hours"] * 3600))
    return {"token": token, "user": user}


@router.post("/signout")
def signout(request: Request, response: Response):
    """Ends the session on the server too, so a copied token stops working (this signs the user
    out on their other devices as well, as a password change does)."""
    token = request.cookies.get(COOKIE, "")
    data = read_token(_cfg["secret"], token) if token else None
    if data:
        with store.db() as c:
            c.execute("UPDATE auth SET session_stamp = hex(randomblob(16)) WHERE id = ? AND session_stamp = ?",
                      (data["id"], data["stamp"]))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/password")
def change_password(form: PasswordChange, response: Response, user: dict = Depends(current_user)):
    """Any signed-in user changes their own password. Other sessions end; this one stays signed in."""
    with store.db() as c:
        row = c.execute("SELECT password_hash FROM auth WHERE id = ?", (user["id"],)).fetchone()
        if not verify_password(form.current, row["password_hash"]):
            raise HTTPException(400, "The current password is incorrect.")
        if len(form.new) < store.MIN_PASSWORD:
            raise HTTPException(400, f"The new password needs at least {store.MIN_PASSWORD} characters.")
        if form.new == form.current:
            raise HTTPException(400, "The new password must differ from the current one.")
        stamp = c.execute("UPDATE auth SET password_hash = ?, session_stamp = hex(randomblob(16)) WHERE id = ? RETURNING session_stamp",
                          (hash_password(form.new), user["id"])).fetchone()["session_stamp"]
    token = make_token(_cfg["secret"], user["id"], stamp, _cfg["hours"])
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=int(_cfg["hours"] * 3600))
    return {"token": token}


@router.get("/users")
def list_users(_: dict = Depends(admin_user)):
    with store.db() as c:
        rows = c.execute("SELECT u.*, a.active FROM user u JOIN auth a ON a.id = u.id ORDER BY u.created_at, u.rowid").fetchall()
    return [store.public(r, r["active"]) for r in rows]


@router.post("/users")
def add_user(form: NewUser, _: dict = Depends(admin_user)):
    try:
        with store.db() as c:
            return store.create_user(c, form.email, form.name, form.password, form.role)
    except (ValueError, sqlite3.IntegrityError) as e:
        raise HTTPException(400, str(e))


def _target(c: sqlite3.Connection, user_id: str, me: dict) -> sqlite3.Row:
    row = c.execute("SELECT u.*, a.active FROM user u JOIN auth a ON a.id = u.id WHERE u.id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "No such user.")
    if store.primary_admin_id(c) == user_id and me["id"] != user_id:
        raise HTTPException(403, "Only the first admin can change the first admin.")
    return row


@router.post("/users/{user_id}")
def update_user(user_id: str, form: UserUpdate, me: dict = Depends(admin_user)):
    with store.db() as c:
        row = _target(c, user_id, me)
        primary = store.primary_admin_id(c) == user_id
        if primary and (form.role not in (None, "admin") or form.active is False):
            raise HTTPException(403, "The first admin must stay an active admin.")
        if me["id"] == user_id and form.active is False:
            raise HTTPException(403, "You cannot disable yourself.")
        email = form.email.strip().lower() if form.email is not None else row["email"]
        role = form.role if form.role is not None else row["role"]
        try:
            store.check_new(email, form.password or "x" * store.MIN_PASSWORD, role)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if c.execute("SELECT 1 FROM user WHERE email = ? AND id != ?", (email, user_id)).fetchone():
            raise HTTPException(400, "That email is already registered.")
        c.execute("UPDATE user SET email = ?, name = ?, role = ? WHERE id = ?",
                  (email, (form.name or row["name"]).strip(), role, user_id))
        if form.active is not None:
            c.execute("UPDATE auth SET active = ? WHERE id = ?", (int(form.active), user_id))
        if form.password:
            c.execute("UPDATE auth SET password_hash = ? WHERE id = ?", (hash_password(form.password), user_id))
        # A new password, a disabled account or a changed role ends the old sessions.
        if form.password or form.active is False or role != row["role"]:
            c.execute("UPDATE auth SET session_stamp = hex(randomblob(16)) WHERE id = ?", (user_id,))
        out = c.execute("SELECT u.*, a.active FROM user u JOIN auth a ON a.id = u.id WHERE u.id = ?", (user_id,)).fetchone()
        return store.public(out, out["active"])


@router.post("/users/{user_id}/revoke")
def revoke(user_id: str, me: dict = Depends(admin_user)):
    with store.db() as c:
        _target(c, user_id, me)
        c.execute("UPDATE auth SET session_stamp = hex(randomblob(16)) WHERE id = ?", (user_id,))
    return {"ok": True}


# POST, not DELETE: main.py's CORS setting allows only GET and POST.
@router.post("/users/{user_id}/delete")
def delete_user(user_id: str, request: Request, me: dict = Depends(admin_user)):
    with store.db() as c:
        _target(c, user_id, me)
        if store.primary_admin_id(c) == user_id or me["id"] == user_id:
            raise HTTPException(403, "That account cannot be deleted.")
        c.execute("DELETE FROM user WHERE id = ?", (user_id,))  # auth row goes with it (ON DELETE CASCADE)
    if hasattr(request.app.state, "memory"):  # and their memories (absent in auth's own self-check)
        request.app.state.memory.drop_scope(user_id)
    return {"ok": True}
