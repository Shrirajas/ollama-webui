"""A check of workspaces without a model:  cd backend && python -m workspace_engine

A stand-in embedding model (words hashed into a vector) shows that search by
meaning and by words combine; a missing one shows that words alone still work.
"""

import asyncio
import hashlib
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from ollama_pipeline import OllamaUnavailable, load_config

from .chunking import split
from .service import WorkspaceService

LEAVE = """Leave policy

Every employee gets 24 days of paid leave a year. Leave must be asked for in the HR portal at least two weeks ahead.

Sick leave is separate: up to 12 days a year with a doctor's note after the second day.

Parental leave is 26 weeks for the birth parent and 4 weeks for the other parent."""

TRAVEL = """Travel policy

Flights over 6 hours may be booked in premium economy. Hotels up to 9,000 rupees a night in metro cities.
Claims go in within 30 days of the trip, with receipts."""


class StandIn:
    """An embedding 'model': each word sets one of 64 positions."""

    def __init__(self, up: bool = True):
        self.up = up

    async def embed(self, model, texts):
        if not self.up:
            raise OllamaUnavailable("not running")
        vectors = []
        for text in texts:
            v = [0.0] * 64
            for word in re.findall(r"\w+", text.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
            vectors.append(v)
        return vectors


class Registry:
    def __init__(self, client):
        config = load_config()
        self.config = replace(config, workspaces=replace(config.workspaces, embed_model="stand-in", query_prefix="", document_prefix=""))
        self._client = client

    def client(self, host):
        return self._client


async def collect(stream):
    return [event async for event in stream]


def main() -> None:
    long = " ".join(f"Sentence number {i} is here." for i in range(400))
    pieces = split(long, 500, 100)
    assert all(len(p) <= 500 for p in pieces) and len(pieces) > 5
    last = pieces[0].rsplit(". ", 1)[-1]
    assert last in pieces[1] and f" {pieces[1].split()[0]} " in f" {long} "  # the overlap is kept, and starts at a word

    with tempfile.TemporaryDirectory() as tmp:
        service = WorkspaceService(Registry(StandIn()), Path(tmp))
        hr = service.create("HR policies", "Answer for employees in India.")
        try:
            service.create("hr policies")
            raise AssertionError("a second workspace with the same name was made")
        except Exception as exc:
            assert "already exists" in str(exc)

        # Names in any language: the id is ASCII, the name keeps its letters.
        for name in ("ಮಾನವ ಸಂಪನ್ಮೂಲ", "Finanzas año 2026"):
            made = service.create(name)
            assert made["name"] == name and service.store.get(made["id"]), made
        assert sorted(w["name"] for w in service.list()) == ["Finanzas año 2026", "HR policies", "ಮಾನವ ಸಂಪನ್ಮೂಲ"]
        for w in service.list():
            if w["id"] != hr["id"]:
                service.delete(w["id"])

        events = asyncio.run(collect(service.add(hr["id"], "leave.txt", LEAVE.encode(), [("", LEAVE)], False)))
        assert events[-1]["type"] == "added" and events[-1]["by_meaning"], events
        events = asyncio.run(collect(service.add(hr["id"], "travel.pdf", TRAVEL.encode(), [("page 1", TRAVEL)], False, 1)))
        assert events[-1]["passages"] == 1
        again = asyncio.run(collect(service.add(hr["id"], "leave.txt", LEAVE.encode(), [("", LEAVE)], False)))
        assert again[-1]["type"] == "skipped"

        hits, mode = asyncio.run(service.search(hr["id"], "How many days of sick leave do I get?"))
        assert mode == "meaning and words" and "Sick leave" in hits[0].text, [h.text[:40] for h in hits]
        hits, _ = asyncio.run(service.search(hr["id"], "hotel limit per night"))
        assert hits[0].document == "travel.pdf" and hits[0].label == "page 1"

        system, event = asyncio.run(service.context(hr["id"], [
            {"role": "user", "content": "What is the hotel limit in metro cities?"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "and for flights?"},
        ]))
        assert "[1] travel.pdf, page 1" in system and "Answer for employees in India." in system
        assert event["items"][0]["document"] == "travel.pdf"

        # The embedding model goes away: words still find passages, and new files are still added.
        down = WorkspaceService(Registry(StandIn(up=False)), Path(tmp))
        hits, mode = asyncio.run(down.search(hr["id"], "parental leave weeks"))
        assert mode == "words" and "Parental" in hits[0].text
        events = asyncio.run(collect(down.add(hr["id"], "notes.txt", b"Office opens at 9.", [("", "Office opens at 9.")], False)))
        assert events[-1]["type"] == "added" and not events[-1]["by_meaning"] and events[-1]["note"]
        assert service.summary(service.store.get(hr["id"]))["missing_vectors"] == 1
        done = asyncio.run(collect(service.reindex(hr["id"])))
        assert done[-1]["missing_vectors"] == 0

        system, event = asyncio.run(service.context(hr["id"], [{"role": "user", "content": "zzzz qqqq"}]))
        assert event["items"] == [] or "not" in system

        doc = next(d for d in service.store.get(hr["id"]).documents() if d["name"] == "leave.txt")
        assert service.remove(hr["id"], doc["id"])
        hits, _ = asyncio.run(service.search(hr["id"], "sick leave doctor"))
        assert all(h.document != "leave.txt" for h in hits)
        service.delete(hr["id"])
        assert service.list() == []

    print("Workspaces: splitting, adding, search by meaning and words, words alone, re-index and removal all pass.")


if __name__ == "__main__":
    main()
