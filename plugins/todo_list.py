"""
plugins/todo_list.py — JARVIS persistent To-Do List plugin.

Shared by desktop JARVIS, WhatsApp, Telegram, and other interfaces.
Storage is a JSON file under memory/ unless TODO_STORAGE_PATH is set.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _storage_path() -> Path:
    override = os.environ.get("TODO_STORAGE_PATH")
    if override:
        return Path(override).expanduser()
    return _project_root() / "memory" / "todo_tasks.json"


def _load() -> dict:
    path = _storage_path()

    if not path.exists():
        return {"next_id": 1, "tasks": []}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            return {"next_id": 1, "tasks": []}

        data.setdefault("next_id", 1)
        data.setdefault("tasks", [])

        if not isinstance(data["tasks"], list):
            data["tasks"] = []

        return data

    except Exception:
        return {"next_id": 1, "tasks": []}


def _save(data: dict) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


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
    """
    Find tasks by description.

    Numbered references are intentionally handled separately by
    _resolve_task_query().
    """
    q = query.strip().lower()

    if not q:
        return []

    matches = []

    for task in tasks:
        desc = task["description"].lower()

        if q in desc or desc in q:
            matches.append(task)

    if matches:
        return matches

    # Keyword overlap fallback.
    q_words = set(re.findall(r"[a-z0-9]+", q))

    for task in tasks:
        d_words = set(
            re.findall(r"[a-z0-9]+", task["description"].lower())
        )

        if q_words and q_words & d_words:
            matches.append(task)

    return matches


def _best_match(tasks: list[dict], query: str) -> Optional[dict]:
    matches = _find_matches(tasks, query)

    if not matches:
        return None

    return sorted(
        matches,
        key=lambda task: len(task["description"]),
    )[0]


def _resolve_task_query(
    tasks: list[dict],
    query: str,
) -> Optional[dict]:
    """
    Resolve either:

      "physics revision"
      "1"
      "task 1"
      "#1"
      "number 1"

    Numbering follows the order shown by list_tasks(), NOT the internal
    database ID. This means "complete task 1" always means the first
    currently pending task when completing a task.
    """
    q = query.strip().lower()

    if not q:
        return None

    # Accept:
    # 1
    # #1
    # task 1
    # task #1
    # number 1
    # number #1
    number_match = re.fullmatch(
        r"(?:task\s*)?(?:number\s*)?#?\s*(\d+)",
        q,
    )

    if number_match:
        index = int(number_match.group(1))

        if index < 1 or index > len(tasks):
            return None

        return tasks[index - 1]

    return _best_match(tasks, q)


# --------------------------------------------------------------------------
# Core API
# --------------------------------------------------------------------------

def add_task(
    description: str,
    due: Optional[str] = None,
) -> dict:
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


def list_tasks(
    include_completed: bool = False,
) -> list[dict]:
    data = _load()
    tasks = data["tasks"]

    if include_completed:
        return list(tasks)

    return [
        task
        for task in tasks
        if not task["completed"]
    ]


def format_task_list(tasks: list[dict]) -> str:
    if not tasks:
        return "You have no tasks on your list."

    lines = [
        _format_task_line(task, index)
        for index, task in enumerate(tasks, start=1)
    ]

    return "\n".join(lines)


def complete_task(
    query: str,
) -> tuple[bool, Optional[dict]]:
    with _LOCK:
        data = _load()

        # IMPORTANT:
        # The numbering seen by the user is based on pending tasks.
        pending = [
            task
            for task in data["tasks"]
            if not task["completed"]
        ]

        match = _resolve_task_query(pending, query)

        if not match:
            return False, None

        for task in data["tasks"]:
            if task["id"] == match["id"]:
                task["completed"] = True
                task["completed_at"] = datetime.now().isoformat(
                    timespec="seconds"
                )

                _save(data)

                return True, task

        return False, None


def delete_task(
    query: str,
) -> tuple[bool, Optional[dict]]:
    with _LOCK:
        data = _load()

        match = _resolve_task_query(data["tasks"], query)

        if not match:
            return False, None

        data["tasks"] = [
            task
            for task in data["tasks"]
            if task["id"] != match["id"]
        ]

        _save(data)

        return True, match


def clear_tasks(
    only_completed: bool = True,
) -> int:
    with _LOCK:
        data = _load()

        before = len(data["tasks"])

        if only_completed:
            data["tasks"] = [
                task
                for task in data["tasks"]
                if not task["completed"]
            ]
        else:
            data["tasks"] = []

        _save(data)

        return before - len(data["tasks"])


def task_status(
    query: str,
) -> Optional[dict]:
    data = _load()

    return _resolve_task_query(
        data["tasks"],
        query,
    )


# --------------------------------------------------------------------------
# JARVIS plugin contract
# --------------------------------------------------------------------------

PLUGIN = {
    "name": "todo_list",
    "description": (
        "Manage the user's persistent To-Do List: add a task, list pending "
        "or all tasks, mark a task complete, delete a task, check a task's "
        "status, or clear tasks. Understand numbered references such as "
        "'complete task 1', 'mark 2 as done', and 'I completed 3'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: ADD_TASK, LIST_TASKS, COMPLETE_TASK, "
                    "DELETE_TASK, CLEAR_TASKS, TASK_STATUS."
                ),
            },
            "description": {
                "type": "STRING",
                "description": "Task text, required for ADD_TASK.",
            },
            "due_date": {
                "type": "STRING",
                "description": (
                    "Optional due date/phrase, e.g. 'tomorrow', 'Friday'."
                ),
            },
            "task_query": {
                "type": "STRING",
                "description": (
                    "Words or a numbered reference identifying an existing "
                    "task, e.g. 'physics', '1', or 'task 2'."
                ),
            },
            "show_completed": {
                "type": "BOOLEAN",
                "description": (
                    "For LIST_TASKS: include completed tasks too."
                ),
            },
        },
        "required": ["action"],
    },
}


def run(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    action = (
        parameters.get("action") or ""
    ).strip().upper()

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

            reply = (
                f"Added to your To-Do List: "
                f"{task['description']}"
            )

            if task["due"]:
                reply += f" — due {_display_due(task['due'])}."

            else:
                reply += "."

            _log(reply)
            return reply

        if action == "LIST_TASKS":
            include_completed = bool(
                parameters.get("show_completed", False)
            )

            tasks = list_tasks(
                include_completed=include_completed
            )

            if not tasks:
                return (
                    "You have no tasks yet."
                    if include_completed
                    else "You have no pending tasks."
                )

            heading = (
                "Your tasks:"
                if include_completed
                else "Your pending tasks:"
            )

            reply = (
                f"{heading}\n\n"
                f"{format_task_list(tasks)}"
            )

            _log(reply)
            return reply

        if action == "COMPLETE_TASK":
            query = parameters.get("task_query", "")

            if not query.strip():
                return "Which task should I mark as completed?"

            ok, task = complete_task(query)

            if not ok:
                return (
                    "I couldn't find that task. "
                    "Say 'show my tasks' to see your current list."
                )

            reply = (
                f"Done. I've marked "
                f"'{task['description']}' as completed."
            )

            _log(reply)
            return reply

        if action == "DELETE_TASK":
            query = parameters.get("task_query", "")

            if not query.strip():
                return "Which task should I delete?"

            ok, task = delete_task(query)

            if not ok:
                return (
                    "I couldn't find that task. "
                    "Say 'show my tasks' to see your current list."
                )

            reply = (
                f"Deleted the "
                f"'{task['description']}' task."
            )

            _log(reply)
            return reply

        if action == "CLEAR_TASKS":
            only_completed = bool(
                parameters.get("show_completed", True)
            )

            count = clear_tasks(
                only_completed=only_completed
            )

            scope = "completed " if only_completed else ""

            reply = (
                f"Cleared {count} {scope}"
                f"task{'s' if count != 1 else ''}."
            )

            _log(reply)
            return reply

        if action == "TASK_STATUS":
            query = parameters.get("task_query", "")

            if not query.strip():
                return "Which task do you want the status of?"

            task = task_status(query)

            if not task:
                return (
                    "I couldn't find that task. "
                    "Say 'show my tasks' to see your current list."
                )

            state = (
                "completed"
                if task["completed"]
                else "still pending"
            )

            reply = (
                f"'{task['description']}' is {state}."
            )

            _log(reply)
            return reply

        return (
            "I can manage your To-Do List. You can say things like "
            "'add study chemistry tomorrow', 'show my tasks', or "
            "'mark my assignment as done'."
        )

    except Exception as e:
        return f"Sir, the to-do list plugin failed: {e}"
