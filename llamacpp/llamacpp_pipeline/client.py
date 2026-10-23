"""Thin async client for one llama-server (llama.cpp), speaking its OpenAI-style API.

It has the same methods as `ollama_pipeline.client.OllamaClient` and hands back
the same shapes Ollama does (tags, `/api/show` capabilities, `/api/chat`
chunks), so the chat pipeline and the documents services work with it unchanged.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from ollama_pipeline import ContextOverflow, OllamaError, OllamaUnavailable

# Loading a model in router mode can take a while before the first token arrives.
STREAM_TIMEOUT = httpx.Timeout(10.0, read=None)
# One whole answer at once, which can take minutes for a long document.
JSON_TIMEOUT = httpx.Timeout(10.0, read=900.0)

# Ollama option -> llama-server request field. Everything else Ollama knows
# (num_ctx, num_gpu, num_thread) is a setting of the server, not of a request.
_OPTIONS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
    "seed": "seed",
    "stop": "stop",
    "num_predict": "max_tokens",
    "repeat_last_n": "repeat_last_n",
    "mirostat": "mirostat",
    "mirostat_tau": "mirostat_tau",
    "mirostat_eta": "mirostat_eta",
    "typical_p": "typical_p",
}

# The first bytes of a base64 picture tell its type; llama-server wants a data URL.
_IMAGE_TYPES = {"iVBOR": "image/png", "/9j/": "image/jpeg", "UklGR": "image/webp", "R0lGO": "image/gif", "Qk": "image/bmp"}


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text or f"llama-server returned HTTP {response.status_code}"
    if isinstance(error, dict):
        return error.get("message") or str(error)
    return error or response.text


def _error(message: str, error: Any = None) -> OllamaError:
    """A request too big for the context gets its own class, with the server's counts, so the
    pipeline can make it fit and try again."""
    error = error if isinstance(error, dict) else {}
    if error.get("type") == "exceed_context_size_error" or "context size" in message.lower():
        return ContextOverflow(message, error.get("n_prompt_tokens"), error.get("n_ctx"))
    return OllamaError(message)


def _error_body(response: httpx.Response) -> Any:
    try:
        return response.json().get("error")
    except (ValueError, AttributeError):
        return None


def _image_url(data: str) -> str:
    if data.startswith("data:"):
        return data
    kind = next((t for prefix, t in _IMAGE_TYPES.items() if data.startswith(prefix)), "image/png")
    return f"data:{kind};base64,{data}"


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ollama messages (text + a list of base64 images, tool calls and results) -> OpenAI messages."""
    out = []
    pending: list[str] = []  # ids of the last calls, for results that do not name theirs
    for message in messages:
        if message.get("tool_calls"):
            calls = [{
                "id": c.get("id") or f"call_{i}", "type": "function",
                "function": {"name": c["function"]["name"], "arguments": _arguments_text(c["function"].get("arguments"))},
            } for i, c in enumerate(message["tool_calls"])]
            pending = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            continue
        if message["role"] == "tool":
            call_id = message.get("tool_call_id") or (pending[0] if pending else "")
            pending = [p for p in pending if p != call_id]
            out.append({"role": "tool", "content": message.get("content") or "", "tool_call_id": call_id})
            continue
        images = message.get("images") or []
        if not images:
            out.append({"role": message["role"], "content": message.get("content") or ""})
            continue
        parts: list[dict[str, Any]] = [{"type": "image_url", "image_url": {"url": _image_url(i)}} for i in images]
        if message.get("content"):
            parts.append({"type": "text", "text": message["content"]})
        out.append({"role": message["role"], "content": parts})
    return out


def _arguments_text(arguments: Any) -> str:
    return arguments if isinstance(arguments, str) else json.dumps(arguments or {})


def _arguments(text: str) -> Any:
    """A streamed call's arguments as Ollama gives them (a dict); text that is not JSON is passed on as is."""
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        try:
            return ast.literal_eval(text)  # as Open WebUI: some models write Python dicts
        except (ValueError, SyntaxError):
            return text


# Tables (Excel, CSV) take about 2.5 characters a token; llama-server refuses a request that
# does not fit, where Ollama silently drops the start. 2048 tokens are kept for the answer.
_CHARS_PER_TOKEN = 2.5
_ANSWER_TOKENS = 2048
_TAIL = 1500


