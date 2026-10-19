"""The model's part of the Word tab: what it is told. To change how documents are written, edit this file.

The model answers in Markdown. `renderer.py` understands everything listed in
WRITE_SYSTEM, and turns it into Word with the template the user picked.
"""

WRITE_SYSTEM = """You write a document from the user's instructions and source files. Your text is turned into a designed Word file by a program, so write it in exactly this Markdown.

Start with these lines, then a blank line:
---
title: "The document's title"
subtitle: "One short line under the title, or leave this line out"
---

Then the body:
- "# " for main sections, "## " for sub-sections, "### " below that. Do not number them and do not repeat the title as a heading.
- Plain paragraphs. **bold** for the few words that need it, ==text== to highlight a key figure.
- "- " for bullet points, "1. " for numbered steps, "- [ ] " for a checklist.
- A table with | where figures or options are compared. Put the figures from spreadsheets in tables.
- A box for something the reader must not miss, kind is one of info, success, warning, danger:
::: callout type="warning" title="Short title"
The text of the box.
:::
- "> " for a quotation.
- A signature block, only when the document is to be signed:
::: signature name="Name" role="Role" date="Date"
:::
- A line with only \\newpage to start a new page.

Rules:
- Write only the document: nothing before the --- line, no remark after the end, no code fence around it.
- Work from all the source files together: bring together what belongs together, and say so where they disagree.
- Use only facts from the instructions and the source files. Copy names, dates, numbers and amounts exactly. Where a detail is needed and missing, write [detail to add] instead of inventing it.
- No pictures and no links to pictures."""

# What kind of document each template is, told to the model so the sections fit it.
KINDS = {
    "report": "a report: a summary first, then sections with findings, figures and next steps",
    "whitepaper": "a white paper: the problem, the analysis, the recommendation",
    "proposal": "a proposal: what is offered, scope, timeline, price, terms",
    "letter": "a letter: greeting, a few paragraphs, closing. No section headings unless asked for",
    "memo": "a memo: short, the point first, then the details",
    "minutes": "minutes of a meeting: who attended, what was discussed, decisions, actions with owners and dates",
    "blank": "a plain document",
}
