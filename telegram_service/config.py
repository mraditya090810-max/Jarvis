"""
telegram_service/config.py — Telegram and shared JARVIS configuration.

Loads:
1. Root JARVIS .env for local development
2. telegram_service/.env for Telegram-specific local settings
3. Real environment variables (Render/cloud deployment)

Environment variables supplied by the real environment always take priority.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _SERVICE_DIR = Path(__file__).resolve().parent
    _ROOT_DIR = _SERVICE_DIR.parent

    # Load shared JARVIS configuration first.
    _ROOT_ENV_PATH = _ROOT_DIR / ".env"
    if _ROOT_ENV_PATH.exists():
        load_dotenv(_ROOT_ENV_PATH, override=False)

    # Then load Telegram-specific local configuration.
    _SERVICE_ENV_PATH = _SERVICE_DIR / ".env"
    if _SERVICE_ENV_PATH.exists():
        load_dotenv(_SERVICE_ENV_PATH, override=False)

except ImportError:
    pass


# Telegram configuration
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET: str = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
AUTHORIZED_TELEGRAM_USER_ID: str = os.environ.get(
    "AUTHORIZED_TELEGRAM_USER_ID", ""
).strip()

AI_ANSWER_TIMEOUT_SECS: int = int(
    os.environ.get("AI_ANSWER_TIMEOUT_SECS", "30")
)

# Shared Todo/Firestore configuration.
# _todo_storage.py reads these directly from os.environ.
TODO_STORAGE_BACKEND: str = os.environ.get(
    "TODO_STORAGE_BACKEND", "file"
).strip().lower()

FIRESTORE_COLLECTION: str = os.environ.get(
    "FIRESTORE_COLLECTION", "jarvis"
).strip()

FIRESTORE_DOCUMENT: str = os.environ.get(
    "FIRESTORE_DOCUMENT", "todo_tasks"
).strip()


TELEGRAM_API_BASE: str = "https://api.telegram.org"


def is_fully_configured() -> bool:
    """True once every secret needed to receive/verify messages is present."""
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_WEBHOOK_SECRET
        and AUTHORIZED_TELEGRAM_USER_ID
    )


def missing_vars() -> list[str]:
    names = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": TELEGRAM_WEBHOOK_SECRET,
        "AUTHORIZED_TELEGRAM_USER_ID": AUTHORIZED_TELEGRAM_USER_ID,
    }
    return [name for name, value in names.items() if not value]