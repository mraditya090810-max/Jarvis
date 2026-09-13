"""
telegram_service/config.py — loads all secrets/config from environment
variables (optionally via a local .env file for development), the same
pattern whatsapp_service/config.py and twilio_service/config.py already
use. Nothing here is ever hard-coded.

Required environment variables (see telegram_service/.env.example):
    TELEGRAM_BOT_TOKEN         - from @BotFather, after creating your bot
    TELEGRAM_WEBHOOK_SECRET    - a string you invent yourself, registered
                                  with Telegram via setup_webhook.py; every
                                  real webhook request carries it back so
                                  we can tell it's genuinely from Telegram
    AUTHORIZED_TELEGRAM_USER_ID - your own numeric Telegram user ID (get it
                                  for free from @userinfobot — just message
                                  it once, no bot setup needed)

Optional:
    PUBLIC_BASE_URL            - only needed to run setup_webhook.py, e.g.
                                  https://your-app.onrender.com (no
                                  trailing slash)
    AI_ANSWER_TIMEOUT_SECS     - timeout for the general-question AI call
                                  (default 30 — a chat can wait a little
                                  longer than a live phone call)
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass


TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET: str = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
AUTHORIZED_TELEGRAM_USER_ID: str = os.environ.get("AUTHORIZED_TELEGRAM_USER_ID", "").strip()
AI_ANSWER_TIMEOUT_SECS: int = int(os.environ.get("AI_ANSWER_TIMEOUT_SECS", "30"))

TELEGRAM_API_BASE: str = "https://api.telegram.org"


def is_fully_configured() -> bool:
    """True once every secret needed to actually receive/verify messages is
    present."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET and AUTHORIZED_TELEGRAM_USER_ID)


def missing_vars() -> list[str]:
    names = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": TELEGRAM_WEBHOOK_SECRET,
        "AUTHORIZED_TELEGRAM_USER_ID": AUTHORIZED_TELEGRAM_USER_ID,
    }
    return [name for name, value in names.items() if not value]
