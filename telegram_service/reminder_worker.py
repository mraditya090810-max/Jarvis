"""One-shot cloud reminder worker for Telegram.

Run this from GitHub Actions, a cron job, or any always-online scheduler.
It intentionally performs one check and exits so it never needs a running PC.
"""
from __future__ import annotations

import logging

from telegram_service.reminders import send_due_reminder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_service.reminder_worker")


def main() -> int:
    try:
        result = send_due_reminder()
        logger.info("Reminder check: %s", result)
        return 0
    except Exception:
        logger.exception("Reminder worker failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
