"""
telegram_service/qa.py — answers a general (non-to-do) message using the
project's existing free-first AI router (core/ai), the same one
actions/code_helper.py and twilio_service/qa.py already call. Kept as its
own small copy (rather than importing twilio_service.qa) so telegram_service
has no dependency on twilio_service at all — either can be deleted without
touching the other.
"""
from __future__ import annotations

import logging

from core.ai import NoFreeModelAvailable, call_llm_text
from telegram_service import config

logger = logging.getLogger("telegram_service.qa")

SYSTEM_PROMPT = (
    "You are JARVIS, a professional, efficient, slightly witty assistant, "
    "chatting with your user over Telegram. Address them as 'sir'. Keep "
    "answers reasonably concise and in plain text — no markdown formatting, "
    "since it may not render. Match response length to the question: a "
    "quick fact gets a short reply, a genuinely complex question can run "
    "longer."
)

FAILURE_REPLY = (
    "Sorry sir, I couldn't reach my reasoning engine just now. Please try that again shortly."
)


def answer_question(question: str) -> str:
    """Never raises — a bad AI response should never crash the webhook."""
    try:
        return call_llm_text(
            question,
            system=SYSTEM_PROMPT,
            timeout=config.AI_ANSWER_TIMEOUT_SECS,
            task="telegram_qa",
        ).strip()
    except NoFreeModelAvailable:
        logger.warning("No free model available to answer question: %r", question)
        return FAILURE_REPLY
    except Exception:
        logger.exception("Unexpected error answering question: %r", question)
        return FAILURE_REPLY
