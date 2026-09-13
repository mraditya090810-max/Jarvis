"""
telegram_service/security.py — two independent checks, both fail closed:

1. is_authorized(user_id): only AUTHORIZED_TELEGRAM_USER_ID may talk to
   JARVIS, mirroring whatsapp_service/security.py and
   twilio_service/security.py.
2. validate_secret_token(...): proves a webhook request actually came from
   Telegram. Telegram lets you register a secret_token when you call
   setWebhook (see setup_webhook.py); every real update then arrives with
   header "X-Telegram-Bot-Api-Secret-Token" set to that exact value —
   https://core.telegram.org/bots/api#setwebhook
"""
from __future__ import annotations

import hmac

from telegram_service import config


def is_authorized(user_id) -> bool:
    """user_id is Telegram's numeric 'from.id' field (int in the JSON,
    compared here as a string for simplicity)."""
    if not config.AUTHORIZED_TELEGRAM_USER_ID:
        # Fail closed: an empty allowlist means nobody is authorized rather
        # than everybody.
        return False
    return str(user_id).strip() == config.AUTHORIZED_TELEGRAM_USER_ID


def validate_secret_token(header_value: str) -> bool:
    if not config.TELEGRAM_WEBHOOK_SECRET or not header_value:
        return False
    return hmac.compare_digest(header_value, config.TELEGRAM_WEBHOOK_SECRET)
