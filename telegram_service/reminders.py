"""
Cloud-backed Telegram reminder settings and task reminders.

The desktop JARVIS can be offline: this module reads the shared Firestore
To-Do store and sends reminders directly through the Telegram Bot API.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from plugins import _todo_storage
from telegram_service import telegram_client

SETTINGS_DOCUMENT = os.environ.get("TELEGRAM_REMINDER_DOCUMENT", "telegram_reminders")
COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "jarvis")
TIMEZONE = os.environ.get("JARVIS_TIMEZONE", "Asia/Kolkata")


def _firestore_doc():
    # Reuse the same Firebase initialization used by the shared todo backend.
    return _todo_storage._firestore_doc().collection(COLLECTION).document(SETTINGS_DOCUMENT)


def _default_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_hours": 1,
        "last_sent_at": None,
    }


def get_settings() -> dict[str, Any]:
    try:
        snap = _firestore_doc().get()
        data = snap.to_dict() if snap.exists else {}
    except Exception:
        data = {}
    settings = _default_settings()
    settings.update(data or {})
    try:
        settings["interval_hours"] = max(1, min(24, int(settings["interval_hours"])) )
    except (TypeError, ValueError):
        settings["interval_hours"] = 1
    settings["enabled"] = bool(settings.get("enabled", True))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    _firestore_doc().set(settings, merge=True)


def configure(enabled: bool | None = None, interval_hours: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    if enabled is not None:
        settings["enabled"] = bool(enabled)
    if interval_hours is not None:
        settings["interval_hours"] = max(1, min(24, int(interval_hours)))
    save_settings(settings)
    return settings


def _now() -> datetime:
    try:
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now().astimezone()


def _parse_due(value: Any):
    if not value:
        return None
    text = str(value).strip()
    # Study Manager uses ISO dates. Accept a few friendly forms for older tasks.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _pending_tasks() -> list[dict[str, Any]]:
    data = _todo_storage.load()
    return [t for t in data.get("tasks", []) if not t.get("completed")]


def _task_priority(task: dict[str, Any], today) -> tuple[int, str]:
    due = _parse_due(task.get("due"))
    if due is None:
        return (2, str(task.get("created_at", "")))
    if due < today:
        return (0, str(task.get("due")))
    if due == today:
        return (1, str(task.get("due")))
    return (3, str(task.get("due")))


def _format_message(tasks: list[dict[str, Any]], now: datetime) -> str:
    today = now.date()
    overdue = []
    today_tasks = []
    upcoming = []
    for task in sorted(tasks, key=lambda t: _task_priority(t, today)):
        due = _parse_due(task.get("due"))
        line = str(task.get("description", "Task"))
        if due and due < today:
            overdue.append(line)
        elif due == today:
            today_tasks.append(line)
        else:
            upcoming.append(line)

    lines = [f"⏰ JARVIS reminder — {now.strftime('%I:%M %p')}"]
    if overdue:
        lines.append(f"⚠️ Overdue: {len(overdue)}")
        lines.extend(f"• {x}" for x in overdue[:4])
    if today_tasks:
        lines.append(f"📚 Today's pending tasks: {len(today_tasks)}")
        lines.extend(f"• {x}" for x in today_tasks[:6])
    if upcoming and not today_tasks and not overdue:
        lines.append(f"📌 Pending tasks: {len(upcoming)}")
        lines.extend(f"• {x}" for x in upcoming[:6])
    total = len(tasks)
    shown = min(total, 4 + 6)
    if total > shown:
        lines.append(f"…and {total - shown} more pending task(s).")
    lines.append("Send /tasks in Telegram for the full list.")
    return "\n".join(lines)


def send_due_reminder(force: bool = False) -> dict[str, Any]:
    """Send one reminder if enabled and the configured interval has elapsed."""
    settings = get_settings()
    now = _now()
    if not settings["enabled"] and not force:
        return {"sent": False, "reason": "disabled"}

    last_raw = settings.get("last_sent_at")
    if last_raw and not force:
        try:
            last = datetime.fromisoformat(str(last_raw))
            if last.tzinfo is None:
                last = last.replace(tzinfo=now.tzinfo)
            if now - last < timedelta(hours=settings["interval_hours"]):
                return {"sent": False, "reason": "interval"}
        except ValueError:
            pass

    tasks = _pending_tasks()
    if not tasks:
        return {"sent": False, "reason": "no_pending_tasks"}

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or os.environ.get(
        "AUTHORIZED_TELEGRAM_USER_ID", ""
    ).strip()
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID or AUTHORIZED_TELEGRAM_USER_ID is required.")

    telegram_client.send_text_message(chat_id, _format_message(tasks, now))
    settings["last_sent_at"] = now.isoformat(timespec="seconds")
    save_settings(settings)
    return {"sent": True, "pending": len(tasks), "at": settings["last_sent_at"]}
