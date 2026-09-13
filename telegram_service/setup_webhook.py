"""
telegram_service/setup_webhook.py — run this once (and again any time
PUBLIC_BASE_URL changes, e.g. after deploying) to tell Telegram where to
send updates. Telegram has no dashboard for this like Meta/Twilio — it's a
single API call.

Usage (reads TELEGRAM_BOT_TOKEN, PUBLIC_BASE_URL, TELEGRAM_WEBHOOK_SECRET
from telegram_service/.env or your real environment variables):

    python -m telegram_service.setup_webhook

To remove the webhook (e.g. to switch back to local testing):

    python -m telegram_service.setup_webhook --delete
"""
from __future__ import annotations

import sys

import requests

from telegram_service import config


def set_webhook() -> None:
    missing = [
        name for name, value in {
            "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
            "PUBLIC_BASE_URL": config.PUBLIC_BASE_URL,
            "TELEGRAM_WEBHOOK_SECRET": config.TELEGRAM_WEBHOOK_SECRET,
        }.items() if not value
    ]
    if missing:
        print(f"Missing required config: {', '.join(missing)}. Fill in telegram_service/.env first.")
        sys.exit(1)

    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {
        "url": f"{config.PUBLIC_BASE_URL}/webhook",
        "secret_token": config.TELEGRAM_WEBHOOK_SECRET,
    }
    resp = requests.post(url, json=payload, timeout=15)
    print(resp.status_code, resp.json())


def delete_webhook() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        print("Missing required config: TELEGRAM_BOT_TOKEN.")
        sys.exit(1)
    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/deleteWebhook"
    resp = requests.post(url, timeout=15)
    print(resp.status_code, resp.json())


if __name__ == "__main__":
    if "--delete" in sys.argv:
        delete_webhook()
    else:
        set_webhook()
