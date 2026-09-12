"""
whatsapp_service/webhook.py — the "JARVIS WhatsApp Webhook" box from the
architecture diagram.

This is a standalone FastAPI app. It is deliberately NOT wired into
main.py / ui.py: the whole point of this feature is that it keeps working
on a cloud server even when the desktop JARVIS app (and the Windows PC it
runs on) is turned off. It reuses plugins/todo_list.py as its data layer
(via whatsapp_service/handler.py) but does not import main.py, PyQt6,
google-genai, or anything else from the desktop app.

Run locally:
    uvicorn whatsapp_service.webhook:app --reload --port 8080

See whatsapp_service/README.md for the full setup/deploy walkthrough.
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response

from whatsapp_service import config, handler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_service.webhook")

app = FastAPI(title="JARVIS WhatsApp To-Do Webhook")


@app.get("/webhook")
def verify_webhook(request: Request) -> Response:
    """Meta calls this once, when you click 'Verify and Save' in the App
    Dashboard, to prove you control this URL."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verification succeeded.")
        return Response(content=challenge, media_type="text/plain", status_code=200)

    logger.warning("Webhook verification failed (mode=%s).", mode)
    return Response(status_code=403)


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Receives all WhatsApp events (messages, statuses, etc). We always
    return 200 quickly, per Meta's requirements, and do the actual work
    (including sending the reply) in a background task."""
    payload = await request.json()

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    _dispatch_message(message, background_tasks)
    except Exception:
        logger.exception("Failed to parse incoming WhatsApp payload: %r", payload)

    return Response(status_code=200)


def _dispatch_message(message: dict, background_tasks: BackgroundTasks) -> None:
    sender = message.get("from", "")
    msg_type = message.get("type")

    if msg_type != "text":
        logger.info("Ignoring non-text message of type '%s' from %s", msg_type, sender)
        return

    text = message.get("text", {}).get("body", "")
    if not sender or not text:
        return

    background_tasks.add_task(handler.handle_incoming_message, sender, text)


@app.get("/health")
def health() -> dict:
    """Simple uptime check for your hosting provider / a curl test."""
    return {
        "status": "ok",
        "configured": config.is_fully_configured(),
        "missing_env_vars": config.missing_vars(),
    }
