# AGENTS.md

Instructions for an AI coding agent working on this repo on a machine it has
not seen before. Read this first, then `PROJECT_FLOW.txt` for the code map.

## What this is

A local chat web app on top of Ollama. A user picks a model (or "Auto"), sends
a prompt with optional PDF/DOCX/text/image attachments, and the reply streams
back. With "Auto", a router sends each message to a light model, a reasoning
model or a vision model on this machine, or, for questions about software, to
OpenCode's free cloud models.

The app runs on one of two model servers, chosen by `PIPELINE` in
`backend/.env`:
- **Ollama** (default, `ollama/`): Auto, OpenCode, every Ollama model.
- **llama-server** (`PIPELINE=llamacpp`, `llamacpp/`): llama.cpp in Docker
  with two models, `llama3.1:8b` and `gemma4:12b`, and Auto between them (Von/rules,
  never OpenCode). Chat uses either; Documents, Word and Invoices use gemma4. Used on the production
  server since 13 October 2026; see "Running the `12-llamacpp` branch" below.

## Running the `12-llamacpp` branch on the server

This branch adds a second pipeline, `llamacpp/` (package `llamacpp_pipeline`):
the whole app answered by llama.cpp's `llama-server` instead of Ollama, with two
models; Auto routes between them (`router:` block in
`llamacpp/config/models.yaml`, no cloud). Chat: `llama3.1:8b` (default) or `gemma4:12b`.
Documents page, Word tab and Invoices tab: `gemma4:12b`. `ollama/` is
unchanged; with the switch off the app runs exactly as on `11-word` (Auto,
OpenCode, Ollama models).

- `llamacpp/llamacpp_pipeline/client.py` talks to llama-server's OpenAI-style
  API and hands back Ollama's shapes, so the chat steps and the documents
  services are used as they are. `registry.py` is Ollama's registry with that
  client. `llamacpp/config/models.yaml` lists the models and which one the
  documents and invoices use.
- `llamacpp/server/start.sh` starts a container of its own,
  `llama-server-webui` on `127.0.0.1:8101`, in router mode with the models in
  `llamacpp/server/models.ini`. It reads Ollama's model files read-only. The
  other `llama-server` container on this machine is not used or changed. The
  container has no restart policy: after a reboot run `start.sh` again.
- `backend/.env`: `LLAMACPP_API_KEY=<random>` (the container and the app use
  it) and `PIPELINE=llamacpp`. Without that line: Ollama.
