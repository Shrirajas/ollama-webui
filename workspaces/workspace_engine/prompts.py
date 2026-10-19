"""What the chat model is told when a workspace is picked. Edit here only."""

from __future__ import annotations

from .index import Hit

ANSWER = """\
You are answering from the documents in the workspace "{name}".{instructions}
Base the answer on the numbered passages below. After each statement that comes from a passage, cite it by its
number in square brackets, like [2]. If the passages do not contain the answer, say that the workspace's documents
do not cover it; do not make an answer up. Quote figures, dates and names exactly as the passages give them.

{passages}"""

NOTHING = """\
You are answering from the documents in the workspace "{name}".{instructions}
No passage in its documents matched this question. Say so plainly, suggest how the person could rephrase it
or which document might be missing, and do not make an answer up."""


def where(hit: Hit) -> str:
    return f"{hit.document}, {hit.label}" if hit.label else hit.document


def system(name: str, instructions: str, hits: list[Hit]) -> str:
    extra = f"\nThe workspace's own instructions: {instructions.strip()}" if instructions.strip() else ""
    if not hits:
        return NOTHING.format(name=name, instructions=extra)
    passages = "\n\n".join(f"[{i}] {where(h)}\n{h.text}" for i, h in enumerate(hits, 1))
    return ANSWER.format(name=name, instructions=extra, passages=passages)
