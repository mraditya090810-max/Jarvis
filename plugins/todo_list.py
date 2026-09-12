"""
plugins/todo_list.py — JARVIS To-Do List plugin.

This is a normal drop-in JARVIS plugin (same PLUGIN + run() contract as every
other file in plugins/ — see plugins/_template.py), so the existing desktop
JARVIS (voice/text via main.py + core/plugin_loader.py) can manage the to-do
list exactly like any other tool ("add finish my homework", "what are my
tasks", ...).

On top of that, every real operation lives in plain module-level functions
(add_task, list_tasks, complete_task, delete_task, clear_tasks, task_status)
with NO dependency on main.py, PyQt6, google-genai, or anything else desktop
JARVIS-specific. That's what lets whatsapp_service/handler.py import this
exact same module directly and reuse it as the single source of truth for
tasks, instead of building a second independent task system.

Storage: pluggable — a local JSON file by default, or a shared Firebase
Firestore document when TODO_STORAGE_BACKEND=firestore is set (so a
desktop JARVIS and a separately-hosted whatsapp_service see the exact
same tasks with no extra syncing code). See plugins/_todo_storage.py for
the full backend selection rules.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Optional

from plugins import _todo_storage as _storage

_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _load() -> dict:
    return _storage.load()


def _save(data: dict) -> None:
    _storage.save(data)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _clean_description(text: str) -> str:
    text = text.strip().strip(".").strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def _display_due(due: Optional[str]) -> str:
    return due.strip().capitalize() if due else ""


def _format_task_line(task: dict, index: int) -> str:
    line = f"{index}. {task['description']}"
    if task.get("due"):
        line += f" — {_display_due(task['due'])}"
    if task.get("completed"):
        line += " (completed)"
    return line


def _find_matches(tasks: list[dict], query: str) -> list[dict]:
    """Case-insensitive substring match against task descriptions, both ways
    (query-in-description or description-in-query), so 'physics assignment'
    matches 'Finish my physics assignment' and vice versa."""
    q = query.strip().lower()
    if not q:
        return []
    matches = []
    for t in tasks:
        desc = t["description"].lower()
        if q in desc or desc in q:
            matches.append(t)
    if matches:
        return matches
    # Fall back to keyword overlap (e.g. query "chemistry" vs description
    # "Study chemistry for the test").
    q_words = set(re.findall(r"[a-z0-9]+", q))
    for t in tasks:
        d_words = set(re.findall(r"[a-z0-9]+", t["description"].lower()))
        if q_words and q_words & d_words:
            matches.append(t)
    return matches


def _best_match(tasks: list[dict], query: str) -> Optional[dict]:
    matches = _find_matches(tasks, query)
    if not matches:
        return None
    # Prefer the shortest description (tightest match) among matches.
    return sorted(matches, key=lambda t: len(t["description"]))[0]


# --------------------------------------------------------------------------
# Core API — used by both the plugin run() below and whatsapp_service/
# --------------------------------------------------------------------------

def add_task(description: str, due: Optional[str] = None) -> dict:
    description = _clean_description(description)
    if not description:
        raise ValueError("Task description cannot be empty.")
    with _LOCK:
        data = _load()
        task = {
            "id": data["next_id"],
            "description": description,
            "due": due.strip() if due else None,
            "completed": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
        }
        data["tasks"].append(task)
        data["next_id"] += 1
        _save(data)
        return task


def list_tasks(include_completed: bool = False) -> list[dict]:
    data = _load()
    tasks = data["tasks"]
    if include_completed:
        return list(tasks)
    return [t for t in tasks if not t["completed"]]


def format_task_list(tasks: list[dict]) -> str:
    if not tasks:
        return "You have no tasks on your list."
    lines = [_format_task_line(t, i + 1) for i, t in enumerate(tasks)]
    return "\n".join(lines)


def complete_task(query: str) -> tuple[bool, Optional[dict]]:
    with _LOCK:
        data = _load()
        match = _best_match([t for t in data["tasks"] if not t["completed"]], query)
        if not match:
            return False, None
        for t in data["tasks"]:
            if t["id"] == match["id"]:
                t["completed"] = True
                t["completed_at"] = datetime.now().isoformat(timespec="seconds")
                _save(data)
                return True, t
        return False, None


def delete_task(query: str) -> tuple[bool, Optional[dict]]:
    with _LOCK:
        data = _load()
        match = _best_match(data["tasks"], query)
        if not match:
            return False, None
        data["tasks"] = [t for t in data["tasks"] if t["id"] != match["id"]]
        _save(data)
        return True, match


def clear_tasks(only_completed: bool = True) -> int:
    with _LOCK:
        data = _load()
        before = len(data["tasks"])
        if only_completed:
            data["tasks"] = [t for t in data["tasks"] if not t["completed"]]
        else:
            data["tasks"] = []
        _save(data)
        return before - len(data["tasks"])


def task_status(query: str) -> Optional[dict]:
    data = _load()
    return _best_match(data["tasks"], query)


# --------------------------------------------------------------------------
# JARVIS plugin contract (desktop voice/text assistant)
# --------------------------------------------------------------------------

PLUGIN = {
    "name": "todo_list",
    "description": (
        "Manage the user's persistent To-Do List: add a task, list pending or "
        "all tasks, mark a task complete, delete a task, check a task's "
        "status, or clear tasks. Trigger on phrases like 'add a task', "
        "'remind me to...', 'what are my tasks', 'show my pending tasks', "
        "'mark ... as done', 'delete the ... task', or 'clear my completed "
        "tasks'. Use this instead of the 'reminder' tool when the user is "
        "managing a to-do list rather than asking for a one-off OS notification."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: ADD_TASK, LIST_TASKS, COMPLETE_TASK, DELETE_TASK, "
                    "CLEAR_TASKS, TASK_STATUS."
                ),
            },
            "description": {
                "type": "STRING",
                "description": "Task text, required for ADD_TASK.",
            },
            "due_date": {
                "type": "STRING",
                "description": "Optional due date/phrase, e.g. 'tomorrow', 'Friday'.",
            },
            "task_query": {
                "type": "STRING",
                "description": (
                    "Words identifying an existing task, for COMPLETE_TASK, "
                    "DELETE_TASK, or TASK_STATUS."
                ),
            },
            "show_completed": {
                "type": "BOOLEAN",
                "description": "For LIST_TASKS: include completed tasks too.",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().upper()

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(f"JARVIS: {msg}")
            except Exception:
                pass

    try:
        if action == "ADD_TASK":
            description = parameters.get("description", "")
            due = parameters.get("due_date")
            if not description.strip():
                return "What would you like me to add to your To-Do List?"
            task = add_task(description, due)
            reply = f"Added to your To-Do List: {task['description']}"
            reply += f" — due {_display_due(task['due'])}." if task["due"] else "."
            _log(reply)
            return reply

        if action == "LIST_TASKS":
            include_completed = bool(parameters.get("show_completed", False))
            tasks = list_tasks(include_completed=include_completed)
            heading = "Your tasks:" if include_completed else "Your pending tasks:"
            body = format_task_list(tasks)
            reply = body if not tasks else f"{heading}\n\n{body}"
            _log(reply)
            return reply

        if action == "COMPLETE_TASK":
            query = parameters.get("task_query", "")
            if not query.strip():
                return "Which task should I mark as completed?"
            ok, task = complete_task(query)
            if not ok:
                return "I couldn't find that task. Say 'show my tasks' to see your current list."
            reply = f"Done. I've marked '{task['description']}' as completed."
            _log(reply)
            return reply

        if action == "DELETE_TASK":
            query = parameters.get("task_query", "")
            if not query.strip():
                return "Which task should I delete?"
            ok, task = delete_task(query)
            if not ok:
                return "I couldn't find that task. Say 'show my tasks' to see your current list."
            reply = f"Deleted the '{task['description']}' task."
            _log(reply)
            return reply

        if action == "CLEAR_TASKS":
            only_completed = bool(parameters.get("show_completed", True))
            count = clear_tasks(only_completed=only_completed)
            scope = "completed " if only_completed else ""
            reply = f"Cleared {count} {scope}task{'s' if count != 1 else ''}."
            _log(reply)
            return reply

        if action == "TASK_STATUS":
            query = parameters.get("task_query", "")
            task = task_status(query)
            if not task:
                return "I couldn't find that task. Say 'show my tasks' to see your current list."
            state = "completed" if task["completed"] else "still pending"
            reply = f"'{task['description']}' is {state}."
            _log(reply)
            return reply

        return (
            "I can manage your To-Do List. You can say things like "
            "'add study chemistry tomorrow', 'show my tasks', or "
            "'mark my assignment as done'."
        )
    except Exception as e:
        return f"Sir, the to-do list plugin failed: {e}"
