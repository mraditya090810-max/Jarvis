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
          -> plugins/_todo_storage.py (pluggable: local file, or shared
                                        Firestore — see section 8)
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

## 8. Shared cloud storage (Firestore) — so desktop JARVIS sees the same tasks

Free hosts like Render don't give free persistent disks, and even if they
did, a file sitting on that server's disk isn't the same file your
desktop JARVIS reads from. `plugins/_todo_storage.py` solves both
problems at once with a second storage backend: Firebase Firestore, a
free cloud database. Point BOTH the deployed webhook and your desktop
JARVIS at the same Firestore project, and every add/list/complete/delete
call — from WhatsApp or from your desktop — reads and writes the exact
same document. No sync step, no "catch up on startup" logic needed;
there's just one shared source of truth.

**One-time Firebase setup (a few minutes):**

1. Go to <https://console.firebase.google.com>, click **Add project**,
   give it any name (e.g. "jarvis-todo"), and finish the wizard (you can
   decline Google Analytics — not needed).
2. In the left sidebar: **Build → Firestore Database → Create database**.
   Choose any region close to you, start in **production mode**.
3. Click the gear icon next to "Project Overview" → **Project settings**
   → **Service accounts** tab → **Generate new private key**. This
   downloads a `.json` file — this is your `FIREBASE_CREDENTIALS_JSON`.
   Keep it secret (it's a full-access key to this database) and never
   commit it to git.
4. Install the one new dependency: `pip install firebase-admin` (already
   added to `requirements.txt`).

**Wire it up in two places:**

- **On the cloud host** (see deploy steps below): set
  `TODO_STORAGE_BACKEND=firestore` and `FIREBASE_CREDENTIALS_JSON` to the
  entire contents of that downloaded `.json` file, pasted as one line, in
  the host's environment-variables dashboard.
- **On your desktop PC**: set the same two environment variables before
  running JARVIS (e.g. in a `.env` your desktop startup already loads, or
  Windows "Edit environment variables for your account"). Easiest is to
  set `FIREBASE_CREDENTIALS_PATH` instead of the `_JSON` version — just
  save the downloaded key file locally (e.g.
  `config/firebase_key.json`) and point `FIREBASE_CREDENTIALS_PATH` at
  it, so you don't have to paste the whole JSON blob into a Windows env
  var box. Add that file to `.gitignore`.

If you skip this step entirely, everything still works exactly as before
— `TODO_STORAGE_BACKEND` defaults to `"file"`, i.e. the original local
`memory/todo_tasks.json` behaviour, untouched.

## 9. Deploy the webhook to Render (free, always-on, PC can stay off)

1. Push this project to a GitHub repo (private is fine).
2. Go to <https://render.com>, sign up/log in, **New → Web Service**,
   connect that repo.
3. Settings:
   - **Runtime**: Python 3
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `python run_whatsapp_service.py`
   - **Instance type**: Free
4. Under **Environment**, add every variable from step 3 above
   (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_VERIFY_TOKEN`, `AUTHORIZED_WHATSAPP_NUMBER`) plus
   `TODO_STORAGE_BACKEND=firestore` and `FIREBASE_CREDENTIALS_JSON` (the
   whole key file, one line) from step 8.
5. Click **Create Web Service**. Render builds it and gives you a
   permanent URL like `https://jarvis-todo.onrender.com`.
6. Back in Meta App Dashboard → WhatsApp → Configuration, set the
   **Callback URL** to `https://jarvis-todo.onrender.com/webhook` (no
   more ngrok needed) and **Verify and Save**.
7. Generate a **permanent** WhatsApp access token (Meta's default tokens
   expire in 24 hours) via a System User in Business Settings, and update
   `WHATSAPP_ACCESS_TOKEN` on Render with it — otherwise the service
   quietly stops working after a day.

That's it — text your JARVIS WhatsApp number any time, PC on or off, and
the task is there next time you open desktop JARVIS too (once step 8's
env vars are set on your desktop). Note: Render's free tier spins the
service down after 15 minutes idle and wakes it back up on the next
message (a few seconds' delay on the first message after a quiet
stretch) — normal, not a bug.

## 9. Notes and limitations

- **Intent parsing is rule-based**, not an LLM call — fast, free, and
  works even without any AI API key configured on the server. It
  recognizes a broad set of phrasings (see `whatsapp_service/nlu.py`) but
  won't understand truly novel phrasing. Unrecognized messages get the
  documented fallback reply rather than guessing wrong. If you want LLM-
  based understanding later, `core/llm_client.py` (already in this
  project) can be called from `whatsapp_service/nlu.py` — the rest of the
  pipeline (`handler.py`, `webhook.py`) doesn't need to change.
- **Desktop + WhatsApp share the same task list only if configured to.**
  By default both use their own local `todo_tasks.json` (fine for
  desktop-only use). To have your desktop JARVIS and the cloud webhook
  see the exact same tasks, set `TODO_STORAGE_BACKEND=firestore` on both
  — see section 8.
- Unauthorized senders are **silently ignored** (no reply at all) rather
  than told they're blocked, so the assistant's existence isn't revealed
  to unknown numbers.
