"""
whatsapp_service/local_test.py — exercise the full incoming-message
pipeline (security allowlist -> NLU -> plugins/todo_list.py -> the
configured storage backend, file or Firestore -> generated reply) without
needing a real WhatsApp message to arrive via Meta at all. This sidesteps
Meta's unpublished-app delivery restriction entirely, since it calls
handler.handle_incoming_message() directly instead of going through
Meta -> Render -> webhook.py.

Usage (PowerShell), from the project root, with your normal env vars set
(WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN,
AUTHORIZED_WHATSAPP_NUMBER, and — if you're testing the shared-storage
setup — TODO_STORAGE_BACKEND=firestore / FIREBASE_CREDENTIALS_JSON or
FIREBASE_CREDENTIALS_PATH):

    python -m whatsapp_service.local_test "add buy milk"
    python -m whatsapp_service.local_test "show my tasks"
    python -m whatsapp_service.local_test "complete buy milk"

    # No arguments -> interactive loop, one message per line, Ctrl+C to quit:
    python -m whatsapp_service.local_test

By default this does NOT call the real WhatsApp Graph API — it swaps out
whatsapp_client.send_text_message for the duration of one message so the
reply that *would* have been sent is printed to your terminal instead.
Everything else runs exactly as it does in production: the sender is
checked against AUTHORIZED_WHATSAPP_NUMBER via security.is_authorized
(so this also proves your real-number security check still works), the
text goes through nlu.parse_message, and todo_list.py reads/writes
whichever storage backend your environment is currently configured for.

Pass --send to skip the patch and actually deliver the reply over
WhatsApp to AUTHORIZED_WHATSAPP_NUMBER instead of just printing it —
useful once you've added your own number to the Meta app's allowed test
recipients (Test mode allows up to 5).
"""
from __future__ import annotations

import sys

from whatsapp_service import config, handler, whatsapp_client


def _run_one(text: str, send: bool) -> None:
    sender = config.AUTHORIZED_WHATSAPP_NUMBER
    if not sender:
        print("AUTHORIZED_WHATSAPP_NUMBER is not set — set it before running this.")
        return

    if send:
        handler.handle_incoming_message(sender, text)
        print("(sent via the real WhatsApp Graph API — check your phone)")
        return

    captured: dict[str, str] = {}

    def _fake_send(to: str, body: str) -> bool:
        captured["to"] = to
        captured["body"] = body
        return True

    original_send = whatsapp_client.send_text_message
    whatsapp_client.send_text_message = _fake_send  # type: ignore[assignment]
    try:
        handler.handle_incoming_message(sender, text)
    finally:
        whatsapp_client.send_text_message = original_send  # type: ignore[assignment]

    if captured:
        print(f"[reply that would be sent to {captured['to']}]\n{captured['body']}")
    else:
        print(
            "[no reply generated — the sender was rejected by "
            "security.is_authorized(), or an exception was logged above]"
        )


def main() -> None:
    args = sys.argv[1:]
    send = "--send" in args
    args = [a for a in args if a != "--send"]

    if args:
        _run_one(" ".join(args), send)
        return

    print("Interactive mode — type a message and press Enter (Ctrl+C to quit).")
    try:
        while True:
            text = input("> ").strip()
            if text:
                _run_one(text, send)
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
