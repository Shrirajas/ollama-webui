"""The Word tab: several source files in, one designed Word document out.

Separate from the Word code in `engine/` and `model/` and from `invoices/`; it
shares the model registry, the Ollama client and the `documents:` settings.

  sources.py   a source file (PDF, Word, Excel, CSV, text) -> its text       no model
  renderer.py  Markdown with frontmatter -> a designed .docx: templates,
               cover, numbered headings, tables, callouts, signatures, TOC   no model
  build.py     the page's choices + the Markdown -> the finished file         no model
  prompts.py   what the model is told, and the Markdown it may write          the model's part
  service.py   sends the sources to the local model, streams what it writes   the model's part

The model writes Markdown only. It never writes the Word file.

Check it without a model:  cd backend && python -m document_engine.word
"""

from .build import TEMPLATES, build_docx
from .service import WordService
from .sources import ACCEPTED, read_source

__all__ = ["ACCEPTED", "TEMPLATES", "WordService", "build_docx", "read_source"]