def _fit(messages: list[dict[str, Any]], num_ctx: int | None) -> list[dict[str, Any]]:
    """Cuts the middle out of the longest message until the text fits the context."""
    if not num_ctx:
        return messages
    messages = [dict(m) for m in messages]
    budget = int((num_ctx - _ANSWER_TOKENS) * _CHARS_PER_TOKEN)
    over = sum(len(m.get("content") or "") for m in messages) - budget
    if over > 0:
        longest = max(messages, key=lambda m: len(m.get("content") or ""))
        text = longest["content"]
        keep = max(len(text) - over - 40, _TAIL)
        head = max(keep - _TAIL, 0)
        longest["content"] = text[:head] + "\n[...cut to fit the model's context...]\n" + text[-_TAIL:]
    return messages


class _Pool:
    """The tokens of one model's context, handed out to chats: a chat that does not fit waits.

    llama-server shares one context (ctx-size) between all running chats and fails them
    all with "Context size has been exceeded" when it is full, so fit is checked here.
    """

    def __init__(self) -> None:
        self.used = 0
        self.changed = asyncio.Condition()

    async def take(self, tokens: int, size: int) -> None:
        async with self.changed:
            await self.changed.wait_for(lambda: self.used + tokens <= size)
            self.used += tokens

    async def give(self, tokens: int) -> None:
        async with self.changed:
            self.used -= tokens
            self.changed.notify_all()


def _options(options: dict[str, Any] | None) -> dict[str, Any]:
    out = {_OPTIONS[k]: v for k, v in (options or {}).items() if k in _OPTIONS and v is not None}
    # `think: false` in a model's options (models.yaml) skips a thinking model's reasoning.
    if (options or {}).get("think") is not None:
        out["chat_template_kwargs"] = {"enable_thinking": bool(options["think"])}
    return out


def _done(timings: dict[str, Any], usage: dict[str, Any], started: float) -> dict[str, Any]:
    """The last chunk, with Ollama's statistics (durations in nanoseconds)."""
    predicted_ms = timings.get("predicted_ms")
    return {
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "prompt_eval_count": timings.get("prompt_n") or usage.get("prompt_tokens"),
        "eval_count": timings.get("predicted_n") or usage.get("completion_tokens"),
        "eval_duration": int(predicted_ms * 1e6) if predicted_ms else None,
        "total_duration": int((time.monotonic() - started) * 1e9),
    }


