"""Chat page: Knowledge + Database pills, and a Sources link opening the workspace viewer, in a real browser
(headless Firefox). Runs against an already running test instance and loads no model: /api/chat is stubbed
in the page, but the workspace, document ids and section labels come from the instance's real API. Run from backend/:

    pip install selenium            # not a project dependency; a scratch venv is fine
    SOURCES_EMAIL=<user> SOURCES_PW=<password> python tests/browser_sources.py

Needs a signed-in-able user, one workspace with a PDF (page labels) and one saved database on the instance.
Never point it at the live site: PORT defaults to 8002.
"""
import json, os, sys, time
from urllib.parse import quote

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
except ImportError:
    sys.exit("selenium is missing: pip install selenium (and Firefox must be installed)")

PORT = int(os.environ.get("PORT", 8002))
B = f"http://127.0.0.1:{PORT}"
EMAIL, PW = os.environ.get("SOURCES_EMAIL"), os.environ.get("SOURCES_PW")
results = []


def check(name, ok):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, flush=True)


def wait(fn, t=8):
    end = time.time() + t
    while time.time() < end:
        try:
            if (v := fn()):
                return v
        except Exception:
            pass
        time.sleep(0.25)


# Runs in the page: answers /api/chat with the given events and remembers the request body.
STUB = """
window.__sent = null;
const real = window.fetch, events = arguments[0];
window.fetch = (url, opts) => {
  if (url !== '/api/chat') return real(url, opts);
  window.__sent = JSON.parse(opts.body);
  return Promise.resolve(new Response(events.map((e) => JSON.stringify(e)).join('\\n') + '\\n'));
};
"""


def main(d):
    api = lambda path: d.execute_script("return fetch(arguments[0]).then(r => r.json())", path)

    d.get(B + "/login.html")
    d.find_element(By.ID, "email").send_keys(EMAIL)
    d.find_element(By.ID, "pw").send_keys(PW)
    d.find_element(By.ID, "go").click()
    wait(lambda: "/login" not in d.current_url)
    d.get(B + "/")
    wait(lambda: d.find_element(By.CSS_SELECTOR, ".sb-user"))
    time.sleep(1.5)

    # A real workspace document with a page label, and a real saved database, from the instance itself.
    ws = doc = label = None
    for w in api("/api/workspaces")["workspaces"]:
        for x in api(f"/api/workspaces/{w['id']}")["documents"]:
            if x["name"].lower().endswith(".pdf"):
                label = next((s["label"] for s in api(f"/api/workspaces/{w['id']}/documents/{x['id']}")["sections"] if s["label"]), None)
                if label:
                    ws, doc = w, x
                    break
        if doc:
            break
    dbs = api("/api/sql/connections")["connections"]
    if not (doc and dbs):
        sys.exit("needs a workspace with a PDF and a saved database on the instance")
    db = dbs[0]

    d.execute_script(STUB, [
        {"type": "route", "model": "gemma", "label": "Gemma", "source": "picked", "route": None, "confidence": None},
        {"type": "sources", "workspace": ws["name"], "workspace_id": ws["id"], "mode": "words",
         "items": [{"n": 1, "document": doc["name"], "doc": doc["id"], "label": label, "text": "passage"}]},
        {"type": "delta", "content": "An answer [1]."},
        {"type": "done", "stats": {}},
    ])

    # 1-2: Knowledge AND Database picked together: both pills, both in the request
    d.find_element(By.ID, "model-btn").click()
    wait(lambda: ws["name"] in d.find_element(By.ID, "model-menu").text)
    d.find_element(By.XPATH, f"//div[@id='model-menu']//span[text()={json.dumps(ws['name'])}]").click()
    d.find_element(By.ID, "model-btn").click()
    wait(lambda: db["id"] in d.find_element(By.ID, "model-menu").text)
    d.find_element(By.XPATH, f"//div[@id='model-menu']//span[text()={json.dumps(db['id'])}]").click()
    pills = d.find_element(By.ID, "attachments").text
    check("both pills show", f"Knowledge: {ws['name']}" in pills and f"Database: {db['id']}" in pills)
    d.find_element(By.ID, "prompt").send_keys("who sold most, and what does the policy say?")
    d.find_element(By.ID, "send").click()
    sent = wait(lambda: d.execute_script("return window.__sent"))
    check("one request carries workspace and database", sent and sent.get("workspace") == ws["id"] and sent.get("database") == db["id"])

    # 3-5: the reply's Sources link opens the viewer on that document, at that page
    wait(lambda: d.find_element(By.CSS_SELECTOR, ".msg.latest .acts"))
    links = d.find_elements(By.CSS_SELECTOR, ".msg.bot .think-text a")
    want = f"/workspaces.html#{quote(ws['id'], safe='')}/doc/{doc['id']}?label={quote(label, safe='')}"
    check("source is a link to the viewer", len(links) == 1 and d.execute_script("return arguments[0].getAttribute('href')", links[0]) == want)
    d.execute_script("arguments[0].closest('details').open = true", links[0])
    links[0].click()
    wait(lambda: len(d.window_handles) == 2)
    d.switch_to.window(d.window_handles[1])
    check("viewer shows the linked document",
          wait(lambda: d.execute_script("return document.getElementById('viewDlg').open"), 15)
          and wait(lambda: d.find_element(By.ID, "v-title").text == doc["name"]))
    check("section of the source's page is marked",
          wait(lambda: d.execute_script("const h = document.querySelector('#v-body .sec.at h3'); return h && h.textContent"), 10) == label)
    d.switch_to.window(d.window_handles[0])
    d.execute_script("fetch('/api/chats/' + new URLSearchParams(location.search).get('c') + '/delete', {method: 'POST'})")
    time.sleep(0.5)


if not (EMAIL and PW):
    sys.exit("set SOURCES_EMAIL and SOURCES_PW (a user on the test instance)")
opts = Options()
opts.add_argument("-headless")
driver = webdriver.Firefox(options=opts)
driver.set_window_size(1440, 900)
try:
    main(driver)
finally:
    driver.quit()
print(f"browser_sources: {sum(results)}/{len(results)} passed")
sys.exit(0 if len(results) == 5 and all(results) else 1)
