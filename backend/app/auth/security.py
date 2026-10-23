"""Password hashing and session tokens. Standard library only (no new dependency)."""
import base64
import hashlib
import hmac
import json
import secrets
import time

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)



def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, key = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(got, bytes.fromhex(key))


# Verified when the email is unknown, so response time does not reveal which emails exist.
DUMMY_HASH = hash_password("not-a-real-password")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make_token(secret: bytes, user_id: str, stamp: str, hours: float) -> str:
    body = _b64(json.dumps({"id": user_id, "stamp": stamp, "exp": int(time.time() + hours * 3600)}).encode())
    return f"{body}.{_b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())}"


def read_token(secret: bytes, token: str) -> dict | None:
    """The payload if the signature is right and it has not expired, else None."""
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None
