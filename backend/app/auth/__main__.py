"""Self-check, no model and no server needed: python -m app.auth"""
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from . import admin_user, configure, current_user, init_db, router, seed_admin
from ..chats import init as init_chats, router as chats_router

with tempfile.TemporaryDirectory() as tmp:
    init_db(Path(tmp) / "auth.db")
    init_chats()
    configure(b"test-secret", 1)
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.include_router(chats_router, prefix="/api/chats")
    app.get("/protected")(lambda u=Depends(current_user): {"hi": u["email"]})
    c = TestClient(app)

    def login(email, pw):
        r = c.post("/api/auth/signin", json={"email": email, "password": pw})
        if r.status_code == 200:
            assert c.get("/protected").status_code == 200, "the cookie alone is a login"
        c.cookies.clear()  # from here on only the Bearer header counts
        return r.status_code, {"Authorization": "Bearer " + r.json()["token"]} if r.status_code == 200 else None

    assert seed_admin("Admin@Test.com", "Adm1n-pass!") and not seed_admin("x@y.com", "Adm1n-pass!"), "seed once"
    s, A = login("admin@test.com", "Adm1n-pass!")
    assert s == 200 and c.get("/protected", headers=A).json() == {"hi": "admin@test.com"}
    assert c.get("/protected").status_code == 401, "no token"
    assert c.post("/api/auth/signin", json={"email": "admin@test.com", "password": "bad"}).status_code == 401
    assert c.post("/api/auth/signin", json={"email": "nobody@test.com", "password": "bad"}).status_code == 401

    add = lambda **k: c.post("/api/auth/users", headers=A, json={"name": "B", "email": "bob@test.com", "password": "Bobpass-1", **k})
    bob = add().json()
    assert bob["role"] == "user" and add().status_code == 400, "duplicate email"
    assert add(email="nope").status_code == 400 and add(email="c@t.com", password="short").status_code == 400
    assert add(email="d@t.com", role="boss").status_code == 400
    s, B = login("BOB@test.com", "Bobpass-1")
    assert s == 200
    assert c.get("/api/auth/users", headers=B).status_code == 403, "non-admin"
    assert c.get("/api/auth/users").status_code == 401

    # a user changes their own password
    pw = lambda cur, new, h: c.post("/api/auth/password", headers=h, json={"current": cur, "new": new})
    assert pw("wrong-pass", "Another-pass1", B).status_code == 400, "wrong current password"
    assert pw("Bobpass-1", "short", B).status_code == 400 and pw("Bobpass-1", "Bobpass-1", B).status_code == 400
    assert c.post("/api/auth/password", json={"current": "a", "new": "b" * 9}).status_code == 401, "needs a login"
    r = pw("Bobpass-1", "Mine-now-77", B)
    assert r.status_code == 200 and c.get("/protected", headers=B).status_code == 401, "old session ends"
    assert c.get("/protected").status_code == 200, "this browser stays signed in (new cookie)"
    c.cookies.clear()
    assert login("bob@test.com", "Bobpass-1")[0] == 401 and (s := login("bob@test.com", "Mine-now-77"))[0] == 200
    B = s[1]
    assert c.post(f"/api/auth/users/{bob['id']}", headers=A, json={"password": "Bobpass-1"}).status_code == 200  # back, for the checks below
    _, B = login("bob@test.com", "Bobpass-1")

    me = c.get("/api/auth/me", headers=A).json()["id"]
    up = lambda uid, **k: c.post(f"/api/auth/users/{uid}", headers=A, json=k)
    assert up(bob["id"], name="Bob B", email="Bob.B@test.com").json()["email"] == "bob.b@test.com"
    assert up(bob["id"], email="admin@test.com").status_code == 400, "email taken"
    assert c.get("/protected", headers=B).status_code == 200
    assert up(bob["id"], password="Newpass-12").status_code == 200
    assert c.get("/protected", headers=B).status_code == 401, "password change ends old sessions"
    assert login("bob.b@test.com", "Bobpass-1")[0] == 401 and (s := login("bob.b@test.com", "Newpass-12"))[0] == 200
    B = s[1]
    assert c.post(f"/api/auth/users/{bob['id']}/revoke", headers=A).status_code == 200
    assert c.get("/protected", headers=B).status_code == 401, "revoke"
    _, B = login("bob.b@test.com", "Newpass-12")
    assert up(bob["id"], active=False).status_code == 200 and login("bob.b@test.com", "Newpass-12")[0] == 401, "disabled"
    assert up(bob["id"], active=True, role="admin").json()["role"] == "admin"

    # first admin is protected, even from another admin; nobody deletes themselves
    _, B = login("bob.b@test.com", "Newpass-12")
    # saved chats: private to each user, searchable, pictures not kept, gone with the user
    msgs = [{"role": "user", "content": "How do I bake sourdough?", "images": ["AAAA"], "names": ["a.png"]},
            {"role": "assistant", "content": "Mix flour and water.", "model": "m"}]
    assert c.get("/api/chats").status_code == 401, "chats need a login"
    cid = c.post("/api/chats", headers=B, json={"messages": msgs}).json()["id"]
    got = c.get(f"/api/chats/{cid}", headers=B).json()
    assert got["title"] == "How do I bake sourdough?" and "images" not in got["messages"][0] and got["messages"][0]["names"] == ["a.png"]
    assert [x["id"] for x in c.get("/api/chats", headers=B).json()] == [cid]
    assert c.get("/api/chats", headers=A).json() == [], "the admin does not see Bob's chats"
    assert c.get(f"/api/chats/{cid}", headers=A).status_code == 404 and c.post(f"/api/chats/{cid}", headers=A, json={"title": "x"}).status_code == 404
    assert c.post(f"/api/chats/{cid}/delete", headers=A).status_code == 404, "cannot delete another user's chat"
    assert [x["id"] for x in c.get("/api/chats?q=sourdough", headers=B).json()] == [cid] and c.get("/api/chats?q=zzz", headers=B).json() == []
    assert c.get("/api/chats?q=%25", headers=B).json() == [], "% is not a wildcard"
    assert c.post(f"/api/chats/{cid}", headers=B, json={"title": "Bread"}).json()["title"] == "Bread"
    msgs.append({"role": "user", "content": "more"})
    c.post(f"/api/chats/{cid}", headers=B, json={"messages": msgs})
    assert len(c.get(f"/api/chats/{cid}", headers=B).json()["messages"]) == 3 and c.get(f"/api/chats/{cid}", headers=B).json()["title"] == "Bread"
    gone = c.post("/api/chats", headers=B, json={"messages": msgs}).json()["id"]
    assert c.post(f"/api/chats/{gone}/delete", headers=B).status_code == 200 and c.get(f"/api/chats/{gone}", headers=B).status_code == 404

    bh = lambda uid, path="", **k: c.post(f"/api/auth/users/{uid}{path}", headers=B, json=k)
    assert bh(me, role="user").status_code == 403 and bh(me, "/delete").status_code == 403
    assert up(me, role="user").status_code == 403 and up(me, active=False).status_code == 403
    assert c.post(f"/api/auth/users/{me}/delete", headers=A).status_code == 403, "no self-delete"
    assert c.post(f"/api/auth/users/{bob['id']}/delete", headers=A).status_code == 200
    assert login("bob.b@test.com", "Newpass-12")[0] == 401
    from .db import db as _db
    with _db() as q:
        assert q.execute("SELECT count(*) FROM chat").fetchone()[0] == 0, "deleting a user deletes their chats"
    assert [u["email"] for u in c.get("/api/auth/users", headers=A).json()] == ["admin@test.com"]
    assert c.post("/api/auth/users/nope/delete", headers=A).status_code == 404

    # The real app (lifespan not run, so no models load): every /api route but health and auth needs a login.
    from ..main import create_app

    real = TestClient(create_app(), raise_server_exceptions=False)
    for path in ("/api/models", "/api/theme", "/api/documents/config", "/api/documents/templates", "/api/documents/invoices/config"):
        assert real.get(path).status_code == 401, path
    assert real.post("/api/chat", json={}).status_code == 401 and real.post("/api/files").status_code == 401
    assert real.get("/api/health").status_code != 401, "health stays open"
    assert real.get("/api/auth/me").status_code == 401
    assert real.get("/login.html").status_code == 200 and real.get("/auth.js").status_code == 200, "login page is public"
    assert real.get("/api/models", headers=A).status_code != 401, "a signed-in user gets through"
    # access-request workflow (n8n): its own key, always a plain user, hash only
    from ..settings import settings

    body = {"name": "Ann Applicant", "email": "Ann.A@test.com", "password": "Generated-9Pw!"}
    post = lambda key=None, **k: real.post("/api/access/users", headers={"X-Access-Key": key} if key else {}, json={**body, **k})
    object.__setattr__(settings, "access_key", "")  # whatever .env says
    assert post("anything").status_code == 503, "off while ACCESS_API_KEY is empty"
    object.__setattr__(settings, "access_key", "k" * 32)
    assert post().status_code == 401 and post("wrong" * 8).status_code == 401, "key required"
    assert real.post("/api/access/users", json=body, headers={"Authorization": "Bearer " + A["Authorization"][7:]}).status_code == 401, "a login is not the key"
    r = post("k" * 32, role="admin")
    assert r.status_code == 200 and r.json()["role"] == "user" and r.json()["email"] == "ann.a@test.com", r.text
    assert "password" not in r.text
    assert login("ann.a@test.com", "Generated-9Pw!")[0] == 200, "the new user can sign in"
    assert post("k" * 32).status_code == 409, "approving twice does not make a second account"
    assert post("k" * 32, email="b@test.com", password="short").status_code == 400
    assert post("k" * 32, email="nope").status_code == 400 and post("k" * 32, name="").status_code == 422
    with _db() as q:
        assert q.execute("SELECT password_hash FROM auth a JOIN user u ON u.id = a.id WHERE u.email = 'ann.a@test.com'").fetchone()[0].startswith("scrypt$")
    print("auth: all checks pass")
