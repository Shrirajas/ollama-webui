"""/api/files for PDFs: a picture-only PDF comes back as images, a text PDF is kept on the server. Run from backend/:

    .venv/bin/python tests/scan_route.py

No server, no model: it calls the route's functions directly.
"""
import asyncio, io, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
from PIL import Image, ImageDraw
from fastapi import UploadFile
from app.routes import MAX_SCAN_PAGES, add_file
from ollama_pipeline import Registry, load_config
from workspace_engine import ChatFiles, Embedder

registry = Registry(load_config())
files = ChatFiles(registry, Path(tempfile.mkdtemp()) / "chatfiles", Embedder(registry))
request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chatfiles=files, registry=registry)))


def scan_pdf(pages):
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(im).text((60, 60), f"Invoice scan page {i + 1}", fill="black")
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def text_pdf(text):
    # shortcut: hand-made one-page PDF with a real text layer; no PDF writer is a project dependency
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


def call(name, data):
    return asyncio.run(add_file(request, UploadFile(file=io.BytesIO(data), filename=name)))


results = []
def check(name, ok):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name)


r = call("scan.pdf", scan_pdf(2))
check("scan: images returned", len(r.get("images", [])) == 2)
check("scan: note names the file", "scan.pdf" in r.get("note", ""))
check("scan: no text content", r["content"] == "")
check("scan: images are JPEG", __import__("base64").b64decode(r["images"][0])[:2] == b"\xff\xd8")

n = MAX_SCAN_PAGES + 2
r = call("long.pdf", scan_pdf(n))
check(f"long scan: capped at {MAX_SCAN_PAGES} pages", len(r["images"]) == MAX_SCAN_PAGES)
check("long scan: note says how many were attached", f"first {MAX_SCAN_PAGES} of {n}" in r["note"])

r = call("text.pdf", text_pdf("Hello invoice text " * 20))
check("text pdf: kept on the server, its text not sent back", r.get("id") and "content" not in r and files.get(r["id"]))
check("text pdf: no images", "images" not in r)

print(f"passed {sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
