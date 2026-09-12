"""
whatsapp_service/whatsapp_client.py — thin wrapper around the official
WhatsApp Business Cloud API (Graph API). No unofficial libraries, no
WhatsApp Web automation, per the project's requirements.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
"""
from __future__ import annotations

import logging

import requests

from whatsapp_service import config

logger = logging.getLogger("whatsapp_service.client")

_TIMEOUT_SECS = 15


def send_text_message(to: str, body: str) -> bool:
    """Send a plain-text WhatsApp message to `to` (digits-only wa_id).
    Returns True on success, False on failure (never raises — a failed
    confirmation message should not crash the webhook)."""
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(
            "Cannot send WhatsApp message: WHATSAPP_ACCESS_TOKEN or "
            "WHATSAPP_PHONE_NUMBER_ID is not set."
        )
        return False

    url = (
        f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/"
        f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT_SECS)
        if resp.status_code >= 400:
            logger.error("WhatsApp send failed (%s): %s", resp.status_code, resp.text[:500])
            return False
        return True
    except requests.RequestException as e:
        logger.error("WhatsApp send raised an exception: %s", e)
        return False
