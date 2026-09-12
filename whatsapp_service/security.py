"""
whatsapp_service/security.py — the entire authorization model for this
feature: exactly one phone number (AUTHORIZED_WHATSAPP_NUMBER) may read or
modify the To-Do List. Everyone else is ignored.
"""
from __future__ import annotations

from whatsapp_service import config


def is_authorized(sender_number: str) -> bool:
    """sender_number is the 'from' field WhatsApp sends, e.g. '15551234567'
    (already digits-only in practice, but we normalize defensively)."""
    if not config.AUTHORIZED_WHATSAPP_NUMBER:
        # Fail closed: an empty allowlist means nobody is authorized rather
        # than everybody.
        return False
    return config.clean_number(sender_number) == config.AUTHORIZED_WHATSAPP_NUMBER
