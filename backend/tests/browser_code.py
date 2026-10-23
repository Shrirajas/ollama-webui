"""Running Python from a chat reply (frontend/codeexec.js + /api/code) in a real browser. Run from backend/:

    pip install selenium            # not a project dependency; a scratch venv is fine
    python tests/browser_code.py

Needs frontend/pyodide/ (python scripts/fetch_pyodide.py). Starts its own server on PORT with a
temporary auth database, so port 8000, the real users and the GPU are not touched (the app does read
llama-server's /health at startup). The first Python run loads Pyodide: allow ~10 s.
"""
import os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
except ImportError:
    sys.exit("selenium is missing: pip install selenium (and Firefox must be installed)")

PORT = int(os.environ.get("PORT", 8011))
B = f"http://127.0.0.1:{PORT}"
ADMIN, PW = "admin@test.com", "Admin-pass-1"
results = []


def check(name, ok):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)


def wait(fn, t=8):
    end = time.time() + t
    while time.time() < end:
        try:
            if (v := fn()):
                return v
        except Exception:
            pass
        time.sleep(0.25)


def main(d):
    d.set_script_timeout(120)
    d.get(B + "/login.html")
    d.find_element(By.ID, "email").send_keys(ADMIN)
    d.find_element(By.ID, "pw").send_keys(PW)
    d.find_element(By.ID, "go").click()
    wait(lambda: "/login" not in d.current_url)
    d.get(B + "/")
    wait(lambda: d.execute_script("return typeof window.runPython === 'function'"))
    run = lambda code, secs=60: d.execute_async_script("const [c, s, done] = arguments; runPython(c, s).then(done);", code, secs)
    api = lambda method, body=None: d.execute_async_script(
        "const [m, b, done] = arguments; fetch('/api/code', {method: m, headers: {'Content-Type': 'application/json'}, body: b && JSON.stringify(b)}).then(r => r.json().then(j => done([r.status, j])));",
        method, body)

    r = run("print(1 + 1)")
    check("print(1 + 1) gives 2", r["stdout"].strip() == "2" and not r["stderr"])
    r = run("import numpy as np\nprint(int(np.arange(5).sum()))")
    check("numpy is loaded from /pyodide/", r["stdout"].strip() == "10" or print("   got:", r))
    r = run("1/0")
    check("an error comes back as stderr", "ZeroDivisionError" in r["stderr"])
    r = run("import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])")
    check("a matplotlib figure comes back as a PNG", r["images"] and r["images"][0].startswith("data:image/png;base64,"))
    r = run("from pyodide.http import pyfetch\nr = await pyfetch('/api/auth/me')\nprint(r.status)")
    check("the code cannot use the login (no 200 from /api/auth/me)", "200" not in r["stdout"])
    r = run("from pyodide.http import pyfetch\nawait pyfetch('https://example.com')\nprint('reached')")
    check("the code cannot reach another address", "reached" not in r["stdout"])
    t = time.time()
    r = run("while True: pass", 3)
    check("an endless loop is stopped on time", "Stopped" in r["stderr"] and time.time() - t < 10)
    check("and the next run still works", run("print('again')")["stdout"].strip() == "again")

    status, cfg = api("GET")
    check("GET /api/code: on, 60 s by default", status == 200 and cfg == {"enabled": True, "timeout": 60})
    status, cfg = api("PUT", {"enabled": False, "timeout": 30})
    check("admin can switch it off", status == 200 and cfg == {"enabled": False, "timeout": 30})
    status, _ = api("PUT", {"enabled": True, "timeout": 0})
    check("a timeout of 0 is refused", status == 422)
    api("PUT", {"enabled": True, "timeout": 60})


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "AUTH_DB": f"{tmp}/auth.db", "AUTH_SECRET": "test-secret-not-for-real-use-123456", "ADMIN_EMAIL": ADMIN, "ADMIN_PASSWORD": PW}
    backend = Path(__file__).parent.parent
    venv = backend / ".venv/bin/python"
    server = subprocess.Popen([str(venv) if venv.exists() else sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--host", "127.0.0.1"],
                              cwd=backend, env=env, stdout=open(f"{tmp}/server.log", "w"), stderr=subprocess.STDOUT)
    opts = Options()
    opts.add_argument("-headless")
    d = None
    try:
        if not wait(lambda: urllib.request.urlopen(B + "/api/health").status == 200, 60):
            sys.exit(f"server did not start, see {tmp}/server.log")
        d = webdriver.Firefox(options=opts)
        main(d)
    finally:
        if d:
            d.quit()
        server.terminate()
    print(f"passed {sum(results)} of {len(results)}")
    sys.exit(0 if results and all(results) else 1)
