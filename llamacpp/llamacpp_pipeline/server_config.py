"""ctx-size and parallel of each model in llamacpp/server/models.ini, and restarting llama-server with them.

Used by Admin Panel -> Models. `write` changes only those two lines of a model's
[section] and keeps everything else (comments, paths, the [*] defaults); the
file before the change is kept as models.ini.bak. `restart` runs start.sh and
waits until every model has loaded and answered; if one does not, the backup
goes back and the server is started again with it.
"""

from __future__ import annotations

import asyncio
import configparser
import os
import shutil
import time
from pathlib import Path

import httpx

SERVER = Path(__file__).resolve().parent.parent / "server"
INI = SERVER / "models.ini"
START = SERVER / "start.sh"
CONTAINER = os.environ.get("LLAMACPP_CONTAINER", "llama-server-webui")  # the name start.sh uses

LIMITS = {"ctx_size": (2048, 131072), "parallel": (1, 16)}
_KEYS = {"ctx_size": "ctx-size", "parallel": "parallel"}  # our name -> models.ini's


def _bak() -> Path:
    return INI.with_name(INI.name + ".bak")


def read() -> dict[str, dict[str, int | None]]:
    """{model name: {"ctx_size", "parallel"}}; a value missing from the section comes from [*], else None."""
    ini = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    ini.read(INI, encoding="utf-8")
    shared = ini["*"] if ini.has_section("*") else {}
    out = {}
    for name in ini.sections():
        if name == "*":
            continue
        values = {}
        for key, ini_key in _KEYS.items():
            value = ini[name].get(ini_key) or shared.get(ini_key)
            values[key] = int(value) if value else None
        out[name] = values
    return out


def validate(changes: dict) -> None:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Nothing to change.")
    models = read()
    for name, values in changes.items():
        if name not in models:
            raise ValueError(f"{name} is not a model in llamacpp/server/models.ini.")
        if not isinstance(values, dict):
            raise ValueError(f"{name}: expected ctx_size and parallel.")
        for key, value in values.items():
            if key not in LIMITS:
                raise ValueError(f"{name}: unknown setting {key}.")
            low, high = LIMITS[key]
            if type(value) is not int:
                raise ValueError(f"{name}: {key} must be a whole number.")
            if not low <= value <= high:
                raise ValueError(f"{name}: {key} must be between {low} and {high}.")


def write(changes: dict) -> None:
    """Sets ctx-size / parallel in the models' sections; the file before is kept as models.ini.bak."""
    validate(changes)
    lines = INI.read_text(encoding="utf-8").splitlines()
    for name, values in changes.items():
        start = next(i for i, line in enumerate(lines) if line.strip() == f"[{name}]")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        for key, value in values.items():
            at = next((i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == _KEYS[key]), None)
            if at is None:  # not in the section yet: after its last line
                at = max(i for i in range(start, end) if lines[i].strip()) + 1
                lines.insert(at, "")
                end += 1
            lines[at] = f"{_KEYS[key]} = {value}"
    shutil.copyfile(INI, _bak())
    new = INI.with_name(INI.name + ".new")
    new.write_text("\n".join(lines) + "\n", encoding="utf-8")
    new.replace(INI)


async def _run(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:  # bash or docker not found
        return 1, str(exc)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


async def _start(timeout: float) -> None:
    """start.sh, then each model loaded and answering one token. Raises RuntimeError."""
    from .config import HOST, load_config  # here: config.py imports this module

    config = load_config()
    code, out = await _run("bash", str(START))
    if code:
        raise RuntimeError(f"start.sh failed: {out}")
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(base_url=config.hosts[HOST], headers=headers) as http:
        while True:
            try:
                if (await http.get("/health", timeout=5)).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("llama-server did not come up in time.")
            await asyncio.sleep(2)
        # The server's own spelling (`llama3.1:8B`): router mode matches names exactly.
        try:
            served = {m["id"].lower(): m["id"] for m in (await http.get("/v1/models", timeout=10)).json().get("data") or []}
        except (httpx.HTTPError, ValueError, KeyError, AttributeError):
            served = {}  # the chat request below then says what is wrong
        # Router mode loads a model on its first request; one model at a time, so a model that
        # does not fit is the one named.
        for name in read():
            name = served.get(name.lower(), name)
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError(f"{name} did not load in time.")
            body = {"model": name, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
            try:
                response = await http.post("/v1/chat/completions", json=body, timeout=httpx.Timeout(10.0, read=left))
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{name} did not load ({type(exc).__name__}).") from None
            if response.status_code >= 400:
                raise RuntimeError(f"{name} did not load: {response.text[:500]}")


async def restart(timeout: float = 240) -> None:
    """Restarts llama-server with models.ini. If a model fails, models.ini.bak goes back and the server
    is started again with it; RuntimeError says what went wrong, with the last lines of the server log."""
    try:
        await _start(timeout)
    except RuntimeError as exc:
        _, log = await _run("docker", "logs", "--tail", "15", CONTAINER)
        message = f"{exc}\n\nllama-server log:\n{log}"
        if not _bak().exists():
            raise RuntimeError(f"{message}\n\nNo models.ini.bak to go back to.") from None
        shutil.copyfile(_bak(), INI)
        try:
            await _start(timeout)
        except RuntimeError as again:
            raise RuntimeError(f"{message}\n\nThe previous models.ini was restored, but it failed too: {again}") from None
        raise RuntimeError(f"{message}\n\nThe previous models.ini was restored and llama-server runs with it.") from None
    shutil.copyfile(INI, _bak())  # the backup is now the last file that worked
