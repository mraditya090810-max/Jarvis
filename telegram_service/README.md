# JARVIS Telegram Bot

Message your own JARVIS Telegram bot in plain English — add/list/complete
tasks, or ask it anything else — using the official Telegram Bot API.
This runs as its own small backend service, independent of the desktop
JARVIS app, and keeps working even when your PC is off.

**Cost: $0, permanently.** The Telegram Bot API has no fees, no paid
tiers, no per-message charges, and no card required — unlike WhatsApp's
Business API (pay-per-conversation past the free tier) and Twilio Voice
(pay-per-minute, and India specifically restricts free trial numbers).
Hosting is the same free Render/Railway/Fly.io tier the WhatsApp service
already uses.

```
Telegram app
  -> Telegram Bot API
    -> JARVIS Telegram Webhook       (telegram_service/webhook.py)
      -> Message pipeline            (telegram_service/handler.py)
        -> to-do command?  -> whatsapp_service/nlu.py + handler.py
        -> anything else?  -> core/ai (free AI router)  (telegram_service/qa.py)
      -> plugins/todo_list.py        (shared with desktop + WhatsApp)
        -> memory/todo_tasks.json    (persistent storage)
```

## 1. Why it's built this way

- **Reuses the existing to-do brain instead of duplicating it.**
  `whatsapp_service/nlu.py` (intent parsing) and
  `whatsapp_service/handler.py`'s `build_reply()` already turn free text
  into a to-do action + a reply. `telegram_service/handler.py` calls that
  directly, so "add buy milk" is understood identically over WhatsApp or
  Telegram — one place understands to-do phrasing, not two.
- **General questions use `core/ai`** (the same free-first OpenRouter
  router `code_helper.py` already uses), so this stays a standalone
  process with no desktop-app dependency — it keeps working with the PC
  off, same as `whatsapp_service`.
- **No dependency on `twilio_service`.** Even though both now answer
  general questions the same way, `telegram_service/qa.py` is its own
  small copy rather than an import from `twilio_service`, so either
  service can be deleted without touching the other.
- **No new pip dependency.** Telegram's Bot API is plain HTTPS + JSON —
  `telegram_client.py` talks to it directly with `requests`, already in
  `requirements.txt`.

## 2. Files created

| File | Purpose |
|---|---|
| `telegram_service/__init__.py` | Package marker. |
| `telegram_service/config.py` | Loads all secrets from environment variables (+ optional local `.env`). |
| `telegram_service/security.py` | Sender allowlist + webhook secret-token verification. |
| `telegram_service/telegram_client.py` | Sends replies via the official Telegram Bot API. |
| `telegram_service/qa.py` | Answers general questions via `core/ai`'s free AI router. |
| `telegram_service/handler.py` | Ties security + to-do routing + general Q&A + reply together. |
| `telegram_service/webhook.py` | The FastAPI app: `POST /webhook`, `GET /health`. |
| `telegram_service/setup_webhook.py` | One-time script to register your webhook URL with Telegram. |
| `telegram_service/local_test.py` | Try the whole pipeline from a terminal — no bot token or network needed. |
| `telegram_service/.env.example` | Template for your secrets — copy to `.env` and fill in. |
| `run_telegram_service.py` | Convenience launcher at the project root. |
| `tests/test_telegram_bot.py` | Offline tests: allowlist, secret-token verification, message routing. |

**Nothing existing was modified** — `plugins/todo_list.py`,
`whatsapp_service/`, `twilio_service/`, and `requirements.txt` are untouched.

## 3. Create your bot (free, ~2 minutes)

1. Open Telegram, search for **@BotFather** (the official bot-creation bot).
2. Send it `/newbot`, give it a name and a username (must end in `bot`,
   e.g. `MyJarvisBot`).
3. BotFather replies with your **bot token** — looks like
   `123456789:AAExampleTokenTextGoesHere`. Copy it.
4. Message **@userinfobot** (a separate, well-known utility bot) once — it
   instantly replies with your own numeric **user ID**. Copy that too.

## 4. Environment variables