- Check: `cd backend && python -m llamacpp_pipeline` lists both models as ready.
- **Reply rendering** (`render` code in `frontend/index.html`, `paintBody`): Markdown by `marked`, code by
  highlight.js, both copied into `frontend/vendor/` (committed; highlight.js is esbuild's bundle of
  `highlight.js/lib/common.js` from the reference's `node_modules`). Raw HTML in a reply is shown as text. Each code
  block has Open WebUI's bar (Collapse, Run, Copy), line numbers and the run output; under each finished reply is Open WebUI's button
  row (`addActions`/`act`): edit, copy, read aloud (browser speech), info, good/bad (`rating` kept in the saved
  message), continue and regenerate (newest reply only, `reply(history, prior)`), fork chat (new saved chat up to
  that reply). Only the block still being written is rebuilt, so collapse state and Run output survive.
- **Run button on python code blocks** (runs in the user's browser with Pyodide, not
  on the server). Once per machine: `python scripts/fetch_pyodide.py` (fills
  `frontend/pyodide/`, git-ignored). Admin Panel -> Code (`/api/code`, table
  `app_setting` in `auth.db`) switches it on/off and sets the time limit. Details and
  limits: `anaylze-code-and-routing.md`.

Rules to keep:
- **One model server on the GPU at a time.** llama-server held 14.7 of 16 GB
  with both models at ctx 16384 (llama3.1:8b 5.9 GB, gemma4:12b with vision 8.7 GB).
  With llama3.1:8b at 24576 (`models.ini` since then): 6.5 + 8.8 GB, 15.4 of 16 GB
  used on the card (`nvidia-smi`, 16 October 2026).
  Ollama and llama-server do not see each other's memory: a model loaded in
  Ollama meanwhile fails with "CUDA out of memory" (seen with qwen-fast and
  gemma4), and the other way round.
- gemma4 runs with `think: false`: with thinking on, its reasoning filled the
  16k context before a long document was written.
- Each model's ctx-size is one pool shared by its slots (kv-unified). A new
  ~14k-token Word job and an invoice page at the same moment come close to
  gemma's 16384; if "Context size has been exceeded" shows up, raise gemma's
  ctx-size (about 1.6 GB of the card is still free) or lower `parallel`, in
  Admin Panel -> Models (see "Admin Panel -> Models" below).

Measured on the server (RTX 4080 Super), 13 October 2026:
- Chat: "hey" answered by llama3.1:8b in 0.1 s (with Auto on Ollama it went to
  qwen-fast, because llama3.2:3b is not pulled here). 125 tokens/s alone; 8
  chats at once all had their first word within 0.4 s, also while invoices
  and a Word job ran on gemma.
- Word tab, 7 invoice PDFs into one report: llama3.1:8b in 11.5 s dropped a
  file, miscopied a total and repeated sections; gemma4 in 12 s had every
  file and every figure right. Both got the sum of the totals wrong: a model's
  arithmetic is not to be trusted. A scanned PDF is refused, as before.
- Invoices: all 54 files of `test-images/` (two folders) read by gemma4 on
  llama-server, no errors: 147 documents (45 invoices, 78 receipts, 24
  payments), 240 sheet rows, 922 s, about 6.4 s a page, average confidence
  0.92, 38 values marked to check. Not compared with Ollama's gemma4 on the
  same files: both cannot be on the card at once.

## Running the `11-word` branch on the server

This branch adds the **Word document** tab to the documents page: several
source files in (PDF, Word, Excel, CSV, text), one designed Word document out,
downloaded as Word or PDF. It contains `10-invoice`. On a machine that already
runs the app:

1. **Get the branch.** `git fetch && git checkout 11-word`
2. **Install one new package** (`mdit-py-plugins`; `markdown-it-py` and
   `PyYAML` come with it or are already there):
   `pip install -r requirements.txt` from the repo root, or with uv
   `cd backend && uv sync --extra router --inexact`. Without it the app still
   starts; the tab then says which packages are missing.
3. **Nothing to add to `models.yaml`.** The tab uses the `documents:` block
   that is already there (`documents.model`, `qwen3:14b`).
4. **For the PDF download, LibreOffice** must be on the server
   (`soffice --version`). Without it the tab offers the Word file only and
   says why. Ask the user before installing it.
5. **Restart** and open <http://127.0.0.1:8000/word.html>, or Docs -> Word
   document. Refresh the browser with Ctrl+Shift+R.
6. **Check it without a model:** `cd backend && python -m document_engine.word`
   must print that sources, sharing, building and letterhead all pass.

What to check is under "Word document tab" below, in "Not done".

## Running the `10-invoice` branch on the server

This branch adds the **Invoices** tab to the documents page: invoices in (PDF,
Word, scans, photos), an Excel or CSV sheet out. It contains `6-documents` and
`8-testing`. On a machine that already runs the app:

1. **Get the branch.** `git fetch && git checkout 10-invoice`
2. **Install three new packages** (`pypdfium2`, `pillow`, `openpyxl`):
   `pip install -r requirements.txt` from the repo root, or with uv
   `cd backend && uv sync --extra router --inexact`. Without them the app still
   starts; the Invoices tab then says which package is missing.
3. **The model must read pictures.** `ollama/config/models.yaml` names
   `qwen3.5:9b` (entry `id: invoices`). `ollama show qwen3.5:9b` must list
   `vision` under Capabilities. `gemma4:12b` reads pictures too and can be
   picked on the page to compare.
4. **If this machine has `ollama/config.local/models.yaml`,** copy the
   `invoices:` block and the `- id: invoices` model entry into it. Without them
   the tab still works: it starts with the first local model that reads
   pictures (by name, so `gemma4:12b` before `qwen3.5:9b`).
5. **Restart** (`cd backend && python -m app`, or `./run.sh`) and open
   <http://127.0.0.1:8000/invoices.html>, or Docs -> Invoices. Refresh the
   browser with Ctrl+Shift+R: it keeps the old page otherwise.
6. **Or from a terminal,** a whole folder at once:
   `cd backend && python ../documents/read_invoices.py /path/to/invoices --out out.xlsx`
   Add `--model gemma4:12b` to compare models on the same files.

What to check is under "Invoices tab" below, in "Not done".

## Running the `6-documents` branch on the server

This branch adds the Word documents page. On a machine that already runs the
app, these are the only steps:

1. **Get the branch.** `git fetch && git checkout 6-documents`
2. **Install the new packages** (the project's own `documents/` package and
   `python-docx`, which brings `lxml`):
   `cd backend && uv sync --extra router --inexact`
   Without uv: `pip install -r requirements.txt` from the repo root.
3. **Pull the documents model:** `ollama pull qwen3:14b` (about 9 GB).
4. **If this machine has `ollama/config.local/models.yaml`, update it.** That
   file replaces `ollama/config/models.yaml` completely, so the new parts must
   be copied into it or the page starts with the wrong model:
   - the whole `documents:` block (with `model: documents`, `allow_cloud: false`);
   - the model entry `- id: documents` (`qwen3:14b`) under `models:`.
   No `config.local`? Nothing to do: `ollama/config/models.yaml` already has both.
5. **Check, then restart.** `cd backend && uv run --inexact python -m ollama_pipeline`
   must list `documents  qwen3:14b  ready`. Then stop the app and start it
   again (`./run.sh`, or `run.ps1` on Windows).
6. **Open the page:** <http://127.0.0.1:8000/documents.html>, or the **Docs**
   link on the main page. The model picker on it should show "Qwen 3 14B".

Memory: `qwen3:14b` takes about 9 GB before any context, so it will not sit on
a 16 GB GPU next to both chat models. Ollama swaps models in and out as needed
(slower first reply after a swap). Check `ollama ps` while testing.

Uploaded and generated files go to `backend/data/documents/` (deleted after a
day); saved templates go to `backend/data/templates/` (kept). Neither is in git.

What to test on this branch is listed under "Word documents page" below,
in "Not done, check these on the server".

## Chat attachments

`POST /api/files` (`backend/app/routes.py`) keeps an attached file on the server and answers with an
id (see "Big files" below; the text no longer comes back to the browser). It reads the text with `read_source` (`documents/document_engine/word/sources.py`, shared with the Word tab):
PDF and Word as before; **Excel `.xlsx`/`.xlsm`** as one Markdown table per sheet (values
Excel last calculated, first 2000 rows a sheet, dates without a time shown as dates);
**CSV/TSV** decoded as UTF-8 (with or without BOM), UTF-16 or Windows-1252; other text as
is. Old binary `.xls` and `.doc` are refused with a message to save them as `.xlsx` /
`.docx` (reading `.xls` would need the `xlrd` package). Limit `workspaces.max_upload_mb` (50 MB), 5,000,000 characters of text (`MAX_CHARS` in `backend/app/workspaces/routes.py`).
**Scanned PDFs** (no text layer) are no longer refused: `/api/files` answers with
`images` (the first `MAX_SCAN_PAGES` = 8 pages as base64 JPEG, longest side `SCAN_SIDE`
= 1400, made by the Invoices reader) and a `note`; the page attaches them as pictures, so
Auto sends them to the vision model (gemma). The Word tab still refuses scans (it needs
text). Test: `backend/tests/scan_route.py`. Reading quality on real scans is not judged yet.
Checked with the real model: it answered from an attached `.xlsx` and `.csv`.

## Users and login

Every page and every `/api` route needs a signed-in user, except `/api/health`,
`/api/auth/*`, `login.html` and `auth.js`. Code: `backend/app/auth/` (standard
library only: SQLite file `backend/data/auth.db`, scrypt passwords, signed
tokens, cookie `token`). Check without a server: `cd backend && python -m app.auth`.

- **First admin:** `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `backend/.env`; made at
  startup only while there are no users (log: `Auth: first admin ... created`).
  `AUTH_SECRET` signs the tokens; without it a random one is kept in
  `backend/data/auth_secret`. `TOKEN_HOURS` (default 168) is how long a login lasts.
- **Sign out ends the session on the server** (`POST /api/auth/signout` changes the user's
  `session_stamp`), so a copied token stops working. It also ends that user's sessions in other browsers.
- **More users:** admins only, through the admin page `/admin.html` (or `POST /api/auth/users`). There is no public signup. Roles: `admin`, `user`.
- **Pages:** `frontend/auth.js` is the first script in each page's `<head>`; it
  hides the page until `/api/auth/me` answers and sends everyone else to
  `login.html`. After it, `await authReady` gives the user and `signOut()` signs out.
  It also adds the name, Sign out and (admins) Admin links to the page header.
- **New API routes** are protected by adding them to one of the routers in
  `main.py` that already carry `dependencies=login`; admin-only routes use
  `Depends(admin_user)`.
- **Sidebar** (`frontend/sidebar.js`, added to every page by `auth.js`, modelled on
  Open WebUI's): New Chat, search, links to Documents / Word / Invoices, the saved
  chats grouped by date (click to go back to one; a ⋯ menu renames or deletes), and
  the user menu (Admin Panel for admins, Sign out). On wide screens it pushes the page
  right by 260 px (`html.sb-on`); on phones it is a drawer that starts closed.
- **Saved chats:** `backend/app/chats.py`, table `chat` in the same `auth.db`, private
  to each user (another user's id answers 404), deleted with the user. `/api/chats`
  (list with `?q=` search, create, read, save, `/{id}/delete`). `index.html` saves
  after every finished reply and opens a chat from `?c=<id>` (`window.Chat.open/reset`).
  Pictures are not kept in a saved chat (file text and names are).
- **Admin panel** (`/admin.html`): Open WebUI style Users tab with count, search,
  sortable table, Add User (form or CSV: Name,Email,Password,Role) and Edit modals.
  Tabs Users, Models (next section) and Code. Non-admins are redirected to the chat.
- **Change password:** `POST /api/auth/password {current, new}` (any signed-in user;
  8+ characters, must differ from the current one) and the "Change password" dialog in
  the sidebar user menu. It ends the user's other sessions and re-issues this browser's
  cookie. Not built: a forced change at first login, "forgot password" by email (an
  admin can set a new one in the Admin Panel). Test: `backend/tests/browser_password.py`.
- The first account made cannot be deleted, demoted or disabled. Full design and
  the Open WebUI analysis it comes from: `anaylze.md`.

## Admin Panel -> Models

`/admin-models.html`, admins only, modelled on Open WebUI's Admin Settings -> Models
and its "Advanced Params". A list with search and an on/off switch per model; a click
opens the model's editor. Code: `backend/app/model_settings.py` (`/api/admin/models`),
`backend/app/model_server.py` (`/api/admin/llama-server`),
`llamacpp/llamacpp_pipeline/server_config.py` (reads and writes `models.ini`, restarts
the container).

- **On/off, for everyone.** A model that is off is not in anyone's chat picker (admins'
  too), a chat that names it gets 403 "This model is turned off by an admin.", and Auto
  sends its messages to a model that is on; with every model off the chat says so.
  Stored as `enabled` in the model's row of table `model_setting` in `auth.db`
  (missing = on). Chat only, like the settings below: the Documents, Word and Invoices
  tabs keep using the model their `documents:` / `invoices:` block names, with their own
  prompts.
- **System prompt and Advanced Params,** each Default (the `models.yaml` value, shown in
  grey) or Custom: the sampling options llama-server takes (temperature, top_p/k, min_p,
  repeat penalty and window, presence/frequency penalty, mirostat, typical_p, max tokens
  `num_predict`, seed, stop) and `think`. Ranges: `NUMBERS` in `model_settings.py`. Stored
  as JSON in `model_setting`, laid over `models.yaml` on every reply: no restart.
- **Server (llama-server only): Context Length and Parallel slots** of each model, which
  are `ctx-size` and `parallel` in `llamacpp/server/models.ini`. The app's `num_ctx` (the
  "too long" warning, the documents' share of the room) is read from `ctx-size`; it is no
  longer kept by hand in `llamacpp/config/models.yaml`. One request can use the whole
  ctx-size, but all slots of a model share it (kv-unified).
- **"Save & restart llama-server"** writes `models.ini` (the old one stays as
  `models.ini.bak`), runs `llamacpp/server/start.sh`, and waits until every model is
  loaded (up to 4 minutes). Replies in progress are cut off. If a model does not load,
  the old `models.ini` is put back, the container is started again, and the panel shows
  the error with the last lines of `docker logs`. One restart at a time (409).
- **Memory: both models must fit on the 16 GB card together.** Measured: llama3.1:8b
  5.9 GB at ctx 16384, 6.5 GB at 24576, 7.1 GB at 32768; gemma4:12b with vision 8.7 GB
  at 16384. Today's values (llama 24576 x 8 slots, gemma 16384 x 4) use 15.4 of 16 GB,
  so there is under 1 GB left: about 0.6 GB per 8k more ctx for llama3.1:8b (q8_0
  cache). After a change check `nvidia-smi`; a model that no longer fits fails to load
  and the change is rolled back.
- On Ollama (`PIPELINE` not `llamacpp`) the Server part is hidden; Ollama's context is
  `num_ctx` in `ollama/config/models.yaml`.
- Checks without a server or a model: `backend/tests/admin_models.py`,
  `backend/tests/llama_server_config.py` (run from `backend/`).

## Admin Panel -> Tools

`/admin-tools.html`, admins only, a copy of Open WebUI's Workspace -> Tools. An admin writes a
Python `class Tools:`; each public method becomes a function the chat model may call (native
function calling). Code: `backend/app/tools.py` (loader, specs, routes), the loop in
`chat_stream` (`ollama/ollama_pipeline/pipeline.py`), `tools=` in both clients.

**Tool code runs inside the backend process, with everything the server can do** (files,
`backend/.env`, `auth.db`, the network, the shell). Give the admin role only to people you trust
with the server. That is why:
- create, update, valves and the code itself are behind `admin_user` (the whole `/api/admin/tools`
  router); nothing else reaches `exec`. Users only get `GET /api/tools`: id, name, description of
  the tools that are on, never the code or the valves.
- a tool id must be an identifier (stored lower-case); the chat runs only ids that exist and are
  on, whatever the browser sends.
- tool names, arguments and results are shown with `textContent` only (`toolBlock` in `index.html`).
- there is no `requirements:` pip install from the tool's frontmatter, on purpose.

How it works:
- **Specs** come from type hints and `:param x:` docstring lines, as Open WebUI does. Parameters
  starting with `__` are not shown to the model; `__user__` (id, email, name, role of the person
  chatting) and `__id__` are passed only to methods that take them. The model cannot pass them itself.
- **Valves:** a `class Valves(BaseModel)` inside `Tools` is the tool's settings (API keys, limits),
  a form in the editor, stored as JSON in `tool.valves` (plain text in `auth.db`). Set on the
  instance before every chat.
- **On/off** per tool, for everyone. **Default tools per model:** Admin Panel -> Models, saved as
  `tool_ids` in `model_setting`; the chat's wrench menu starts with those ticked. The user can
  change the ticks per chat; `tool_ids` goes with `/api/chat` (at most 20).
- **The loop:** the model gets the ticked tools' specs; when it answers with `tool_calls` each one
  runs (sync methods in a thread, async awaited, `TIMEOUT` 30 s), the result (cut to 4000
  characters) goes back as a `tool` message, and the model is asked again. After
  `MAX_TOOL_ROUNDS` (5) it answers without tools. A tool that raises, hangs, is unknown or gets bad
  arguments gives `Error: ...` to the model; the chat goes on. Events `tool_call` and `tool_result`
  show as a folded "Used <name>" block above the reply.
- OpenCode models get no tools (a notice says so). The model must support tools: llama-server
  needs its Jinja chat templates (default in current builds; `start.sh` passes no `--jinja` or
  `--no-jinja`); on Ollama a model without the `tools` capability answers with an error when a
  tool is ticked. **Not yet tried with a real model** on either server: only against stand-ins.

Things to know:
- **Prompt injection.** The model decides what to call, and an attached file or a pasted text can
  tell it to. A tool runs for whoever is chatting, with the server's rights: write tools that only
  read, or that check `__user__["role"]`, and never one that runs shell commands, SQL or `eval` on
  its arguments. Open WebUI's own template has a calculator with `eval(equation)`: that is code
  execution on the server for every chat user, so this app's template does not (checked by
  `tests/tools.py`, which loads the template from `admin-tools.html`).
- The `Error: <exception text>` goes to the model and is shown to the user. An exception that
  contains a valve (a URL with `?key=`) shows it: catch such errors in the tool.
- The number of calls in one round is not capped (5 rounds are).
- Sync tools run in a pool of their own (`_pool`, 8 threads in `tools.py`), so a hung tool cannot
  stall Von routing or invoice reading. Python cannot kill a thread: 8 sync calls that never return
  fill that pool, and every later sync tool call times out until the app is restarted.
- Tool code is run again after a restart, at the first chat that uses it, and on every save.
- Test without a server or a model: `cd backend && .venv/bin/python tests/tools.py` (routes, who
  may reach them, specs, valves, the loop with a stand-in model, both clients against a fake server).
- **The two bundled tools** in `tools/` are Open WebUI tools adapted to this app: `generate_spreadsheets.py`
  (id `generate_spreadsheets`, Excel) and `generate_slides.py` (id `generate_slides`, PowerPoint). To install
  one: Admin Panel -> Tools -> New tool, type the id, paste the whole file, save. Both need `openpyxl` and
  `python-pptx`, listed in `requirements.txt` (`cd backend && uv sync --inexact` if a package is missing).
  Their files go to `backend/data/tool_files/` and are linked as `/api/tools/files/<name>` (signed-in users
  only, never deleted). Slides valves: `unsplash_access_key` (optional; no key, no stock photos), and
  `template_mode_enabled`, `presentation_edit_enabled`, `inspect_slides_enabled`, all off by default: chat
  attachments are not stored here, so template mode and inspect are not available. Offline test for the
  slides tool: `cd backend && .venv/bin/python tests/slides_tool.py`; the spreadsheet tool has no test file yet.

## Workspaces (Knowledge)

`/workspaces.html`, a copy of Open WebUI's Knowledge. A workspace is a set of documents added once, split into
passages and indexed on this machine; picked in the chat's model menu, every question in it is answered from the
passages that match, with a "Sources" block under the reply. Code: `workspaces/workspace_engine/` (`index.py`,
`service.py`, `chunking.py`, `embedding.py`, `prompts.py`), routes `backend/app/workspaces/routes.py`
(`/api/workspaces`), data in `backend/data/workspaces/` (`WORKSPACES_DIR`), one folder and one SQLite
`index.db` for each workspace.

- **Who:** every signed-in user lists, opens, searches and chats with a workspace; only admins create, rename,
  add or remove documents, reindex or delete (`Depends(admin_user)`). Creating the same name twice at once is
  serialised by a lock.
- **Search:** words (SQLite FTS5) always; meaning too while the embedding model answers. On llama-server that is
  EmbeddingGemma-300M (Q8_0 GGUF in `/home/ahana/models/embed/`) in a container of its own, **on the CPU**
  (the card is full): `llamacpp/server/start-embed.sh` starts `llama-embed-webui` on `127.0.0.1:8102` (same
  `LLAMACPP_API_KEY`, `--restart unless-stopped`). Config: `embed: url:` and `workspaces.embed_host: embed`,
  `embed_model: embeddinggemma`, with EmbeddingGemma's query/document prefixes, in `llamacpp/config/models.yaml`.
  Measured 22 October: 368 MiB RAM, 11-17 ms a question, 0.9 s for 8 passages of 1200 characters. Stopped, search is by words.
  After a change of embedding model, Re-index the workspace. "how much time off do I get?" finds the leave policy
  (which says "earned leave") first by meaning; by words alone it came third.
- **Reading a document:** each document's text by section (a PDF page by page) and its original file are kept in
  `index.db` (tables `sections`, `originals`) and go with it. `GET /api/workspaces/{id}/documents/{doc}` gives
  `{name, kind, sections: [{label, text}]}`; `.../{doc}/file` the original (PDF shown inline, anything else
  downloaded, `nosniff`). Any signed-in user. A document added before 22 October has no original (404: add it
  again) and its text is put back together from its passages. The chat's Sources link to it
  (`workspace_id` and each item's `doc` in the `sources` event).
- Documents: PDF (kept by page, so a source says "page 87"), Word, Excel, CSV, text. Pictures are refused.
  `MAX_CHARS` 5,000,000 a file, `workspaces.max_upload_mb` (50): the upload is read up to the limit plus one byte,
  then refused with 413.
- Only the passages that fit go to the model (`ollama_pipeline/context.py`, see "Big files").
- Test without a model: `cd backend && .venv/bin/python -m workspace_engine`.

## Databases

`/databases.html`, the SQL connector: a user picks a saved database in the chat's model menu and asks in plain
words; the model looks at the tables and runs read-only queries (tools `list_tables`, `get_table_schema` and the query tool in
`sql/sql_connector/tools.py`); the answer follows, with each query shown in a folded block above it. PostgreSQL,
MySQL/MariaDB and SQLite. Code: `sql/sql_connector/`, routes `backend/app/sql/routes.py` (`/api/sql`),
connections in `backend/data/sql/` (`SQL_DIR`). `sql:` block in `models.yaml`: `model` (the one that calls tools:
`gemma` on llama-server), `max_rows`, `timeout`, `max_rounds`.

- **Who:** every signed-in user lists the names and uses a database in the chat. Only admins save, test, change,
  delete or list the tables of a connection: it holds a password.
- **Read only, four locks** (`safety.py`, `connectors.py`): one statement, starting with SELECT/WITH/SHOW/
  DESCRIBE/EXPLAIN/VALUES/TABLE, no write word anywhere outside strings; every driver runs one statement a call;
  SQLite is opened read-only; PostgreSQL and MySQL run each query in a read-only transaction. **The last lock to
  rely on is a database user that can only read**: make one on the database server and save that user. The
  text check is known to be bypassable (PostgreSQL `E'\''` strings); the read-only transaction still stops writes.
- **Saved passwords:** shown to nobody. A change with an empty password keeps the saved one only for the same
  engine, host, port and user (`same_server`); otherwise the empty password is used and the test fails.
- **SQLite files inside `backend/data/` are refused** (users, password hashes, saved connections live there).
  Any other file the server can read can be added by an admin: give the admin role only to people you trust.
- A spreadsheet attached in the chat becomes a SQLite table of its own, queried the same way (see "Big files").
- The model must call tools: llama-server needs its Jinja chat templates (the default; do not pass `--no-jinja`),
  and the model entry is not `tools: false`. **Llama 3.1 8B cannot do it** (live test, 22 October: llama-server's
  "peg-native format" error on a database question; it sends a chart's `series` as a string), so it is `tools: false`
  and `sql.model` is `gemma`: a chat with a database, a chart or a memory is answered by gemma4, also when llama is
  picked (a note says so). With none able to call tools, the chat says so and names llama-server.
- Test without a server: `cd backend && .venv/bin/python -m sql_connector`.

## Features & Memory

Three skills for the chat's local model, with switches in Admin Panel -> Features (`/admin-features.html`):
**Database queries**, **Charts**, **Memory** and, under Memory, **Save and forget from the chat**. All on by
default. Code: `skills/agent_skills/`, routes `backend/app/skills.py`: `GET /api/skills` (any signed-in user: what
is on, the chat hides what is off), `GET/POST /api/admin/features` (admins), `/api/memories` (the user's own).
Switches are kept in `backend/data/skills/skills.json`.

- **Charts:** offered when a database or a spreadsheet is in the chat, or the message asks for a chart or graph (not for a workspace alone). The model calls `create_chart` with kind (bar, line, area, pie) and numbers; the numbers are checked
  (`charts.py`) and a mistake goes back to the model to correct. The reply carries a `chart` in its `tool_result`
  event and the chat page draws it. The model never sends code or HTML to the page.
- **Memory:** short facts each person wants kept, given to the model in every chat of theirs. **Private to each
  user** (scope = user id; another user's id answers 404), deleted with the user. Add, edit and delete in the
  sidebar menu -> Memory. When an admin allows it, "remember that ..." and "forget ..." in the chat save or remove
  one; "forget" asks which one when two match equally. Limits: 200 per user, 500 characters each. A memory is text
  given to the model: do not store secrets in it.
- The tools model status in the admin page is `sql.model` from `models.yaml`; skills need a model that calls tools.
- Test without a model: `cd backend && .venv/bin/python -m agent_skills`.

## Big files (chat attachments, context)

**Why:** one request to llama-server holds only a slot of its context (`ctx-size` / `parallel`, shared with the
other slots because of kv-unified). A 100-page PDF is about 60,000 tokens. Before, the whole text went into the
chat message and came back with every later message: refused, or the model saw part of the file.

- `POST /api/files` keeps the file under a random id in `backend/data/chatfiles/` (`CHATFILES_DIR`) for
  `documents.keep_hours` and answers `{id, name, kind, chars, tokens, pages, truncated, sheets, rows, query}` with
  no text. `/api/chat` takes `files: [{name, id}]` (the old `{name, content}` still works). Code:
  `workspaces/workspace_engine/chatfiles.py`. Limit `workspaces.max_upload_mb` (50 MB) a file; the upload is read to the limit plus
  one byte before the check, never in full. A scanned PDF still answers with `images` and a `note`.
- **For each question:** files that fit are given whole; otherwise the passages that match, as many as fit, with a
  "Sources" block under the reply. A short follow-up ("and how often?") is searched together with the question
  before it. If nothing matches, the start of the file goes to the model but is not listed as a source.
- **"Summarise ..." of a file too long to read at once:** `read_whole` reads it in parts that fit, notes each (the
  progress shows as "Reading ... parts read"), and the answer is written from the notes; the notes are kept.
  At most 40 parts (about 250 pages).
- **Spreadsheets** (`.xlsx`, `.csv`) are loaded into a SQLite table and the model queries them with the Databases
  tools instead of reading rows ("total sales by region as a chart" gives the SQL, the totals and a chart).
- **Fitting:** `ollama/ollama_pipeline/context.py` fits every request to the model's real context before it is
  sent: oldest turns go first, then the largest instruction or tool result is cut in the middle. Tokens are
  estimated, leaning high; when the server still refuses ("exceeds the available context size", `ContextOverflow`)
  `chat_stream` retries once with a budget scaled by the numbers in the error. The context comes from `models.ini`
  on llama-server and `num_ctx` on Ollama.
- Test without a model: `cd backend && .venv/bin/python -m ollama_pipeline.selfcheck`.

### Running these features (test instance)

To try them without touching the running app, copy the data and use another port. `app/settings.py` reads
`PORT`, `HOST`, `AUTH_DB`, `DOCUMENTS_DIR`, `SQL_DIR`, `WORKSPACES_DIR`, `CHATFILES_DIR`, `SKILLS_DIR`. Templates and
tool files still go to `backend/data/`. The Von router needs Python 3.12 (`uv sync --extra router`); on 3.11 the
app runs and routes by keyword rules.

## Access requests (n8n)

People ask for access on the request page (`access-page` container, nginx, port
8080; its source is `new.html`), n8n (`n8n` container, port 5678) asks the approver,
and on approval the account is created **in this app's database** as a normal `user`.
Workflow file: `n8n/fastshot-access-approval.json`. App side: `backend/app/access.py`,
`POST /api/access/users` (header `X-Access-Key` = `ACCESS_API_KEY` in `backend/.env`;
without the key set the route answers 503). It always makes a `user` (a `role` in the
body is ignored), stores only the password hash, answers 409 for an existing email.

How it runs: request page -> `access-request` webhook -> approver email (signed link,
valid 7 days) -> approver opens it and clicks **confirm** (a second step, so mail
scanners that open links cannot approve) -> password generated in the *Generate
password* node (rules at its top) -> `POST /api/access/users` -> credentials emailed to
the person. Nothing is written to Excel any more.

Set up (once): in n8n import the file, open the **Settings** node and set `secret`
(any long random text), `accessKey` (`grep ACCESS_API_KEY backend/.env`) and check
`appUrl` (`http://192.168.250.3:8000`, reachable from the n8n container), `n8nUrl`,
`approverEmail`, `fromEmail`; keep the SMTP credential; **deactivate the old "Create
account and email credentials" workflow first** (same webhook paths), then activate
this one. The request page needs no change.

Things to know:
- The old workflow's `users.xlsx` (plaintext passwords) is untouched. Its
  `download-users` webhook is protected only by `key=CHANGE_ME_DOWNLOAD_KEY` unless
  that was changed: while the old workflow is active anyone on the network can
  download that file. Existing rows can be loaded into the app with the admin
  panel's CSV import (columns Name, Email, Password, Role).
- If the email fails after the account was made, the approver sees a page saying so;
  set a new password for that person in the Admin Panel.
- The person has no way to change the generated password yet (there is no
  self-service password change; an admin can set one).

## Start here on a new machine

Five steps. "Setup" further down has the details and the reasons.

1. **Python packages.** `cd backend && uv sync --extra router` (needs
   [uv](https://docs.astral.sh/uv/) and Python 3.12+). Without uv:
   `pip install -r requirements.txt` from the repo root.
2. **Ollama** running, with the models this machine will use pulled
   (`ollama list` shows them). The Word documents page needs `qwen3:14b`.
3. **OpenCode 1.18.34, exactly that version:**
   `npm install -g opencode-ai@1.18.34`, then `opencode --version` must print
   `1.18.34`. Not the package `@opencode/cli`: that is 2.x and does not work.
4. **Models: one file, `ollama/config/models.yaml`.** The header at its top
   names the three entries to change. Nothing else in the project names a
   model.
5. **Check, then run.** `cd backend && uv run --inexact python -m ollama_pipeline`
   lists every model as ready or not. Then `./run.sh` (Windows:
   `powershell -ExecutionPolicy Bypass -File run.ps1`) and open
   <http://127.0.0.1:8000>. The server log should show `Router: Von is ready`
   and `OpenCode: server ready at …`.

**On llama-server instead of Ollama** (Linux with Docker and an NVIDIA GPU;
steps 3 and 4 are then not needed, Ollama only has to hold the model files):
1. Put `LLAMACPP_API_KEY=<some long random text>` and `PIPELINE=llamacpp` in
   `backend/.env`.
2. Start the container: `llamacpp/server/start.sh`. It reads Ollama's model
   files from `/home/ollama/models/blobs` (Ollama's `OLLAMA_MODELS` since 15
   October 2026; `OLLAMA_BLOBS=<folder>` overrides); the paths are in
   `llamacpp/server/models.ini`. **A model removed from Ollama (`ollama rm`, or
   its files moved) breaks llama-server at its next restart**, not before: a
   running container keeps the open files. On 16 October gemma4:12b had to be
   pulled again for that reason.
3. `cd backend && python -m llamacpp_pipeline` must list both models as ready.
4. `./run.sh`. The server log shows `Pipeline: llama-server at http://127.0.0.1:8101`.
   Make sure no model is loaded in Ollama at the same time (`ollama ps`).

## Latest changes — 4 October 2026, on the Windows test laptop

What the last working session added, for whoever picks this up next:

- **Routing.** Questions about software now go to OpenCode: everyday coding
  to a new `code` route, the hardest to `expert`. Everything else stays on the
  local models. Software is recognised by keywords and, failing that, by two
  questions to Von. See "OpenCode routes" below.
- **Routing decisions are logged**, one line each (`Router: …`).
- **Eight free OpenCode models**, several per route, with the next one taking
  over when one fails. What each provider does with prompts is written above
  them in `models.yaml`.
- **OpenCode is pinned and kept apart.** The app starts it with updates off
  and gives it folders of its own (`opencode.home`), so another OpenCode on
  the machine cannot break it.
- **Scrolling.** While a reply is written the chat follows it only if the
  reader is at the bottom; scrolling up is no longer pulled back
  (`follower()` in `frontend/index.html`).
- **`requirements.txt`** for pip, generated from `backend/uv.lock`.
- **`models.yaml`** has a header saying what to edit, with the local models
  first.
- That laptop's own config (`ollama/config.local/`, not in git) uses
  `qwen3-gpu:latest` as the reasoning model and `num_gpu: 99` on the light
  one; see the next section for why.
- Every check in this file was run again there and passes. Still not tested:
  Linux, a 16 GB GPU, `qwen3:14b`, two local models loaded at once.

## Current status — read before trusting anything

- Developed on a laptop without Ollama, then run against real Ollama 0.32.5 on
  a second laptop: Windows 11, GTX 1650 (4 GB VRAM), 16 GB RAM, with
  `llama3.2:3b`, `gemma3:4b`, and `qwen3:4b` standing in for `qwen3:14b`.
  All eleven checks in "What to verify" pass there, with Von 1.3.7 loaded
  (10 by pointing the backend at a port with no Ollama, not by stopping it).
- **Not run anywhere yet:** `qwen3:14b`, a 16 GB GPU, and two routed models
  loaded at once. On 4 GB Ollama swaps models on every route change, so
  nothing measured there says how fast the server will be.
- Left to itself, Ollama puts no routed model fully on a 4 GB GPU at `num_ctx`
  8192: `qwen3:4b` ran 45% on the CPU at 17 tokens/s. Forcing every layer onto
  the GPU fits: `num_gpu: 99` in an entry's `options`, or in the model itself
  as `qwen3-gpu` on that laptop does. Measured: 45 tokens/s for `qwen3:4b`,
  56 for `llama3.2:3b` (37 before). Worth trying on any GPU that is short of
  memory; a model that does not fit this way fails to load.
- So on the server the open questions are about hardware: do the models fit,
  do both routed models stay loaded, and how long do replies take. Expect
  small integration bugs anyway. Fix them, and report what you changed.

## OpenCode routes (cloud) — added after the Ollama test run

With Auto, questions about software go to OpenCode's free cloud models instead
of Ollama: everyday coding to the `code` route, and the hardest (architecture
and system design, hard-to-find bugs such as deadlocks, races and memory
leaks, large refactors) to the `expert` route. Mail, bills, documents and
other reasoning stay on the local models. Code:
`ollama/ollama_pipeline/opencode.py`; config: `opencode:`, the `opencode*`
models and the `code` and `expert` routes in `models.yaml`.

- Tested with OpenCode 1.18.34 and the real free model `opencode/big-pickle`
  on macOS and on Windows 11; checks (a) to (g) below pass on both. **Not
  tested on Linux.** On Windows the server starts from Python and no
  `opencode serve` is left after a clean stop, a crash, or a killed process
  tree (the last one leaves the empty temp folder behind). Not tried: killing
  only the backend process.
- **Use OpenCode 1.18.34; 2.x does not work.** `@opencode/cli` 2.0.22 is a
  different npm package from the same authors. Its `serve` only has
  `/api/...` routes (`POST /session` answers 405), so every cloud model shows
  as unavailable and Auto quietly answers locally. Install the tested version
  with `npm install -g opencode-ai@1.18.34`.
- **If `opencode` on this machine is already a 2.x**, leave it and install the
  old one into a folder: `npm install --prefix <folder> opencode-ai@1.18.34`,
  then set `opencode.command` in `models.yaml` to
  `<folder>/node_modules/.bin/opencode`.
- **1.x and 2.x cannot share OpenCode's default data folder.** Once 2.x has
  run, 1.x stops with "Database is not empty and has no session table". The
  app therefore gives its OpenCode folders of its own: `opencode.home` in
  `models.yaml`, by default `ollama/.opencode/` (not in git, safe to delete).
  It also starts it with `OPENCODE_DISABLE_AUTOUPDATE`, so the installed
  version is the one that runs.
- How a message gets to the cloud (`router.py`), in this order:
  1. Clear signs of software (`_SOFTWARE`: language names, code snippets,
     pasted commands, file names like `app.py`, "write a code for", "fix this
     bug", "git commit") send it there without asking Von.
  2. Otherwise Von is asked whether the request is about software
     (`_von_says_software`): two questions, and it must pass both. This is
     what catches plain wording such as "make a landing page for my coffee
     shop" or "can you build me a snake game".
  3. `_EXPERT` then picks `expert` over `code`.
  Everything else stays local, where Von chooses between `simple` and
  `complex` as before. The server log has one line per decision, e.g.
  `Router: code -> opencode-space-bunny (von, 0.902)`: read it first when a
  message went somewhere unexpected.
- Why two questions and not a third choice for Von. With `code` or `expert`
  added to its choices Von was unsure on more than half of the sample
  requests, so keyword rules decided and coding requests in plain words went
  to the local model. One yes/no question alone was either too eager (it
  called "hey" software) or too cautious. Passing both, on 35 samples: 15 of
  16 software requests caught, 2 of 19 others let through (both asked for a
  "script", for a film and for a school event). Each question costs Von about
  0.4 s on a laptop CPU, so routing takes up to about a second.
- `_SOFTWARE` is narrow on purpose: an everyday message it matches leaves the
  machine without Von being asked, so words that are also everyday words
  ("class", "error", "script", "java") are not in it.
- **Software is checked before stickiness.** A code question goes to the cloud
  even when a local model answered the message before it. A message with no
  software word of its own ("add error handling to it") also goes there when
  the chat is already about software: the request before it was a software
  one, the last reply contains a code block, or a cloud model answered last
  (`_cloud_route`, `_in_software_thread`; the reply header says "Routed by
  context"). Requests for an email, translation or summary (`_EVERYDAY`) do
  not follow the thread and go back to a local model. The "stay on the model
  already answering" rule now applies to local models only.
- When a software question stays local because the chat has attachments, the
  reply carries a note saying so. Without it this looks like a routing bug.
- Checks: after a local reply ask "now write it in html" and "can you code
  this" → OpenCode; after a reply with code ask "add error handling to it" →
  OpenCode ("context"); after an OpenCode reply ask "write an email to my boss
  about it" → local light model; attach a PDF, then ask for code → local, with
  the note.
- Each cloud route lists several models (`model: [a, b, c]`). The first one
  OpenCode offers answers. If it fails, the next answers and the reply says
  so; after the last, the local heavy model. With another model lined up the
  first failure is enough and OpenCode's own retries are not waited for: a
  broken first model cost under 3 s in testing, against 75 s.
- Models, tested 2026-10-04 with a coding task and a bug hunt: eight of the
  nine free models that are not previews answered both correctly.
  `ling-3.0-flash-fin-free` answered "Endpoint is unavailable" and is left out,
  like `longcat-2.5-preview-free`. The free list changes; `opencode models`
  shows the current one, and an id that is gone shows as unavailable.
- **What the providers do with prompts differs** (https://opencode.ai/docs/zen
  on that date). Space Bunny keeps nothing, which is why it is first in the
  `code` route. Big Pickle, MiMo, Ling and Fledge "may be used to improve the
  model". Nemotron is logged. Muse Spark trains future Meta models, so it is
  in the picker but in no route. Check these before changing a route's order.
- The backend starts its own `opencode serve` (127.0.0.1, random port, random
  password, empty temp folder) at startup and talks to it the way OpenCode's
  own `opencode run` does: create a session, send the prompt, read the event
  stream. The log prints `OpenCode: server ready at …` when it is up.
- **Replies stream live.** Text comes from `message.part.delta` events and
  reaches the browser as it is written; first words usually within 5-10 s.
  On an open question `big-pickle` can reason for over a minute first (87 s
  for check (a) on one run); the Thinking block shows that as it happens.
  Each reply is a throwaway session that is aborted on Stop and then deleted.
- **Do not customise the agent, its prompt, or which tools exist.** OpenCode's
  service answers "free tier can only be used from within OpenCode" (403) for
  anything but a built-in agent. Do not work around that by faking a client.
- Shell, edit, web and outside-folder access are set to permission "ask", and
  the client answers every `permission.asked` event with "reject" (plus a
  message so the model carries on in text). OpenCode therefore cannot run
  commands, change files, fetch URLs or read outside its empty folder. A
  request that is never answered stays pending; nothing is approved by
  default. Keep it so, and never reply "once" or "always". Every tool attempt
  is logged (`OpenCode: rejected its request to use 'bash'`).
- Privacy rules, keep them: attached files and images are never sent; in Auto
  a conversation that has had any attachment stays on local models.
- If OpenCode is missing, refuses, errors or times out, the local heavy model
  answers and the reply says so.
- If `ollama/config.local/models.yaml` exists on this machine it overrides
  `ollama/config/` — copy the new `opencode:` block, the `opencode` model and
  the `expert` route into it, or the route will not exist here.

Checks: (a) "design the architecture for a chat service" → header shows
"Auto · OpenCode Big Pickle" and the text grows as it is written, with an
orange caret at the end until it finishes; (b) follow with "make it shorter" →
stays; (c) same question with a PDF attached → local reasoning model;
(d) rename the command in config to something that does not exist → local
model answers; (e) press Stop mid-reply → text stops at once; (f) pick
OpenCode in the picker and ask it to "run `uname -s` with your shell tool" →
the server log shows the rejection and the reply contains no real output of a
command; (g) stop the backend → `opencode serve` is no longer running;
(h) "write a python function for binary search and explain its complexity" →
"Auto · OpenCode Space Bunny", and so does "make a landing page for my coffee
shop" (tooltip: "Routed by von"); (i) follow with a longer request for an email →
a local model answers; (j) put a model that fails first in a route
(`opencode/ling-3.0-flash-fin-free` did on the test day) → the next model
answers within seconds and the reply says which one could not. An id
OpenCode does not offer at all is skipped without a note.

## Live generation in the UI

Nothing should "pop out in one go". What the user sees while a reply is made:

- **Answer text** grows as it arrives, for Ollama and OpenCode alike, with an
  orange caret at the end until the reply is complete.
- **Thinking** (models that reason first, e.g. `qwen3`): the reasoning text
  streams into a "Thinking…" block above the answer. When the answer starts
  the block folds to "Thought for Ns"; clicking it opens it again.
- Before anything has arrived, three dots.

Where it lives: the backend sends `thinking` and `delta` events
(`pipeline.py`, `opencode.py`); `send()` in `frontend/index.html` paints them
once per animation frame. A browser tab in the background only repaints about
once a second, so judge smoothness with the tab visible.

Check on this machine: ask `qwen3` a coding question and watch the Thinking
block fill, then fold; the answer must appear word by word, not as one block.
If a reply does arrive in one piece, look at what sits between browser and
backend (a proxy that buffers) before changing the code.

## Word documents page (work in progress)

`frontend/documents.html`, reached from the **Docs** link on the main page.
Tabs: fill a template, change a document, write a new one, the template
library (result: a downloadable `.docx`), Word document (several sources into
one designed document, as Word or PDF, see "Word document tab" below), and
Invoices (result: a sheet, see "Invoices tab" below).

- **The model never writes the Word file.** It answers in JSON (which blank
  gets which value, or which edit operations to run) and the app's own code
  applies that to a copy of the uploaded file, so the rest of the file is
  untouched. The feature has folders of its own, see "Where the documents
  code is" just below.
- **Local models only.** `documents.allow_cloud` in `models.yaml` is false and
  must stay false on the server; it exists only to try the page on a machine
  without Ollama.
- The user reviews every suggested value before the file is made. Values are
  tagged by where they came from (their files, their instructions, or written
  by the model).
- Files are kept in `backend/data/documents/` under random ids and deleted
  after `documents.keep_hours`.
- Needs `python-docx` (in `requirements.txt` and the uv lock). Accepts `.docx`
  and `.dotx`; refuses old `.doc`, macro files and password-protected files.

### Where the documents code is

Separate from the chat code. Nothing for this page lives in `ollama/` or in
`backend/app/routes.py`.

```
documents/                         the feature itself (package `document_engine`)
  document_engine/
    engine/                        works on Word files. No model.
      wordfile.py                    open, number paragraphs and text boxes, find blanks, fill
      wordedit.py                    the fixed list of edit operations; Markdown -> Word
      slotmap.py                     labels of a designed template -> its slot map
      render.py                      fill a designed template, copy/remove repeated blocks
    model/                         the model's part. It decides, it never writes a file.
      prompts.py                     every instruction the model gets + the JSON it must answer in
      service.py                     sends them to the local model, checks the answers
    invoices/                      the Invoices tab, apart from the Word code
      reader.py                      a file -> pages (text and/or a picture). No model.
      checks.py                      clean, check, score the confidence. No model.
      export.py                      rows -> .xlsx / .csv. No model.
      prompts.py                     the model's part: what it is told, the JSON it answers in
      service.py                     the model's part: one page at a time to the local model
    word/                          the Word document tab, apart from both
      sources.py                     a source file -> text; Excel sheets as tables. No model.
      renderer.py                    Markdown -> a designed .docx (from Generate Documents, MIT). No model.
      build.py                       the page's choices + the Markdown -> the file. No model.
      prompts.py                     the model's part: what it is told
      service.py                     the model's part: sources to the local model, the text streamed back
  read_invoices.py                 the same reading from a terminal, for a folder of files
backend/app/documents/             the HTTP side
  routes.py                          /api/documents/... endpoints
  storage.py                         uploads, versions, the template library on disk
  schemas.py                         request bodies
  invoices.py                        /api/documents/invoices/... endpoints (stores nothing)
  word.py                            /api/documents/word/... endpoints
frontend/documents.html            the page
frontend/word.html                 its Word document tab
frontend/invoices.html             its Invoices tab
```

What is shared with the chat side, on purpose: the model registry and Ollama
client (`ollama_pipeline`), the `documents:` block and model list in
`ollama/config/models.yaml`, and `UnsupportedDocument`. To change what the
model is told, edit `model/prompts.py` only. To change what happens to a
file, edit `engine/`.

### Designed templates (a resume, a report with a fixed look)

Such a file has no blanks: it is full of sample text and its layout lives in
the body (tables, text boxes). It goes through the **Template library** tab:

1. **Prepare, once.** The model labels every line: `fixed` (a heading that
   stays), `field` (one value), or `group_item` (part of a repeating block:
   job 2's title, its bullets). A person reviews the colours on the page,
   corrects by clicking a line, and saves. Stored in
   `backend/data/templates/<id>/` (template.docx, labels.json, slotmap.json);
   not deleted by `keep_hours`.
2. **Fill, any number of times.** The model moves the user's content into the
   slot map's shape (`docgen.extract`), the user reviews it, and
   `render.py` writes it into a copy of the template: repeated blocks are
   copied for more items and removed for fewer, text boxes are written in both
   their copies, skill-chip boxes can be dropped but not added.

Code: `engine/slotmap.py` (labels -> slot map, JSON schema for the model),
`engine/render.py` (fill + clone/remove), `prepare` and `extract` in
`model/service.py` with their instructions in `model/prompts.py`.

The label view shows the whole template and scrolls with the page; its
controls are docked at the bottom of the screen (`.dock`). Do not put the
template back into a fixed-height box: on a 12-page file that reads as stuck.

Rules to keep:
- "Write a new one" never replaces the body of a designed template
  (`WordFile.is_letterhead()`); it only writes onto a real letterhead.
- Text boxes are numbered `t1`, `t2`, ... (modern copy only);
  `WordFile.save()` copies changes into the fallback copy. Do not resize shapes.
- JSON calls send `think: false` to thinking models (`ModelEntry.thinking`).
- The documents model is `documents.model` in `models.yaml` (`qwen3:14b`).

Status. Tested without a model, on the brown two-column resume template:
text boxes read and written in both copies; slot map built from hand labels;
a made-up resume with 4 jobs (2-5 bullets), 2 schools and fewer skills
rendered with the design kept and no sample text left in the XML; a
follow-up edit changed only the profile box; library routes; the refusal to
write over a designed template. Looked at with macOS Quick Look only.

**Not done, check these on the server with `qwen3:14b`:**
- `prepare` and `extract` have never been run against any model. Count how
  many of the brown template's 37 lines the model labels right before
  correction (expected labels: fields name, title, phone, email, address,
  linkedin, summary; groups jobs x3, education, skills, additional_skills;
  six fixed headings).
- The Template library tab has not been clicked through in a browser.
- No file was opened in Word or LibreOffice. Open one result in Word and
  confirm there is no repair prompt (copied text boxes get new drawing ids).
- LibreOffice is not installed here, so the PDF preview path
  (`_pdf` in `backend/app/documents/routes.py`) is untested; it is skipped when
  `soffice` is missing.
- Only the resume template was tried; a second kind (report, letter with a
  table) still needs to go through prepare -> fill.

### Word document tab (several sources into one designed document)

`frontend/word.html`, a tab of the documents page. Sources in: PDF, `.docx`,
`.xlsx`/`.xlsm`, CSV, text. Out: a `.docx` built by the app, and a PDF of it.

What it has, all chosen on the page: seven kinds of document (report,
proposal, white paper, memo, minutes, letter, plain), one colour that the whole
design is built on, a cover page, a contents page, an author line, a company
letterhead (header, footer and margins kept, body replaced). In the text:
numbered headings, tables with a coloured header and right-aligned figures,
callout boxes (info, success, warning, danger), checklists, quotations, code
blocks, signature blocks, page breaks, highlighted figures.

How a document is made (`documents/document_engine/word/`):

1. `sources.py` (no model) turns each file into text. PDF, Word and text go
   through the chat's `extract_text`; an Excel sheet becomes a Markdown table.
2. `service.py` + `prompts.py` (the model's part) send the instructions and
   all the sources to the local model, which writes the document as Markdown.
   It streams to the page as it is written.
3. `build.py` (no model) puts the page's choices on top of that text and
   `renderer.py` (no model) writes the Word file.
4. The file is an ordinary job of the documents page: downloaded through
   `/api/documents/{job}/download`, changed through `/api/documents/{job}/edit`.
   `/api/documents/word/{job}/pdf` converts the newest version with LibreOffice.

The text stays on the page and can be edited. "Build again" makes the file
from it with another kind, colour or pages, without asking the model.

Where the renderer comes from: `renderer.py` is "Generate Documents" 1.2.4 by
IANUSTEC, a tool written for Open WebUI (MIT, licence file beside it). It was
cut down by script: everything that talks to Open WebUI and everything that
goes to the network (Unsplash, image generation, pictures fetched from a URL)
is gone. The design system, templates, Markdown parser and renderers are
unchanged, so its README still describes what the Markdown may contain.

OfficeCLI (github.com/iOfficeAI/OfficeCLI) was read and **not** integrated.
It is a compiled .NET program that updates itself and installs agent skills on
the machine it runs on, so it was not downloaded or run here. Most of what it
does for Word this project already does in Python: edit operations, filling
a template's blanks, contents page, headers and footers. Its HTML and PNG
rendering of a `.docx` is the one thing that would be new.

Rules to keep:
- **Local models only, always.** There is no cloud path in `word/service.py`
  and `documents.allow_cloud` does not apply to it. The page never sends a
  request without a local model picked.
- **The model writes Markdown, never the Word file.**
- **Nothing is fetched.** A picture the model links to is left out.
- **The page's choices win** over anything the model wrote in the frontmatter.
  A letterhead is only ever the file the user attached, never a name in the text.
- **A designed template is refused as a letterhead** (`WordFile.is_letterhead()`),
  before the model starts.
- **Each source gets an equal share of the context** (`share()` in
  `service.py`); what a short file does not use goes to the longer ones. A
  file that is cut is named in a note on the page.
- The contents page lists the section names as text. Word replaces that with
  the real contents and page numbers when the file is opened; a PDF keeps the
  names without page numbers (`_list_contents` in `build.py`).

Status. On this laptop there is no Ollama, so the model was a stand-in that
streams a hand-typed document. Run that way: reading every source type, the
page in a browser (add files, write, edit the text, build again with another
kind and colour, the change box, both downloads), letterhead, the refusal of
a designed template, the PDF through LibreOffice 26.8 on macOS, and
`python -m document_engine.word`. The upstream example `report.md` builds to
a file of the same size as upstream's own `demo_report.docx`.

**Not done, check these on the server:**
- **No real model has written a document.** Check that `qwen3:14b` keeps to
  the Markdown in `prompts.py`: the `---` title lines first, callouts written
  as `::: callout`, figures from an Excel sheet put in a table and copied
  exactly. A model that ignores the title lines still gives a document: the
  first `# ` heading becomes the title.
- Several long sources at once: how much of each fits at `num_ctx` 16384, and
  whether the note about cut files appears.
- No built file was opened in Word itself, only in LibreOffice and python-docx.
  Open one with a contents page and confirm Word offers to update it.
- PDF: fonts. The templates ask for Calibri; LibreOffice on a server without
  it substitutes another font and the line breaks move.
- Two PDF requests at the same moment can fail (one LibreOffice profile);
  the second click works. Noted as a shortcut in `backend/app/documents/word.py`.
- Scanned PDFs and photos are not read here. The Invoices tab reads pictures;
  this tab says so and refuses them.

### Invoices tab (invoices into a sheet)

`frontend/invoices.html`, the fifth tab of the documents page. Files in: PDF,
`.docx`, JPG/PNG/WEBP/BMP/TIFF. Out: `.xlsx` or `.csv` with exactly these
columns, in this order: Invoice File Name, InvoiceId, Invoice Date, DueDate,
InvoiceTotal, VendorName, VendorAddress, CustomerName, CustomerId,
BillingAddress, BillingAddressRecipient, VendorAddressRecipient, VendorGST,
CustomerGST, Description, Qty, UnitPrice, UnitAmount, Discount, TaxableValue,
CGSTAmount, SGSTAmount, IGSTAmount, TotalTaxAmount, HSNSAC, Currency,
DocumentType, Confidence. Confidence is last, as asked.

**One row for each line of an invoice.** A bill with seven items is seven rows:
Description, Qty, UnitPrice, UnitAmount and HSNSAC are the line's own. The
invoice's fields (number, date, seller, buyer, GST numbers, total, taxes, Confidence)
and the file name stand on its first row, as on the page; the rows after it
carry only the line. Confidence is written as a percentage (80%). Tick boxes under
the sheet add: a Summary sheet (one row for each invoice), the Checks sheet, a
totals row, the invoice's details on every row, its totals on every row.
A document with no item table (a taxi receipt, a payment screenshot) is one row.

How a file is read (`documents/document_engine/invoices/`):

1. `reader.py` (no model) turns the file into pages. A PDF page with real text
   gives its text, laid out as on the page, **and** a picture of the page. A
   scan or photo gives a picture only. A Word file gives its text, and each
   large picture inside it as a page.
2. `service.py` + `prompts.py` (the model's part) send one page at a time to
   the local model, which answers in JSON: the invoices, receipts and payment
   confirmations on that page. One file can give several rows (a claim with
   three bills, 24 taxi receipts).
3. `checks.py` (no model) cleans each value, checks it, and works out the
   Confidence. Pages of one invoice are merged into one row.
4. A person reviews the rows on the page, then `export.py` writes the sheet.

Rules to keep:
- **Local models only, always.** There is no cloud path in this code and
  `documents.allow_cloud` does not apply to it.
- **Nothing is stored.** A file is read in memory and forgotten; the rows live
  in the browser until downloaded.
- **The model never sees the file name.** The names here start with an upload
  time that reads like a date. The earlier attempt returned a wrong invoice
  date for a scanned file whose only text was a print header and that name.
- **A PDF page with under 200 characters of text is treated as a scan**
  (`MIN_TEXT`): what text it has is a print header or footer.
- **Nothing the model returns is trusted as is.** `Confidence` is a score from
  checks, not a probability: a value found in the file's own text, a GSTIN
  whose check digit is right, or a total that matches the amount in words
  counts 1.0; a value read from a picture with nothing to confirm it 0.8; a
  failed check or a value the model marked unclear 0.35. The weights are at
  the top of `checks.py` and reach the page through `/config`.
- Dates are read day first (Indian invoices) and written as `31-Mar-2026`
  (`invoices.date_format`). A due date is never worked out from payment terms.
- A PAN is not put in a GST column.
- **The tax breakdown must add up.** Taxable value (less a discount) + tax =
  total, within a rupee of round-off. When it does, those figures count as
  confirmed, on a scan too. When it does not, the figures the file's own text
  does not confirm are marked, with the sum in the note. An invoice with a
  charge that is neither (freight billed outside the taxable value) is marked
  as well: that is a prompt to look, not an error.
- `TotalTaxAmount` is not asked of the model: it is CGST + SGST + IGST, added
  here. A page with one tax figure and no split keeps that figure. CGST and
  SGST that differ, or IGST beside them, are marked. A rate ("9%") is never
  taken for an amount; a tax printed as 0.00 or a dash is left empty.
- `Currency` is read off the sign or code the model copied (₹, Rs, INR,
  "Rupees ... Only"), `DocumentType` is what the model called the page
  (Invoice, Receipt, Payment). Neither counts towards Confidence.
- **The lines can confirm, they do not mark.** Quantity x unit price making the
  line's amount, and the lines adding up to the total (or the total before
  tax, or the taxable value), count as confirmed. When they do not, no cell is
  marked; a sum that is off is said once in a note. The second server run
  (1,345 rows, 9 October) had 355 line amounts marked, mostly bills whose
  lines carry discounts. The Checks sheet lists only values marked for
  checking and notes on unconfirmed values, not fields that are simply absent. Lines for totals and
  taxes that the model lists as items are dropped (`_NOT_AN_ITEM`).
- A row is finished in `merge` (`finish`), after the pages of one invoice are
  together: only then are all its lines known. `clean` alone returns a row
  with no Confidence yet.
- Seen in the first real run, and handled in `checks.py`: the invoice total
  entered as a tax (dropped); one tax amount entered as both CGST and SGST
  (moved to IGST when only one such amount fits the total); payment terms
  entered as a due date (left empty); a year more than three years back
  (marked).
- Text in the sheet is written as text, never as a formula: invoices come from
  outside the company.

Settings: the `invoices:` block in `models.yaml`. To change what the model is
told, edit `invoices/prompts.py` only.

Status. On this laptop there is no Ollama, so everything here was run against
a stand-in that returns hand-typed answers: reading every file type, the
checks (on the real GSTINs, dates, totals, amounts in words and line items of
the samples), merging, the page in a browser (add, read, stop, edit, add and
remove a line, tick, remove, both downloads), the Excel and CSV files, the
error messages, and `python -m app` without the project's packages installed.

On the server the user ran it with a real model on 8 October 2026 and
reported that it works. Their sheet from that run is what the line-by-line
rows, UnitPrice and UnitAmount, and the fixes listed above come from.

**Not done, check these on the server:**
- **The line-by-line reading has not been run by a real model.** The model is
  now asked for a list of lines (`Items`) where it gave one description
  before. Check that it gives one entry for each line, with the unit price and
  the amount in the right places, on the two files this was built from: a
  printed cash bill with seven items, and a photo of a handwritten bill.
- A page's answer is longer now (a line is about 50 tokens), so pages with
  many lines take longer; `MAX_ANSWER_TOKENS` in `service.py` is 6000.
- Speed per page and memory on the 16 GB card (`ollama ps`).
- Handwriting. Expect mistakes there; the line sums, the Confidence and the
  orange marks are what should catch them.
- If small print is misread, raise `invoices.image_side` to 2000.
- No downloaded file was opened in Excel itself, only read back with openpyxl.
  A CSV made from the Excel file with Excel's plain "CSV" turns Kannada and
  other scripts into question marks; the page's own Download CSV keeps them.

## Your task on this machine

1. Get the app running against real Ollama (Setup below).
2. Run the checks in "What to verify" and note pass/fail for each.
3. Fix what fails, keeping to "Rules".
4. Report back: hardware, models pulled, what passed, what failed, what you
   changed, and anything you could not test.

## Setup

Requirements: Python 3.11+ (3.12+ for Von), [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com/download) running, and for the cloud routes
Node.js with OpenCode 1.18.34. Ask the user before installing Ollama or
pulling models — models are several GB each.

```bash
cd backend && uv sync --extra router    # Python packages, with the Von router
npm install -g opencode-ai@1.18.34      # OpenCode, the tested version
ollama pull llama3.2:3b      # "simple" route
ollama pull qwen3:14b        # "complex" route (~9 GB)
ollama pull gemma3:4b        # screenshots (optional)
./run.sh                     # http://127.0.0.1:8000
```

Without uv, `pip install -r requirements.txt` from the repo root installs the
same pinned versions (tested in a fresh environment on Windows), and
`cd backend && python -m app` starts the server. The versions in use on the
test laptop: Python 3.13, uv 0.11, Node 24, Ollama 0.32.5, OpenCode 1.18.34,
Von 1.3.7.

`run.sh` needs bash and `lsof` (macOS/Linux/WSL). On plain Windows run
`powershell -ExecutionPolicy Bypass -File run.ps1` instead.

Downloads go to the user's home folder by default. If that disk is short of
space, set `UV_CACHE_DIR` before `uv sync` (packages) and `HF_HOME` in
`backend/.env` (Von's weights). Ollama keeps models where `OLLAMA_MODELS` points.

Check hosts and which configured models are pulled:

```bash
cd backend && uv run --inexact python -m ollama_pipeline
```

### Models: one file

`ollama/config/models.yaml` is the only place that names models: the Ollama
tags, the OpenCode ids, and which route uses which. To use other local models,
change the `model:` tag and the `label:` of the `general`, `reasoning` and
`vision` entries (the file's header says the same) and restart. Any other
model pulled in Ollama appears in the picker by itself (`discover: true`).
`python -m ollama_pipeline` shows what is ready.

### Fit the models to this machine's hardware

Check GPU memory first (`nvidia-smi`, or system info on a Mac).
`ollama/config/` targets the production server: edit it there. On any other
machine copy that folder to `ollama/config.local/` and edit the copy: it is
ignored by git and used automatically when present. The server log and
`python -m ollama_pipeline` print which folder is in use. In `models.yaml`:

- If `qwen3:14b` does not fit, point the `reasoning` entry at a smaller tag
  (e.g. `qwen3:8b`, or `qwen3:4b` on a 4 GB GPU) and pull that instead.
- `default_options.num_ctx` is 16384. If replies are very slow or `ollama ps`
  shows a model partly on CPU, lower it (8192). Do not go below 8192 unless
  necessary: attached documents get cut off.
- The target production server is an RTX 4080 Super (16 GB VRAM), 32 GB RAM,
  i7-13700K. The light and reasoning models must fit in 16 GB together.

### Optional: the Von router

```bash
cd backend && uv sync --extra router    # Python 3.12+, PyTorch, ~3 GB weights on first start
```

Restart and read the server log: it prints either `Router: Von is ready` or
why it fell back to keyword rules. Von is English-only and runs on CPU
(`router.device` in `models.yaml`).

## What to verify

Send these with **Auto** selected. Each reply's header shows the model used
("Auto · <model>"); hover it to see why.

| # | Do this | Expected |
| - | ------- | -------- |
| 1 | "write an email to my manager asking for leave" | light model answers |
| 2 | "review this contract clause and tell me the risks for the tenant: the tenant pays all repairs" | reasoning model answers |
| 3 | After #2, "make it shorter" | stays on the reasoning model |
| 4 | Attach a text PDF, ask a question about its content | reasoning model; answer uses the PDF |
| 5 | Attach a .docx, ask about it | same |
| 6 | Attach a long PDF (30+ pages) | warning about context size appears in the reply |
| 7 | Attach a screenshot, "what is this?" | vision model answers (needs gemma3) |
| 8 | Pick a specific model in the picker, send anything | that model answers, no routing |
| 9 | Press the send button mid-reply | generation stops |
| 10 | Stop Ollama, send a message | clear "Can't reach Ollama" error, no crash |
| 11 | With Von installed: repeat 1–3 | 1 and 2: header tooltip says "Routed by von" with a confidence. 3: "Routed by sticky" |

Also check `ollama ps` while testing: both routed models should stay loaded
and on the GPU. If Ollama unloads one each time the route changes, set
`OLLAMA_MAX_LOADED_MODELS=2` for the Ollama server.

Confirmed with Ollama 0.32.5 and Von 1.3.7:
- `qwen3` is a thinking model. Thinking arrives as separate `thinking` events
  and is shown live in the "Thinking…" block (seen with real Ollama: it
  filled for 61 s, then folded to "Thought for 61s"). On older Ollama
  versions it may arrive inline as `<think>…</think>` text instead, which
  would show up in the answer.
  On slow hardware the thinking can last a minute.
- Vision detection uses `capabilities` from Ollama's `/api/show`, which
  0.32.5 reports.
- `von.decide(...)` returns `.choice` and `.confidence` as the router expects
  and honours `VON_DEVICE`. The first start downloads 3 GB; later starts load
  in under a minute.
- A conversation larger than `num_ctx` is cut from the start, not the end: a
  40-page PDF at `num_ctx` 8192 was answered from its last four pages only
  (Ollama reported 4,098 prompt tokens). The warning appears, but the answer
  does not say what was skipped.

## Where things are

```
frontend/index.html              whole UI (one file)
backend/app/routes.py            API endpoints
ollama/config/models.yaml        every model, route and num_ctx  ← the one file for models
ollama/config.local/             optional per-machine copy of config/, not in git
ollama/.opencode/                the app's OpenCode keeps its own data here, not in git
ollama/ollama_pipeline/
  context.py                     fits a request to the model's context (oldest turns first)
  router.py                      which model answers (Von + keyword rules)
  pipeline.py                    builds the prompt, streams the reply, falls back
  registry.py                    which models exist / are pulled
  opencode.py                    runs OpenCode's server and streams from it
  documents.py                   PDF/DOCX/text → text (chat attachments)
  client.py                      raw calls to Ollama
llamacpp/                        the app on llama-server instead of Ollama (PIPELINE=llamacpp)
  config/models.yaml             its two models, and which one Documents, Word and Invoices use
  server/models.ini              what llama-server loads: model files, context, slots (Admin Panel -> Models)
  server/start.sh                starts the container llama-server-webui on 127.0.0.1:8101
  server/start-embed.sh          starts llama-embed-webui (EmbeddingGemma, CPU) on 127.0.0.1:8102
  llamacpp_pipeline/
    client.py                    raw calls to llama-server, answered in Ollama's shapes
    registry.py                  Ollama's registry with that client
    config.py                    reads config/models.yaml; num_ctx from models.ini
    server_config.py             reads/writes models.ini's ctx-size and parallel, restarts the container
sql/sql_connector/               Databases: saved connections, read-only checks, the model's query tools
workspaces/workspace_engine/     Workspaces (passages, search) and chat files kept on the server
skills/agent_skills/             Charts and Memory, and the switches for them
backend/app/sql/, workspaces/, skills.py   their HTTP routes
frontend/workspaces.html, databases.html, admin-features.html   their pages
documents/document_engine/       the Word documents page: engine/ (files) and model/ (prompts, calls)
documents/document_engine/invoices/   the Invoices tab: reader, checks, export, and the model's part
documents/document_engine/word/  the Word document tab: sources, renderer, build, and the model's part
documents/read_invoices.py       invoices from a terminal
backend/app/documents/           its HTTP routes and storage
frontend/documents.html          its page
frontend/invoices.html           the Invoices tab
frontend/word.html               the Word document tab
requirements.txt                 pinned Python packages for pip, made from backend/uv.lock
run.sh, run.ps1                  start the app (macOS/Linux, Windows)
```

## Rules

- Calls go one way: `frontend → backend → documents → ollama`, or
  `→ llamacpp → ollama` in llama-server mode. No Ollama- or llama.cpp-specific
  code in `backend/`; it only calls the `ollama_pipeline`, `llamacpp_pipeline`
  and `document_engine` packages, and picks the pipeline from `PIPELINE`.
  `ollama/` never imports from `documents/` or `llamacpp/`. `llamacpp/` reuses
  the chat steps of `ollama_pipeline` and does not change them.
- One model server on the GPU at a time: llama-server holds 14.7 of the 16 GB,
  and a model Ollama loads meanwhile fails with "CUDA out of memory".
- Routing must never block a chat. Any router failure falls back to keyword
  rules; a missing routed model falls back to another one. Keep it that way.
- `frontend/index.html` follows a pixel-exact design. On desktop the composer
  toolbar uses absolute coordinates — do not convert it to flexbox or change
  those numbers.
- Backend, `ollama/` or `llamacpp/` changes need a server restart; a change to
  `llamacpp/server/models.ini` also needs `llamacpp/server/start.sh` again (Admin Panel -> Models
  does both for ctx-size and parallel). Frontend changes only
  need a browser refresh.
- New Python dependency: add it to the right `pyproject.toml`, then
  `cd backend && uv sync --inexact` (plain `uv sync` removes the Von extra),
  and regenerate `requirements.txt` with the command in its header.
- Do not commit `.env`, model weights or the user's documents.
- Work on a branch and open a pull request against `main`; do not push to
  `main` directly.

## Not built yet

No "forgot password" by email, no
per-user permissions on workspaces and databases (everyone signed in uses all of them, only admins change them),
no text from scanned PDFs (the chat reads them as
pictures instead; the Word tab refuses them), Figma links are
passed as text only, no paid cloud models (e.g. Claude) - only OpenCode's
free tier.