class LlamaCppClient:
    def __init__(self, base_url: str, api_key: str = "", capabilities: dict[str, list[str]] | None = None):
        self.base_url = base_url
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=base_url, timeout=10.0, headers=headers)
        # What each served model can do, from llamacpp/config/models.yaml: llama-server does not say.
        self._capabilities = capabilities or {}
        # Lower-case name -> the server's own spelling: router mode writes `llama3.1:8b` as `llama3.1:8B`.
        self._names: dict[str, str] = {}
        self._pools: dict[str, _Pool] = {}

    def _unavailable(self, exc: Exception) -> OllamaUnavailable:
        return OllamaUnavailable(
            f"Can't reach llama-server at {self.base_url} ({type(exc).__name__}). Is its container running?"
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        if response.status_code >= 400:
            raise OllamaError(_error_text(response))
        return response.json()

    async def version(self) -> str:
        await self._request("GET", "/v1/models")  # unlike /health, this one checks the API key
        try:
            build = (await self._request("GET", "/props")).get("build_info")
        except OllamaError:
            build = None  # a router-mode server only answers /props for a named model
        return f"llama.cpp {build}" if build else "llama.cpp"

    async def list_models(self) -> list[dict[str, Any]]:
        """Models this server can answer with, shaped like Ollama's tags."""
        tags = []
        for model in (await self._request("GET", "/v1/models")).get("data") or []:
            status = model.get("status")
            if isinstance(status, dict) and status.get("value") == "failed":
                continue  # router mode: the model is listed but could not be loaded
            name = model["id"].lower()
            self._names[name] = model["id"]
            tags.append({"name": name, "digest": model["id"], "size": None, "details": {}})
        return tags

    async def show(self, model: str) -> dict[str, Any]:
        return {"capabilities": self._capabilities.get(model.lower(), ["completion"])}

    def _name(self, model: str) -> str:
        return self._names.get(model.lower(), model)

    async def chat_stream(
        self, model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Waits until the chat fits in the model's context, then streams (see `_stream`)."""
        size = int((options or {}).get("num_ctx") or 16384)
        messages = _fit(messages, size)
        tokens = min(size, int(sum(len(m.get("content") or "") for m in messages) / _CHARS_PER_TOKEN) + _ANSWER_TOKENS)
        pool = self._pools.setdefault(model, _Pool())
        await pool.take(tokens, size)
        try:
            for attempt in (1, 2):
                sent = False
                try:
                    async for chunk in self._stream(model, messages, options, tools):
                        sent = True
                        yield chunk
                    return
                except OllamaError as exc:
                    # Router mode now and then drops its connection to a model ("proxy error: Failed to read
                    # connection"). Ask once more, but only when nothing has reached the user yet.
                    if sent or attempt == 2 or "proxy error" not in str(exc).lower():
                        raise
        finally:
            await pool.give(tokens)

    async def _stream(
        self, model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields Ollama-shaped /api/chat chunks while llama-server streams the reply; tool calls,
        streamed in pieces, come as one `message.tool_calls` chunk before the last."""
        payload: dict[str, Any] = {
            "model": self._name(model),
            "messages": _messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            **_options(options),
        }
        if tools:
            payload["tools"] = tools
        started = time.monotonic()
        timings: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        calls: dict[int, dict[str, str]] = {}  # index -> id, name, arguments text
        try:
            async with self._http.stream("POST", "/v1/chat/completions", json=payload, timeout=STREAM_TIMEOUT) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise _error(_error_text(response), _error_body(response))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        error = chunk["error"]
                        raise _error(error.get("message") if isinstance(error, dict) else str(error), error)
                    timings = chunk.get("timings") or timings
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        for piece in delta.get("tool_calls") or []:
                            call = calls.setdefault(piece.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            function = piece.get("function") or {}
                            call["id"] = piece.get("id") or call["id"]
                            call["name"] = function.get("name") or call["name"]
                            call["arguments"] += _arguments_text(function["arguments"]) if function.get("arguments") else ""
                        if delta.get("reasoning_content") or delta.get("content"):
                            yield {
                                "message": {
                                    "role": "assistant",
                                    "content": delta.get("content") or "",
                                    "thinking": delta.get("reasoning_content") or "",
                                },
                                "done": False,
                            }
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        if calls:
            yield {
                "message": {"role": "assistant", "content": "", "tool_calls": [
                    # an id always, so the result can name the call it answers
                    {"id": c["id"] or f"call_{i}", "function": {"name": c["name"], "arguments": _arguments(c["arguments"])}}
                    for i, c in sorted(calls.items())
                ]},
                "done": False,
            }
        yield _done(timings, usage, started)

    async def chat_json(
        self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any], options: dict[str, Any] | None = None,
        *, think: bool | None = None,
    ) -> Any:
        """One answer, held by llama-server to the given JSON schema, as a Python object.

        `think=False` asks the model's chat template to skip its reasoning; a
        template without that switch ignores it.
        """
        payload: dict[str, Any] = {
            "model": self._name(model),
            "messages": _messages(messages),
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
            **_options(options),
        }
        if think is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": think}
        data = await self._request("POST", "/v1/chat/completions", json=payload, timeout=JSON_TIMEOUT)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            return json.loads(content)
        except ValueError:
            found = re.search(r"\{.*\}", content, re.DOTALL)
            try:
                return json.loads(found.group(0)) if found else None
            except ValueError:
                return None

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One vector per text. Needs an embedding model on this llama-server (workspaces' embed_model)."""
        data = await self._request("POST", "/v1/embeddings", json={"model": self._name(model), "input": texts}, timeout=JSON_TIMEOUT)
        items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") or [] for item in items]
        if len(vectors) != len(texts):
            raise OllamaError(f"llama-server returned {len(vectors)} vectors for {len(texts)} texts. Is {model} an embedding model?")
        return vectors

    async def aclose(self) -> None:
        await self._http.aclose()
