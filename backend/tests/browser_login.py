"""Login and admin pages in a real browser (headless Firefox). Run from backend/:

    pip install selenium            # not a project dependency; a scratch venv is fine
    python tests/browser_login.py

Starts its own server on PORT with a temporary auth database, so port 8000, the real
users and the models are not touched (the app does read llama-server's /health at startup).
"""
import os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
except ImportError:
    sys.exit("selenium is missing: pip install selenium (and Firefox must be installed)")

PORT = int(os.environ.get("PORT", 8010))
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


def main(d, tmp):
    body = lambda: d.find_element(By.TAG_NAME, "body").text
    msg = lambda: d.find_element(By.ID, "msg").text

    def login(email, pw):
        d.get(B + "/login.html")
        d.find_element(By.ID, "email").send_keys(email)
        d.find_element(By.ID, "pw").send_keys(pw)
        d.find_element(By.ID, "go").click()

    for p in ["/", "/documents.html", "/word.html", "/invoices.html", "/admin.html"]:
        d.get(B + p)
        check(f"signed out: {p} goes to login", wait(lambda: "/login.html" in d.current_url))

    login(ADMIN, "wrong-password")
    check("wrong password shows the error", wait(lambda: d.find_element(By.ID, "err").text))
    d.get(B + "/login.html?next=//evil.com")
    d.find_element(By.ID, "email").send_keys(ADMIN)
    d.find_element(By.ID, "pw").send_keys(PW)
    d.find_element(By.ID, "go").click()
    check("login ignores an outside ?next=", wait(lambda: d.current_url.rstrip("/") == B))

    for p in ["/documents.html", "/word.html", "/invoices.html"]:
        d.get(B + p)
        check(f"signed in: {p} is shown", wait(lambda: d.execute_script("return getComputedStyle(document.documentElement).visibility") != "hidden") and p in d.current_url)

    d.get(B + "/admin.html")
    wait(lambda: ADMIN in body())
    form = lambda: d.find_element(By.ID, "add")

    def add(name, email, pw):
        for k, v in [("name", name), ("email", email), ("password", pw)]:
            form().find_element(By.NAME, k).send_keys(v)
        form().find_element(By.CSS_SELECTOR, "button").click()

    row = lambda email, label: d.find_elements(By.XPATH, f"//tr[contains(.,'{email}')]//button[text()='{label}']")
    add("Bob", "Bob@Y.com", "Bob-pass-123")
    check("add user: listed, email lower-cased", wait(lambda: "bob@y.com" in body()))
    add("Dup", "bob@y.com", "Bob-pass-123")
    check("duplicate email: the API's message is shown", wait(lambda: "already registered" in msg()))
    row("bob@y.com", "Disable")[0].click()
    check("disable: button becomes Enable", wait(lambda: row("bob@y.com", "Enable")))
    row("bob@y.com", "Enable")[0].click()
    wait(lambda: row("bob@y.com", "Disable"))
    row("bob@y.com", "Delete")[0].click()
    d.switch_to.alert.accept()
    check("delete: row is gone", wait(lambda: "bob@y.com" not in body()))

    add("Cy", "cy@y.com", "Cy-pass-1234")
    wait(lambda: "cy@y.com" in body())
    d.find_element(By.ID, "out").click()
    check("sign out goes to login", wait(lambda: "/login.html" in d.current_url))
    d.get(B + "/documents.html")
    check("after sign out the pages are guarded", wait(lambda: "/login.html" in d.current_url))
    login("cy@y.com", "Cy-pass-1234")
    wait(lambda: "/login" not in d.current_url)
    d.get(B + "/admin.html")
    check("normal user sees only 'Admins only'", wait(lambda: "Admins only" in body()) and "Add user" not in body())


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "AUTH_DB": f"{tmp}/auth.db", "AUTH_SECRET": "test-secret-not-for-real-use-123456", "ADMIN_EMAIL": ADMIN, "ADMIN_PASSWORD": PW}
    backend = Path(__file__).parent.parent
    venv = backend / ".venv/bin/python"  # the app's own Python; selenium may live in another one
    server = subprocess.Popen([str(venv) if venv.exists() else sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--host", "127.0.0.1"],
                              cwd=backend, env=env, stdout=open(f"{tmp}/server.log", "w"), stderr=subprocess.STDOUT)
    opts = Options()
    opts.add_argument("-headless")
    d = None
    try:
        if not wait(lambda: urllib.request.urlopen(B + "/api/health").status == 200, 60):
            sys.exit(f"server did not start, see {tmp}/server.log")
        d = webdriver.Firefox(options=opts)
        main(d, tmp)
    finally:
        if d:
            d.quit()
        server.terminate()
    print(f"passed {sum(results)} of {len(results)}")
    sys.exit(0 if results and all(results) else 1)
