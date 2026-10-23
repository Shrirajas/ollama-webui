"""Copies the Pyodide runtime (+ the listed packages and their dependencies) into frontend/pyodide/.

Usage: python scripts/fetch_pyodide.py [SRC] [package ...]
SRC is a folder holding pyodide.js and pyodide-lock.json; default: the Open WebUI reference copy.
The files are not in git (frontend/pyodide/ is ignored); run this once on each machine.
"""
import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "reference-openwebui-for-main-project/open-webui/static/pyodide"
wanted = sys.argv[2:] or ["numpy", "pandas", "matplotlib"]
out = root / "frontend/pyodide"

lock = json.loads((src / "pyodide-lock.json").read_text())["packages"]
names, todo = set(), list(wanted)
while todo:
    n = todo.pop().lower().replace("_", "-")
    if n in names:
        continue
    names.add(n)
    todo += lock[n]["depends"]  # KeyError = a package this Pyodide does not have

out.mkdir(parents=True, exist_ok=True)
files = ["pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json", "package.json"]
files += [lock[n]["file_name"] for n in sorted(names)]
for f in files:
    shutil.copy2(src / f, out / f)
print(f"{len(files)} files, {sum((out / f).stat().st_size for f in files) / 1e6:.0f} MB in {out}")
