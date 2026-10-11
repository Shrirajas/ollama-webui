"""The HTTP routes of the Word tab, under /api/documents/word.

  /config        models, templates, accepted source files, whether a PDF can be made
  /source        one source file in -> its text (PDF, Word, Excel, CSV, text)
  /create        sources + instructions -> the document, written live, then built
  /build         Markdown + the page's choices -> the Word file again, without the model
  /{job}/pdf     the newest version of a document as a PDF to download

The work itself is in the top-level `documents/` folder (`document_engine.word`).
A finished document is an ordinary job of the documents page: it is downloaded
through /api/documents/{job}/download and changed through /api/documents/{job}/edit.
"""

import re
import shutil
from collections.abc import AsyncIterator
from typing import Any

from document_engine import WordFile, tidy_markdown
from document_engine.word import ACCEPTED, TEMPLATES, build_docx, read_source
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from ollama_pipeline import PipelineError, UnsupportedDocument
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .routes import MAX_UPLOAD_BYTES, _download, _pdf, _stream
from .schemas import Source

router = APIRouter()


class Options(BaseModel):
    template: str = "report"
    accent: str = Field(default="", description="The colour the design is built on, as #RRGGBB; empty = the template's own")
    cover: bool | None = Field(default=None, description="A cover page; left out = as the template has it")
    toc: bool = Field(default=False, description="A contents page")
    title: str = ""
    subtitle: str = ""
    author: str = ""


class WriteRequest(BaseModel):
    model: str | None = None
    instructions: str = ""
    sources: list[Source] = Field(default_factory=list, max_length=30)
    options: Options = Field(default_factory=Options)
    letterhead: str | None = Field(default=None, description="An uploaded file (its job id) whose header, footer and margins the document takes")


class BuildRequest(BaseModel):
    markdown: str = Field(max_length=400_000)
    options: Options = Field(default_factory=Options)
    letterhead: str | None = None


def _office() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


async def _built(request: Request, markdown: str, options: Options, letterhead: str | None) -> dict[str, Any]:
    """Builds the Word file and stores it as a new document."""
    store = request.app.state.store
    base = store.read(letterhead, 0)[:2] if letterhead else None
    data, title = await run_in_threadpool(build_docx, markdown, options.model_dump(), base)
    job = store.create(f"{title}.docx", data, "Written")
    preview = await run_in_threadpool(lambda: WordFile(title, data).preview())
    return {
        "job": job, "version": 0, "name": f"{title}.docx", "download": _download(job, 0),
        "pdf": f"/api/documents/word/{job}/pdf" if _office() else None, "preview": preview, "markdown": markdown,
    }


@router.get("/config")
async def config(request: Request):
    service = request.app.state.word
    default, models = await service.models()
    return {
        "default": default, "models": models, "accepted": list(ACCEPTED),
        "templates": [{"id": key, "label": label, "about": about} for key, (label, about) in TEMPLATES.items()],
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "keep_hours": service.config.keep_hours,
        # A PDF is made by LibreOffice from the Word file; without it only Word can be downloaded.
        "pdf": bool(_office()),
    }


@router.post("/source")
def source(file: UploadFile):
    """The text of one source file, for the model to read."""
    name = file.filename or "file"
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        content, truncated = read_source(name, data)
    except UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(500, f"Reading {name} needs the {exc.name} package on the server: run `pip install -r requirements.txt`.") from exc
    return {"name": name, "content": content, "truncated": truncated, "chars": len(content)}


@router.post("/create")
async def create(body: WriteRequest, request: Request):
    """Streams the document as the model writes it, then builds the Word file from it."""
    sources = [s.model_dump() for s in body.sources]
    if body.letterhead:
        # Checked before the model starts: finding out after it has written everything wastes the wait.
        base = request.app.state.store.read(body.letterhead, 0)[:2]
        if not await run_in_threadpool(lambda: WordFile(*base).is_letterhead()):
            raise HTTPException(409, f"{base[0]} is a designed template, not a letterhead. Use Fill a template so its layout is kept.")

    async def events() -> AsyncIterator[dict]:
        written = ""
        async for event in request.app.state.word.write(body.model, body.instructions, sources, body.options.template):
            if event["type"] == "delta":
                written += event["content"]
            yield event
        markdown = tidy_markdown(written)
        if not markdown:
            raise PipelineError("The model wrote nothing. Try again or pick another model.")
        yield {"type": "progress", "message": "Building the Word file"}
        yield {"type": "result", **await _built(request, markdown, body.options, body.letterhead)}

    return _stream(events())


@router.post("/build")
async def build(body: BuildRequest, request: Request):
    """The Word file from the text as it stands on the page: after editing it, or with another template."""
    try:
        return await _built(request, tidy_markdown(body.markdown), body.options, body.letterhead)
    except (PipelineError, UnsupportedDocument) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{job}/pdf")
def pdf(job: str, request: Request, inline: bool = False):
    """The newest version of the document as a PDF. Made on request, so a changed document gives a new one.
    `inline` shows it on the page instead of saving it."""
    store = request.app.state.store
    name, _, version = store.read(job)
    # shortcut: one LibreOffice run per request; two at the same moment can fail and the user retries. Queue them if that shows up.
    if not _pdf(request, job):
        raise HTTPException(503, "A PDF needs LibreOffice on the server (the `soffice` command). Download the Word file instead.")
    stem = re.sub(r"\.(docx|dotx)$", "", name, flags=re.IGNORECASE)
    return FileResponse(
        store.root / job / f"v{version}.pdf", media_type="application/pdf", filename=f"{stem}.pdf",
        content_disposition_type="inline" if inline else "attachment",
    )
