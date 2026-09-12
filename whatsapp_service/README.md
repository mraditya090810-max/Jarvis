# JARVIS WhatsApp To-Do List

Message your JARVIS WhatsApp number in plain English and it manages a
persistent To-Do List, using the official WhatsApp Business Cloud API.
This runs as its own small backend service, independent of the desktop
JARVIS app — it keeps working even when your PC is off.

```
WhatsApp
  -> WhatsApp Business Cloud API
    -> JARVIS WhatsApp Webhook        (whatsapp_service/webhook.py)
      -> JARVIS To-Do Handler         (whatsapp_service/handler.py)
        -> plugins/todo_list.py       (shared with the desktop assistant)
          -> memory/todo_tasks.json   (persistent storage)
```

## 1. What was inspected in your project first

- **`todo_list.py` did not exist yet** anywhere in the uploaded project —
  there was no prior to-do plugin or command to reuse. So it was created
  from scratch as `plugins/todo_list.py`, following the exact plugin
  contract every other file in `plugins/` already uses.
- **Plugin discovery**: `core/plugin_loader.discover_plugins()` scans
  `plugins/*.py` once at startup, imports each file, and requires a
  `PLUGIN` dict + a `run(parameters, player=None, session_memory=None)`
  function (see `plugins/_template.py`). Files starting with `_` are
  skipped. Name collisions with core tools (declared in `main.py`) are
  rejected automatically — `todo_list` doesn't collide with anything.
- **Plugin execution**: `main.py` merges plugin tool declarations into the
  Gemini Live tool list; unmatched tool calls fall through to
  `self.plugins.run(name, parameters, ...)`.
- **Persistent data**: other JARVIS state lives as JSON files under
  `memory/` (e.g. `memory/long_term.json`, `memory/action_memory.json`).
  `plugins/todo_list.py` follows the same pattern:
  `memory/todo_tasks.json`.
- **Where the webhook plugs in**: nowhere inside the existing desktop
  pipeline (`main.py`, `ui.py`, `dashboard/server.py` all assume the
  desktop app + Gemini Live session are running). Since the feature must
  keep working with the PC off, it's a **separate, standalone FastAPI
  app** (`whatsapp_service/`) that imports only `plugins/todo_list.py` —
  nothing else in the desktop app was touched.

## 2. Files created

| File | Purpose |
|---|---|
| `plugins/todo_list.py` | New JARVIS plugin. Also usable standalone (no PyQt6/Gemini dependency) — this is the single source of truth for tasks, used by both the desktop assistant and WhatsApp. |
| `whatsapp_service/__init__.py` | Package marker. |
| `whatsapp_service/config.py` | Loads all secrets from environment variables (+ optional local `.env`). |
| `whatsapp_service/security.py` | Allowlist check — only `AUTHORIZED_WHATSAPP_NUMBER` may act. |
| `whatsapp_service/whatsapp_client.py` | Sends replies via the official WhatsApp Cloud API (`graph.facebook.com`). |
| `whatsapp_service/nlu.py` | Rule-based natural-language intent router (ADD_TASK / LIST_TASKS / COMPLETE_TASK / DELETE_TASK / CLEAR_TASKS / TASK_STATUS / UNKNOWN). |
| `whatsapp_service/handler.py` | Ties security + NLU + `todo_list` + WhatsApp reply together. |
| `whatsapp_service/webhook.py` | The FastAPI app: `GET /webhook` (Meta verification), `POST /webhook` (incoming messages), `GET /health`. |
| `whatsapp_service/.env.example` | Template for your secrets — copy to `.env` and fill in. |
| `run_whatsapp_service.py` | Convenience launcher at the project root. |

**Files modified:** only `requirements.txt` (one line added: `python-dotenv`).
Nothing else in the existing project was changed.

## 3. Environment variables

Copy `whatsapp_service/.env.example` to `whatsapp_service/.env` and fill in:

| Variable | Where to get it |
|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Meta App Dashboard → WhatsApp → API Setup |
| `WHATSAPP_PHONE_NUMBER_ID` | Same page, under "Phone number ID" |
| `WHATSAPP_VERIFY_TOKEN` | You invent this — any random string |
| `AUTHORIZED_WHATSAPP_NUMBER` | Your own number, digits only with country code, e.g. `15551234567` |
| `WHATSAPP_API_VERSION` | Optional, defaults to `v20.0` |
| `TODO_STORAGE_PATH` | Optional, overrides where `todo_tasks.json` is stored |

`.env` is only read locally for convenience — never commit it. On a real
host, set these as actual environment variables in the platform's
dashboard/config instead.

