"""Account creation for the access-request workflow (n8n). Mounted at /api/access.

n8n asks the approver, generates a password and calls this with the shared key
(ACCESS_API_KEY in .env). The account is always a normal `user`; only the password
hash is stored. Without the key set, the route is switched off."""
import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .auth import db as store
from .settings import settings

router = APIRouter()


class NewAccount(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str
    password: str


@router.post("/users")
def create_user(form: NewAccount, x_access_key: str = Header(default="")):
    if not settings.access_key:
        raise HTTPException(503, "Access requests are not enabled (ACCESS_API_KEY is not set).")
    if not hmac.compare_digest(x_access_key.encode(), settings.access_key.encode()):
        raise HTTPException(401, "Wrong access key.")
    try:
        with store.db() as c:
            user = store.create_user(c, form.email, form.name, form.password, "user")
    except ValueError as e:
        raise HTTPException(409 if "already registered" in str(e) else 400, str(e))
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}
