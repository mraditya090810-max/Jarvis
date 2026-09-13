"""
telegram_service/webhook.py — the FastAPI app Telegram POSTs updates to.

Standalone, same as whatsapp_service/webhook.py and twilio_service/webhook.py:
not wired into main.py / ui.py, so it keeps working on a free cloud server
even when the desktop JARVIS app (and PC) are off.

Run locally:
    uvicorn telegram_service.webhook:app --reload --port 8082

See telegram_service/README.md for the full setup/deploy walkthrough.
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response

from telegram_service import config, handler, security

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_service.webhook")

app = FastAPI(title="JARVIS Telegram Bot")


@app.post("/webhook")
async def receive_update(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Telegram calls this for every update. We always return 200 quickly
    (Telegram retries on non-2xx), and do the actual work — including
    sending the reply — in a background task."""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not security.validate_secret_token(secret):
        logger.warning("Rejected webhook request with invalid/missing secret token.")
        return Response(status_code=403)

    payload = await request.json()

    try:
        message = payload.get("message") or payload.get("edited_message")
        if message and "text" in message:
            user_id = message.get("from", {}).get("id")
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "")
            if user_id is not None and chat_id is not None:
                background_tasks.add_task(handler.handle_incoming_message, user_id, chat_id, text)
    except Exception:
        logger.exception("Failed to parse incoming Telegram update: %r", payload)

    return Response(status_code=200)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "configured": config.is_fully_configured(),
        "missing_env_vars": config.missing_vars(),
    }
