from __future__ import annotations

from datetime import datetime


def test_format_message_includes_pending_tasks(monkeypatch):
    from telegram_service import reminders
    tasks = [
        {"description": "Maths Integration", "due": "2026-09-14", "completed": False},
        {"description": "Old revision", "due": "2026-09-13", "completed": False},
    ]
    text = reminders._format_message(tasks, datetime.fromisoformat("2026-09-14T10:00:00"))
    assert "Maths Integration" in text
    assert "Old revision" in text
    assert "Today's pending tasks" in text


def test_due_parser():
    from telegram_service import reminders
    assert reminders._parse_due("2026-09-14").isoformat() == "2026-09-14"
    assert reminders._parse_due("14-09-2026").isoformat() == "2026-09-14"