Copy `telegram_service/.env.example` to `telegram_service/.env` and fill in:

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather in step 3 |
| `TELEGRAM_WEBHOOK_SECRET` | You invent this — any random string |
| `AUTHORIZED_TELEGRAM_USER_ID` | Your numeric ID from @userinfobot in step 4 |
| `PUBLIC_BASE_URL` | Only needed to run `setup_webhook.py` — your public HTTPS URL, no trailing slash |
| `AI_ANSWER_TIMEOUT_SECS` | Optional, defaults to `30` |

`.env` is only read locally for convenience — never commit it. On a real
host, set these as actual environment variables in the platform's
dashboard/config instead.

## 5. Try it locally without deploying anything

```bash
python -m telegram_service.local_test
```

Type things like `add buy milk`, `what are my tasks`, or `what's the
capital of France`. This exercises the exact same `handler.py` code the
real webhook calls — no bot token needed yet.

## 6. Run the webhook and register it with Telegram

```bash
pip install -r requirements.txt
cp telegram_service/.env.example telegram_service/.env
# then edit telegram_service/.env with your real values
python run_telegram_service.py
```

For local testing with the real Telegram app, you need a public HTTPS URL:

```bash
ngrok http 8082
```

Set `PUBLIC_BASE_URL` in your `.env` to the ngrok URL, then register the
webhook (one-time, and again any time the URL changes):

```bash
python -m telegram_service.setup_webhook
```

Check it's alive:

```bash
curl http://localhost:8082/health
```

## 7. Test it

1. Open your bot in Telegram (search for the username you gave BotFather)
   and send `/start`.
2. Try:
   - "I need to finish my physics assignment tomorrow" → adds a task
   - "What are my tasks?" → reads them back
   - "Mark the physics assignment as done"
   - "What's the capital of France?" → answered via the AI router
3. Watch the terminal running `uvicorn` for logs.
4. Have someone else message your bot — it should get no reply at all.
   Check the logs to confirm it was blocked and see their user ID logged
   (useful if you ever want to authorize a second person).

## 8. Deploy so it works remotely (PC can stay off)

Same idea as `whatsapp_service`/`twilio_service` — any small always-on
Python host works (Render, Railway, Fly.io, etc.), and it's free:

1. Push this project to the host.
2. Set the environment variables from step 4 in the host's dashboard (not
   a committed `.env` file) — set `PUBLIC_BASE_URL` to the permanent
   HTTPS URL the platform gives you.
3. Start command: `python run_telegram_service.py` (reads `PORT` from the
   environment automatically).
4. Run `python -m telegram_service.setup_webhook` once, pointed at that
   permanent URL (either from your own machine with `PUBLIC_BASE_URL` set
   to the deployed URL, or as a one-off command on the host).

Once deployed, messaging your bot works with your desktop JARVIS app and
PC completely off — and if you also deployed `whatsapp_service` and/or
`twilio_service`, all three run independently, at the same time, for the
same $0.

## 9. Notes and limitations

- **To-do commands are understood by the same rule-based parser
  WhatsApp uses** (`whatsapp_service/nlu.py`) — fast, free, and works
  without any AI key configured, but won't understand truly novel
  phrasing. Anything it doesn't recognize as a to-do command is treated
  as a general question instead of guessing wrong.
- **General questions go through `core/ai`**, which only uses free
  OpenRouter models and can occasionally be slow or briefly unavailable;
  `qa.py` catches that and replies saying so rather than going silent.
- **Desktop, WhatsApp, and Telegram share the same task list only if they
  share the same `todo_tasks.json`** — set `TODO_STORAGE_PATH` to the
  same value everywhere you deploy, exactly as documented in
  `whatsapp_service/README.md`.
- **Unauthorized senders get no reply at all** rather than being told
  they're blocked, so the bot's existence isn't revealed to strangers who
  might find its username — same philosophy as the WhatsApp allowlist.
- Every webhook request is checked for the correct `X-Telegram-Bot-Api-
  Secret-Token` header before anything else runs; if it's missing or
  wrong (e.g. `setup_webhook.py` was never run, or the secret in `.env`
  doesn't match what you registered), requests are rejected with a log
  line saying so.
