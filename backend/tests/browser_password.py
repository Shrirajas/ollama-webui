"""Change-password dialog in a real browser (headless Firefox). Run from backend/:

    pip install selenium            # not a project dependency; a scratch venv is fine
    python tests/browser_password.py

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

PORT = int(os.environ.get("PORT", 8012))
B = f"http://127.0.0.1:{PORT}"
ADMIN, PW, NEW = "admin@test.com", "Admin-pass-1", "Admin-pass-2"
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
    def login(email, pw):
        d.get(B + "/login.html")
        d.find_element(By.ID, "email").send_keys(email)
        d.find_element(By.ID, "pw").send_keys(pw)
        d.find_element(By.ID, "go").click()

    def menu(label):
        d.find_element(By.CSS_SELECTOR, ".sb-user").click()
        wait(lambda: d.find_element(By.CSS_SELECTOR, ".sb-pop"))
        d.find_element(By.XPATH, f"//div[@class='sb-pop']//*[text()='{label}']").click()

    def fill(cur, new, again):
        for inp, v in zip(d.find_elements(By.CSS_SELECTOR, "dialog input"), (cur, new, again)):
            inp.clear(); inp.send_keys(v)
        d.find_element(By.CSS_SELECTOR, "dialog form > div:last-child button:last-child").click()

    msg = lambda: d.find_element(By.CSS_SELECTOR, "dialog .msg").text

    login(ADMIN, PW)
    wait(lambda: "/login" not in d.current_url)
    d.get(B + "/")
    wait(lambda: d.find_element(By.CSS_SELECTOR, ".sb-user"))
    menu("Change password")
    check("user menu opens the dialog", wait(lambda: d.find_element(By.CSS_SELECTOR, "dialog[open]")))

    fill("not-the-password", NEW, NEW)
    check("wrong current password shows the error", wait(lambda: "incorrect" in msg()))
    fill(PW, NEW, NEW + "x")
    check("mismatched new passwords show the error", wait(lambda: "differ" in msg()))
    fill(PW, NEW, NEW)
    check("success shows 'Password changed.'", wait(lambda: msg() == "Password changed."))
    wait(lambda: not d.find_elements(By.CSS_SELECTOR, "dialog"), 5)

    menu("Sign out")
    check("sign out goes to login", wait(lambda: "/login.html" in d.current_url))
    login(ADMIN, PW)
    check("the old password fails", wait(lambda: d.find_element(By.ID, "err").text))
    d.refresh()
    login(ADMIN, NEW)
    check("the new password works", wait(lambda: "/login" not in d.current_url))


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
        main(d)
    finally:
        if d:
            d.quit()
        server.terminate()
    print(f"passed {sum(results)} of {len(results)}")
    sys.exit(0 if results and all(results) else 1)
