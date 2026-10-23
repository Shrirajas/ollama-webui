"""`python -m llamacpp_pipeline` - checks the llama-server and every configured model."""

import asyncio
from pathlib import Path

from . import Registry, config_dir, load_config
from .config import HOST

try:  # the API key is in backend/.env, which the backend loads itself
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / "backend" / ".env")
except ImportError:
    pass


async def main() -> int:
    registry = Registry(load_config())
    try:
        print(f"config     {config_dir()}")
        health = await registry.health()
        for name, info in health.items():
            state = f"ok ({info['version']})" if info["ok"] else f"UNREACHABLE ({info['error']})"
            print(f"{name:<10} {info['url']:<32} {state}")
        print()
        for entry in await registry.list_models():
            state = "ready" if entry.available else "not on the server"
            print(f"  {entry.id:<12} {entry.model:<20} {state}")
        return 0 if health[HOST]["ok"] else 1  # the embedding server is optional: without it, search by words
    finally:
        await registry.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
