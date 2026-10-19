"""The Markdown the model wrote + what the user chose on the page -> the finished Word file. No model here.

The page's choices win over anything the model put in the frontmatter: the
template, the accent colour, cover and contents page are the user's to set.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from io import BytesIO
from typing import Any

from ollama_pipeline import PipelineError

from ..engine.wordfile import WordFile, as_docx
from . import renderer

# The templates of the renderer, in the order the page lists them: id -> (label, what it is for).
TEMPLATES = {
    "report": ("Report", "Cover, numbered sections, page numbers"),
    "proposal": ("Proposal", "Cover, sections, page numbers"),
    "whitepaper": ("White paper", "Editorial cover, numbered sections"),
    "memo": ("Memo", "Short, no cover"),
    "minutes": ("Minutes", "Meeting notes, no cover"),
    "letter": ("Letter", "Wider margins, no headings rules"),
    "blank": ("Plain", "No cover, no numbering"),
}

_HEX = re.compile(r"#?[0-9a-fA-F]{6}")


def _text(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    return value.strip()[:200] if isinstance(value, str) else ""


def _spec(markdown: str, options: dict[str, Any], letterhead: tuple[str, bytes] | None) -> dict[str, Any]:
    if not renderer._HAS_MD_PARSER:
        raise PipelineError(
            "The Word tab needs packages that are not installed on the server (markdown-it-py, mdit-py-plugins, PyYAML). "
            "Run `pip install -r requirements.txt` in the project folder (or `uv sync --extra router --inexact` in backend/), then restart."
        )
    try:
        spec = renderer.parse_content(markdown)
    except ValueError as exc:  # only text that starts like JSON and is not
        raise PipelineError("The text could not be read. It must be Markdown.") from exc
    if not isinstance(spec, dict) or not spec.get("blocks"):
        raise PipelineError("There is nothing to put in the document yet.")

    if options.get("template") in TEMPLATES:
        spec["template"] = options["template"]
    for key in ("title", "subtitle", "author"):
        if _text(options, key):
            spec[key] = _text(options, key)
    accent = _text(options, "accent")
    if _HEX.fullmatch(accent):
        styles = spec["styles"] if isinstance(spec.get("styles"), dict) else {}
        spec["styles"] = {**styles, "accent": "#" + accent.lstrip("#")}
    # A letterhead is the file the user attached, never a name the model wrote.
    for key in ("letterhead", "sample", "base_docx"):
        spec.pop(key, None)

    spec = renderer._resolve_template(spec)
    if letterhead:
        name, data = letterhead
        if not WordFile(name, data).is_letterhead():
            # Its body is the design: replacing it would throw the layout away.
            raise PipelineError(f"{name} is a designed template, not a letterhead. Use Fill a template so its layout is kept.")
        spec["letterhead"], spec["_letterhead_bytes"], spec["cover"] = name, as_docx(name, data), None
    elif options.get("cover") is False:
        spec["cover"] = None
    elif options.get("cover") is True and not isinstance(spec.get("cover"), dict):
        spec["cover"] = "auto"

    blocks = spec["blocks"]
    first = blocks[0] if isinstance(blocks[0], dict) else {}
    if not spec.get("title") and first.get("type") == "heading" and first.get("level") == 1:
        spec["title"] = re.sub(r"[*_=`]", "", str(first.get("text") or "")).strip()
        if spec.get("cover") and len(blocks) > 1:
            blocks.pop(0)  # the cover carries it; the body should not say it twice
    if spec.get("cover") and not spec.get("date"):
        spec["date"] = date.today().strftime("%d %B %Y").lstrip("0")
    if options.get("toc") and not any(isinstance(b, dict) and b.get("type") == "toc" for b in blocks):
        blocks.insert(0, {"type": "toc", "title": "Contents"})
    return spec


def _list_contents(document, spec: dict[str, Any]) -> None:
    """Puts the section names where the contents page only has "update this field".

    Word works the real contents out, with page numbers, when the file is
    opened. A PDF made from the file is never opened in Word, and would show
    that instruction to the reader.
    """
    names = [
        "\u00a0" * 4 * (b["level"] - 1) + re.sub(r"[*_=`]", "", str(b.get("text") or "")).strip()
        for b in spec["blocks"] if isinstance(b, dict) and b.get("type") == "heading" and b.get("level") in (1, 2)
    ]
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            if run.text.startswith("Right-click and select"):
                run.text = "\n".join(names)


def build_docx(
    markdown: str, options: dict[str, Any] | None = None, letterhead: tuple[str, bytes] | None = None
) -> tuple[bytes, str]:
    """Returns the .docx and a file name (without extension) made from its title.

    Call it from a worker thread: the renderer is async and gets a loop of its own here.
    """
    spec = _spec(markdown, options or {}, letterhead)
    try:
        document = asyncio.run(renderer.build_document(spec))
    except ValueError as exc:
        raise PipelineError(f"The Word file could not be built: {exc}") from exc
    _list_contents(document, spec)
    out = BytesIO()
    document.save(out)
    return out.getvalue(), renderer._human_filename(renderer._document_title(spec))
