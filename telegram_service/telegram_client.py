"""
telegram_service/telegram_client.py — thin wrapper around the official,
free Telegram Bot API. No unofficial libraries — same "talk to the
provider's HTTP API directly" approach whatsapp_client.py uses.

Docs: https://core.telegram.org/bots/api
"""
from __future__ import annotations

import logging

import requests

from telegram_service import config

logger = logging.getLogger("telegram_service.client")

_TIMEOUT_SECS = 15
_MAX_MESSAGE_LEN = 4096  # Telegram's hard limit per sendMessage call.


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]


def send_text_message(chat_id, text: str) -> bool:
    """Send a plain-text message to `chat_id`. Splits messages longer than
    Telegram's 4096-character limit into multiple calls. Returns True only
    if every chunk sent successfully — never raises, since a failed reply
    should not crash the webhook."""
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("Cannot send Telegram message: TELEGRAM_BOT_TOKEN is not set.")
        return False

    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    for chunk in _chunks(text, _MAX_MESSAGE_LEN):
        try:
            resp = requests.post(
                url, json={"chat_id": chat_id, "text": chunk}, timeout=_TIMEOUT_SECS
            )
            if resp.status_code >= 400:
                logger.error("Telegram send failed (%s): %s", resp.status_code, resp.text[:500])
                ok = False
        except requests.RequestException as e:
            logger.error("Telegram send raised an exception: %s", e)
            ok = False
    return ok
