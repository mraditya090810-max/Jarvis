"""
whatsapp_service/handler.py — the "JARVIS To-Do Handler" box from the
architecture diagram. Given an incoming WhatsApp text message, this module:
  1. checks the sender against the allowlist (security.py),
  2. classifies intent + extracts arguments (nlu.py),
  3. calls the existing plugins/todo_list.py functions,
  4. sends a confirmation/result message back over WhatsApp (whatsapp_client.py).

Kept intentionally free of any FastAPI/webhook-specific code so it can be
unit-tested by calling handle_incoming_message(sender, text) directly.
"""
from __future__ import annotations

import logging

from plugins import todo_list
from whatsapp_service import nlu, security, whatsapp_client

logger = logging.getLogger("whatsapp_service.handler")

UNKNOWN_MESSAGE = (
    "I can manage your To-Do List. You can say things like "
    "'add study chemistry tomorrow', 'show my tasks', or "
    "'mark my assignment as done'."
)
NOT_FOUND_MESSAGE = "I couldn't find that task. Say 'show my tasks' to see your current list."


def _display_due(due: str | None) -> str:
    return due.strip().capitalize() if due else ""


def build_reply(intent: nlu.Intent) -> str:
    """Pure function: intent -> reply text. Separated from I/O so it's easy
    to unit test without hitting the network."""
    action = intent.action

    if action == nlu.ADD_TASK:
        if not intent.description or not intent.description.strip():
            return "What would you like me to add to your To-Do List?"
        task = todo_list.add_task(intent.description, intent.due_date)
        reply = f"Added to your To-Do List: {task['description']}"
        reply += f" — due {_display_due(task['due'])}." if task["due"] else "."
        return reply

    if action == nlu.LIST_TASKS:
        tasks = todo_list.list_tasks(include_completed=intent.show_completed)
        if not tasks:
            return "You have no pending tasks." if not intent.show_completed else "You have no tasks yet."
        heading = "Your tasks:" if intent.show_completed else "Your pending tasks:"
        return f"{heading}\n\n{todo_list.format_task_list(tasks)}"

    if action == nlu.COMPLETE_TASK:
        if not intent.task_query:
            return "Which task should I mark as completed?"
        ok, task = todo_list.complete_task(intent.task_query)
        if not ok:
            return NOT_FOUND_MESSAGE
        return f"Done. I've marked '{task['description']}' as completed."

    if action == nlu.DELETE_TASK:
        if not intent.task_query:
            return "Which task should I delete?"
        ok, task = todo_list.delete_task(intent.task_query)
        if not ok:
            return NOT_FOUND_MESSAGE
        return f"Deleted the '{task['description']}' task."

    if action == nlu.CLEAR_TASKS:
        only_completed = intent.show_completed
        count = todo_list.clear_tasks(only_completed=only_completed)
        scope = "completed " if only_completed else ""
        return f"Cleared {count} {scope}task{'s' if count != 1 else ''}."

    if action == nlu.TASK_STATUS:
        if not intent.task_query:
            return "Which task do you want the status of?"
        task = todo_list.task_status(intent.task_query)
        if not task:
            return NOT_FOUND_MESSAGE
        state = "completed" if task["completed"] else "still pending"
        return f"'{task['description']}' is {state}."

    return UNKNOWN_MESSAGE


def handle_incoming_message(sender_number: str, text: str) -> None:
    """Full pipeline for one inbound WhatsApp text message. Never raises —
    a bad message should never crash the webhook."""
    if not security.is_authorized(sender_number):
        logger.warning("Ignored message from unauthorized number: %s", sender_number)
        return  # Silently ignore — do not reveal the assistant exists or reply.

    try:
        intent = nlu.parse_message(text)
        reply = build_reply(intent)
    except Exception as e:
        logger.exception("Failed to process message %r from %s", text, sender_number)
        reply = f"Sorry, something went wrong handling that: {e}"

    whatsapp_client.send_text_message(sender_number, reply)
