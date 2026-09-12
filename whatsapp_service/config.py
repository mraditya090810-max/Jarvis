"""
whatsapp_service/config.py — loads all secrets/config from environment
variables (optionally via a local .env file for development). Nothing here
is ever hard-coded, per the project's security requirements.

Required environment variables (see whatsapp_service/.env.example):
    WHATSAPP_ACCESS_TOKEN      - Meta permanent/temporary access token
    WHATSAPP_PHONE_NUMBER_ID   - the "Phone number ID" from Meta App dashboard
    WHATSAPP_VERIFY_TOKEN      - a string you invent, used for webhook verification
    AUTHORIZED_WHATSAPP_NUMBER - your own WhatsApp number, digits only, e.g. 15551234567

Optional:
    WHATSAPP_API_VERSION       - Graph API version (default: v20.0)
    TODO_STORAGE_PATH          - override for where tasks.json lives
                                  (also read directly by plugins/todo_list.py)
"""
from __future__ import annotations

import os
from pathlib import Path

# python-dotenv is optional — if present, load a local .env for convenience
# during development. In production, real environment variables set by the
# host (systemd, Docker, Render/Railway/Fly.io dashboard, etc.) take priority
# and are NOT overridden by a stray .env file.
try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass


def _clean_number(raw: str) -> str:
    """Digits only, no '+', spaces, or dashes — matches WhatsApp Cloud API's
    'wa_id' format so comparisons are reliable."""
    return "".join(ch for ch in raw if ch.isdigit())


WHATSAPP_ACCESS_TOKEN: str = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID: str = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN: str = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_API_VERSION: str = os.environ.get("WHATSAPP_API_VERSION", "v20.0")

_raw_authorized = os.environ.get("AUTHORIZED_WHATSAPP_NUMBER", "")
AUTHORIZED_WHATSAPP_NUMBER: str = _clean_number(_raw_authorized)


def clean_number(raw: str) -> str:
    return _clean_number(raw)


def is_fully_configured() -> bool:
    """True once every secret needed to actually send/receive is present.
    The webhook's GET verification route only needs WHATSAPP_VERIFY_TOKEN,
    so this is checked lazily where it matters, not at import time."""
    return bool(
        WHATSAPP_ACCESS_TOKEN
        and WHATSAPP_PHONE_NUMBER_ID
        and WHATSAPP_VERIFY_TOKEN
        and AUTHORIZED_WHATSAPP_NUMBER
    )


def missing_vars() -> list[str]:
    names = {
        "WHATSAPP_ACCESS_TOKEN": WHATSAPP_ACCESS_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
        "WHATSAPP_VERIFY_TOKEN": WHATSAPP_VERIFY_TOKEN,
        "AUTHORIZED_WHATSAPP_NUMBER": AUTHORIZED_WHATSAPP_NUMBER,
    }
    return [name for name, value in names.items() if not value]