## 4. Installation

From the project root:

```bash
pip install -r requirements.txt
```

(This adds `python-dotenv` to your existing environment; `fastapi`,
`uvicorn`, and `requests` were already in `requirements.txt`.)

```bash
cp whatsapp_service/.env.example whatsapp_service/.env
# then edit whatsapp_service/.env with your real values
```

## 5. Run the webhook locally

```bash
python run_whatsapp_service.py
```

or directly with uvicorn:

```bash
uvicorn whatsapp_service.webhook:app --reload --port 8080
```

Check it's alive:

```bash
curl http://localhost:8080/health
```

## 6. Configure the Meta WhatsApp webhook

1. In [Meta for Developers](https://developers.facebook.com/apps), open your
   app → **WhatsApp → Configuration**.
2. Local testing needs a public HTTPS URL — use a tunnel, e.g.:
   ```bash
   ngrok http 8080
   ```
   Copy the `https://....ngrok-free.app` URL it gives you.
3. Under **Webhook**, click **Edit** and enter:
   - **Callback URL**: `https://<your-tunnel-or-domain>/webhook`
   - **Verify token**: the exact value you put in `WHATSAPP_VERIFY_TOKEN`
4. Click **Verify and Save** — this triggers the `GET /webhook` check.
5. Under **Webhook fields**, subscribe to **messages**.
6. Make sure your own WhatsApp number is added as a tester number under
   **API Setup** (required while your app is in development mode).

## 7. Test adding a task from WhatsApp

1. From the phone number you set as `AUTHORIZED_WHATSAPP_NUMBER`, send your
   JARVIS WhatsApp test number a message:
   ```
   I need to finish my physics assignment tomorrow
   ```
2. You should get back:
   ```
   Added to your To-Do List: Finish my physics assignment — due Tomorrow.
   ```
3. Try the others:
   - `What are my tasks?`
   - `Mark the physics assignment as done`
   - `Delete the physics assignment task`
   - `Clear my completed tasks`
4. Watch the terminal running `uvicorn` for logs, and check
   `memory/todo_tasks.json` to see the stored tasks directly.
5. Message from a *different* number — it should be silently ignored (no
   reply, no task changes). Check the logs to confirm it was blocked.

## 8. Deploy so it works remotely (PC can stay off)

Any small always-on host that can run a Python web service works —
Render, Railway, Fly.io, a $5 VPS, etc. General steps:

1. Push this project (or at least `plugins/todo_list.py`,
   `whatsapp_service/`, `run_whatsapp_service.py`, and `requirements.txt`)
   to the host.
2. Set the environment variables from step 3 in the host's dashboard
   (not a committed `.env` file).
3. Start command: `python run_whatsapp_service.py` (it reads `PORT` from
   the environment automatically, so it works on hosts that assign their
   own port).
4. The platform gives you a permanent HTTPS URL — put
   `https://your-app.example.com/webhook` into the Meta webhook
   configuration (step 6), replacing the ngrok URL.
5. Make sure `TODO_STORAGE_PATH` points at a location on **persistent**
   disk on that host (some platforms wipe the filesystem on redeploy) —
   e.g. a mounted volume, or point it at a small database later if you
   outgrow a JSON file.
6. Generate a permanent WhatsApp access token (Meta's default tokens
   expire in 24 hours) via a System User in Business Settings, so the
   service doesn't stop working after a day.

Once deployed, the WhatsApp To-Do feature runs entirely on that server —
your desktop JARVIS app and Windows PC can be off and it keeps working.

## 9. Notes and limitations

- **Intent parsing is rule-based**, not an LLM call — fast, free, and
  works even without any AI API key configured on the server. It
  recognizes a broad set of phrasings (see `whatsapp_service/nlu.py`) but
  won't understand truly novel phrasing. Unrecognized messages get the
  documented fallback reply rather than guessing wrong. If you want LLM-
  based understanding later, `core/llm_client.py` (already in this
  project) can be called from `whatsapp_service/nlu.py` — the rest of the
  pipeline (`handler.py`, `webhook.py`) doesn't need to change.
- **Desktop + WhatsApp share the same task list only if they share the
  same `todo_tasks.json`.** By default each machine has its own file. If
  you want your desktop JARVIS and the cloud webhook to see the exact
  same tasks, point both at the same file/network path via
  `TODO_STORAGE_PATH`, or sync that file between them.
- Unauthorized senders are **silently ignored** (no reply at all) rather
  than told they're blocked, so the assistant's existence isn't revealed
  to unknown numbers.
