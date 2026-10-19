"""Workspaces at work: adding documents, searching them, and the context a chat gets.

The model's only part here is the embedding model, which turns passages and
questions into vectors. Without it everything still works, by keywords.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
from ollama_pipeline import OllamaError, PipelineError, Registry

from . import prompts
from .chunking import passages as split_passages
from .index import Hit, Workspace, WorkspaceError, Workspaces

log = logging.getLogger(__name__)

BATCH = 16  # passages sent to the embedding model at once
STATUS_SECONDS = 60  # how long a check of the embedding model is trusted
SNIPPET = 600  # characters of a passage shown under an answer


def _unit(vector: list[float]) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    return array / norm if norm else array


class WorkspaceService:
    def __init__(self, registry: Registry, root: Path):
        self.registry = registry
        self.config = registry.config.workspaces
        self.store = Workspaces(root)
        self._status: tuple[float, dict[str, Any]] | None = None

    # ── the embedding model ──
    async def _embed(self, texts: list[str], prefix: str) -> list[np.ndarray]:
        client = self.registry.client(self.config.embed_host)
        vectors = await client.embed(self.config.embed_model, [prefix + t for t in texts])
        return [_unit(v) for v in vectors]

    async def embedder(self, fresh: bool = False) -> dict[str, Any]:
        """Whether passages can be searched by meaning right now, and if not, why."""
        if not self.config.embed_model:
            return {"ready": False, "model": "", "reason": "No embedding model is set (workspaces.embed_model): search is by keywords."}
        if self._status and not fresh and time.monotonic() - self._status[0] < STATUS_SECONDS:
            return self._status[1]
        try:
            await self._embed(["ready?"], self.config.query_prefix)
            status = {"ready": True, "model": self.config.embed_model, "reason": ""}
        except PipelineError as exc:
            status = {"ready": False, "model": self.config.embed_model,
                      "reason": f"The embedding model {self.config.embed_model} is not reachable ({exc}). Search is by keywords until it is."}
        self._status = (time.monotonic(), status)
        return status

    def _stale(self, workspace: Workspace) -> bool:
        """Vectors made by another embedding model than the one set now cannot be compared with new ones."""
        made_by = workspace.meta().get("embed_model") or ""
        return bool(made_by) and made_by != self.config.embed_model

    # ── workspaces ──
    def summary(self, workspace: Workspace) -> dict[str, Any]:
        meta, counts = workspace.meta(), workspace.counts()
        return {**meta, **counts, "stale": self._stale(workspace),
                "missing_vectors": counts["passages"] - counts["with_vectors"]}

    def list(self) -> list[dict[str, Any]]:
        return sorted((self.summary(w) for w in self.store.all()), key=lambda w: w["name"].lower())

    def create(self, name: str, instructions: str = "") -> dict[str, Any]:
        return self.summary(self.store.create(name, instructions))

    def update(self, workspace_id: str, name: str | None, instructions: str | None) -> dict[str, Any]:
        workspace = self.store.get(workspace_id)
        changes: dict[str, Any] = {}
        if name is not None and name.strip():
            if any(w.id != workspace_id and w.meta()["name"].lower() == name.strip().lower() for w in self.store.all()):
                raise WorkspaceError(f"A workspace named '{name.strip()}' already exists.")
            changes["name"] = name.strip()[:80]
        if instructions is not None:
            changes["instructions"] = instructions.strip()[:4000]
        workspace.save_meta(**changes)
        return self.summary(workspace)

    def delete(self, workspace_id: str) -> None:
        self.store.delete(workspace_id)

    # ── documents ──
    async def add(
        self, workspace_id: str, name: str, data: bytes, sections: list[tuple[str, str]], truncated: bool,
        pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Splits a read document into passages, gives them vectors when it can, and stores them.

        Yields progress events, then `added` (or `skipped` when the same file is already there).
        """
        workspace = self.store.get(workspace_id)
        sha = hashlib.sha256(data).hexdigest()
        existing, same = workspace.find(name, sha)
        if same:
            yield {"type": "skipped", "name": name, "message": f"{name} is already in this workspace, unchanged."}
            return

        yield {"type": "progress", "name": name, "message": "Splitting into passages"}
        found = split_passages(sections, self.config.chunk_chars, self.config.overlap_chars)
        if not found:
            raise WorkspaceError(f"{name} has no text to search.")

        vectors: list[np.ndarray | None] = [None] * len(found)
        note = None
        status = await self.embedder()
        if status["ready"] and self._stale(workspace):
            note = "Search by meaning is paused for this workspace until it is re-indexed with the new embedding model."
        elif status["ready"]:
            try:
                for start in range(0, len(found), BATCH):
                    yield {"type": "progress", "name": name,
                           "message": f"Reading for meaning: {min(start + BATCH, len(found))} of {len(found)} passages"}
                    batch = await self._embed([p.text for p in found[start:start + BATCH]], self.config.document_prefix)
                    vectors[start:start + len(batch)] = batch
                if not workspace.meta().get("embed_model"):
                    workspace.save_meta(embed_model=self.config.embed_model)
            except PipelineError as exc:
                vectors = [None] * len(found)
                self._status = None
                note = f"Indexed for keyword search only: the embedding model failed ({exc}). Re-index later."
        else:
            note = status["reason"]

        document_id = await asyncio.to_thread(
            workspace.add, name, sha, sum(len(t) for _, t in sections), pages, truncated, found, vectors
        )
        log.info("Workspaces: %s added to %s (%d passages, vectors: %s)", name, workspace_id, len(found), vectors[0] is not None)
        yield {
            "type": "added", "name": name, "id": document_id, "passages": len(found), "replaced": existing is not None,
            "by_meaning": vectors[0] is not None, "truncated": truncated, "note": note,
        }

    def remove(self, workspace_id: str, document_id: int) -> bool:
        return self.store.get(workspace_id).remove(document_id)

    async def reindex(self, workspace_id: str) -> AsyncIterator[dict[str, Any]]:
        """Gives vectors to every passage that has none; after a change of embedding model, to all of them."""
        workspace = self.store.get(workspace_id)
        status = await self.embedder(fresh=True)
        if not status["ready"]:
            raise WorkspaceError(status["reason"])
        if self._stale(workspace):
            workspace.clear_vectors()
        workspace.save_meta(embed_model=self.config.embed_model)
        todo = workspace.passages_without_vectors()
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            yield {"type": "progress", "message": f"Reading for meaning: {start + len(batch)} of {len(todo)} passages"}
            vectors = await self._embed([text for _, text in batch], self.config.document_prefix)
            await asyncio.to_thread(workspace.set_vectors, [(chunk_id, v) for (chunk_id, _), v in zip(batch, vectors)])
        yield {"type": "done", **self.summary(workspace)}

    # ── search ──
    async def search(self, workspace_id: str, question: str, top_k: int | None = None) -> tuple[list[Hit], str]:
        """The passages that best match a question, and how they were found ("meaning and words" or "words")."""
        workspace = self.store.get(workspace_id)
        vector = None
        if not self._stale(workspace) and workspace.counts()["with_vectors"]:
            status = await self.embedder()
            if status["ready"]:
                try:
                    vector = (await self._embed([question], self.config.query_prefix))[0]
                except (OllamaError, PipelineError):
                    self._status = None
        hits = await asyncio.to_thread(workspace.search, question, vector, top_k or self.config.top_k)
        return hits, "meaning and words" if vector is not None else "words"

    @staticmethod
    def question(messages: list[dict[str, Any]]) -> str:
        """What to search for: the last question, with the one before when it is a short follow-up."""
        asked = [m.get("content") or "" for m in messages if m.get("role") == "user"]
        if not asked:
            return ""
        last = asked[-1]
        if len(last.split()) < 6 and len(asked) > 1:
            return asked[-2] + "\n" + last
        return last

    async def context(self, workspace_id: str, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        """The instructions and passages for a chat reply, and a `sources` event for the page."""
        workspace = self.store.get(workspace_id)
        meta = workspace.meta()
        hits, mode = await self.search(workspace_id, self.question(messages))
        items = [
            {"n": i, "document": h.document, "label": h.label, "matched_by": h.matched_by,
             "text": h.text if len(h.text) <= SNIPPET else h.text[:SNIPPET].rstrip() + " …"}
            for i, h in enumerate(hits, 1)
        ]
        event = {"type": "sources", "workspace": meta["name"], "mode": mode, "items": items}
        return prompts.system(meta["name"], meta.get("instructions") or "", hits), event
