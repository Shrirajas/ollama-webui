"""The llama-server client asks again once when its router drops the connection ("proxy error"), and only then.

No server and no model: a fake transport. Run from backend/: `.venv/bin/python tests/llama_retry.py`.
"""

import asyncio
import json

import httpx
from llamacpp_pipeline.client import LlamaCppClient
from ollama_pipeline import OllamaError


def run(answers: list[tuple[int, str]]) -> tuple[str, int]:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        status, text = answers[min(len(calls), len(answers)) - 1]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": text}})
        body = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    client = LlamaCppClient("http://fake")
    client._http = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(handler))

    async def go() -> str:
        out = ""
        async for chunk in client.chat_stream("m", [{"role": "user", "content": "hi"}], {"num_ctx": 4096}):
            out += chunk["message"]["content"]
        return out

    try:
        return asyncio.run(go()), len(calls)
    except OllamaError as exc:
        return f"{type(exc).__name__}: {exc}", len(calls)


PROXY = "proxy error: Failed to read connection"
assert run([(500, PROXY), (200, "hello")]) == ("hello", 2)  # one retry, then the answer
assert run([(500, PROXY)]) == (f"OllamaError: {PROXY}", 2)  # two tries, then the error
assert run([(500, "model crashed")]) == ("OllamaError: model crashed", 1)  # other errors are not retried
assert run([(200, "hi")]) == ("hi", 1)
overflow = run([(400, "the request exceeds the available context size")])
assert overflow[1] == 1 and overflow[0].startswith("ContextOverflow")
print("llama-server retry: proxy errors retried once, other errors and overflow not retried, all pass.")
