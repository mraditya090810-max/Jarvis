"""
telegram_service/handler.py — given an incoming Telegram text message,
this module:
  1. checks the sender against the allowlist (security.py),
  2. routes it: a to-do command reuses whatsapp_service's existing
     rule-based NLU + reply logic verbatim (so there's exactly one place
     that understands to-do phrasing, not three), anything else is
     answered as a general question via qa.py,
  3. sends the reply back over Telegram (telegram_client.py).

Kept free of any FastAPI/webhook-specific code so it can be unit-tested,
and so local_test.py can exercise generate_reply() with no network calls.
"""
from __future__ import annotations

import logging

from telegram_service import qa, security, telegram_client
from whatsapp_service import handler as wa_handler
from whatsapp_service import nlu

logger = logging.getLogger("telegram_service.handler")

HELP_TEXT = (
    "JARVIS here, sir. I can manage your To-Do List — try 'add buy milk', "
    "'what are my tasks', or 'mark buy milk as done' — or just ask me "
    "anything else and I'll answer directly."
)


def generate_reply(text: str) -> str:
    """Pure function: message text -> reply text. No I/O, easy to test."""
    stripped = text.strip()
    if stripped in ("/start", "/help"):
        return HELP_TEXT

    intent = nlu.parse_message(stripped)
    if intent.action != nlu.UNKNOWN:
        return wa_handler.build_reply(intent)
    return qa.answer_question(stripped)


def handle_incoming_message(user_id, chat_id, text: str) -> None:
    """Full pipeline for one inbound Telegram text message. Never raises —
    a bad message should never crash the webhook."""
    if not security.is_authorized(user_id):
        logger.warning("Ignored message from unauthorized Telegram user id: %s", user_id)
        return  # Silently ignore — do not reveal the assistant exists or reply.

    if not text or not text.strip():
        return

    try:
        reply = generate_reply(text)
    except Exception as e:
        logger.exception("Failed to process message %r from %s", text, user_id)
        reply = f"Sorry sir, something went wrong handling that: {e}"

    telegram_client.send_text_message(chat_id, reply)
