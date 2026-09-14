# JARVIS Telegram hourly reminders

This update adds cloud-backed Telegram reminders that continue working when the JARVIS PC is OFF.

## Architecture

PC JARVIS <-> Firestore <-> Telegram webhook / GitHub Actions reminder worker <-> phone

The reminder worker is one-shot. GitHub Actions starts it once per hour, checks Firestore, sends a Telegram message when appropriate, and exits.

## Required cloud variables

Use the SAME Firebase project/credentials on the desktop and the Telegram service:

- `TODO_STORAGE_BACKEND=firestore`
- `FIREBASE_CREDENTIALS_JSON=<your Firebase service-account JSON as one line>`
- `FIRESTORE_COLLECTION=jarvis`
- `FIRESTORE_DOCUMENT=todo_tasks`
- `TELEGRAM_BOT_TOKEN=<your bot token>`
- `AUTHORIZED_TELEGRAM_USER_ID=<your numeric Telegram user id>`
- `TELEGRAM_CHAT_ID=<your private chat id; normally the same number>`
- `JARVIS_TIMEZONE=Asia/Kolkata`

Do not commit credentials or `.env` files.

## Telegram commands

- `/tasks`
- `/today`
- `/progress`
- `/done <number>`
- `/reminders on`
- `/reminders off`
- `/reminders 2` (every 2 hours; 1–24 accepted)

## GitHub Actions

Copy the repository files to your GitHub repository. In GitHub:

**Settings -> Secrets and variables -> Actions -> New repository secret**

Add:

- `TELEGRAM_BOT_TOKEN`
- `AUTHORIZED_TELEGRAM_USER_ID`
- `TELEGRAM_CHAT_ID`
- `FIREBASE_CREDENTIALS_JSON`

The workflow `.github/workflows/jarvis_hourly_reminders.yml` runs at minute 0 of every hour. GitHub may occasionally delay scheduled jobs by a few minutes.

## First-time behavior

Reminders are enabled by default at a 1-hour interval. The first run sends a message only when at least one pending task exists. `/reminders off` or `/reminders 2` changes the persistent Firestore setting.
