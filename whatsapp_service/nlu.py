"""
whatsapp_service/nlu.py — turns a free-text WhatsApp message into one of the
six To-Do intents plus extracted arguments, without requiring slash-commands.

This is a deliberately simple, dependency-free, deterministic rule-based
router (regex + keyword matching) rather than an LLM call:
  - it works even if the cloud server this webhook runs on has no LLM/API
    key configured at all,
  - it's instant and free per message,
  - it's easy for a beginner to read, extend, and debug.

If you later want smarter understanding, core/llm_client.py (already in this
project, used by dev_agent/code_helper) can be swapped in here — see the
"Optional: smarter NLU" note in whatsapp_service/README.md. Nothing else
would need to change, since everything downstream just consumes the
Intent object this module returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ADD_TASK = "ADD_TASK"
LIST_TASKS = "LIST_TASKS"
COMPLETE_TASK = "COMPLETE_TASK"
DELETE_TASK = "DELETE_TASK"
CLEAR_TASKS = "CLEAR_TASKS"
TASK_STATUS = "TASK_STATUS"
UNKNOWN = "UNKNOWN"


@dataclass
class Intent:
    action: str
    description: Optional[str] = None
    due_date: Optional[str] = None
    task_query: Optional[str] = None
    show_completed: bool = False


_DUE_PATTERN = re.compile(
    r"\b(?:by|on|due|for)?\s*"
    r"(today|tonight|tomorrow|tmrw|this\s+weekend|next\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_ADD_PREFIXES = [
    r"^i\s+(?:have|need|got|gotta)\s+to\s+",
    r"^i'?ve\s+got\s+to\s+",
    r"^remind\s+me\s+to\s+",
    r"^don'?t\s+forget\s+to\s+",
    r"^please\s+add\s+",
    r"^add\s+(?:a\s+task\s+to\s+|task\s+to\s+|to\s+)?",
    r"^note\s+to\s+self[:,]?\s+",
    r"^i\s+must\s+",
    r"^i\s+should\s+",
]

_COMPLETE_PREFIXES = [
    r"^mark\s+(?:the\s+|my\s+)?",
    r"^complete\s+(?:the\s+|my\s+)?",
    r"^finish(?:ed)?\s+(?:the\s+|my\s+)?",
    r"^i'?(?:ve|m)?\s*(?:finished|done|completed)\s+(?:with\s+)?(?:the\s+|my\s+)?",
]
_COMPLETE_SUFFIXES = [
    r"\s+as\s+(?:done|completed|complete)$",
    r"\s+(?:task\s+)?(?:is\s+)?(?:done|completed|complete)$",
    r"\s+task$",
]

_DELETE_PREFIXES = [
    r"^(?:please\s+)?delete\s+(?:the\s+|my\s+)?",
    r"^(?:please\s+)?remove\s+(?:the\s+|my\s+)?",
    r"^cancel\s+(?:the\s+|my\s+)?",
]
_DELETE_SUFFIXES = [r"\s+task$"]

_STATUS_PREFIXES = [
    r"^(?:what'?s|what\s+is)\s+the\s+status\s+of\s+(?:the\s+|my\s+)?",
    r"^status\s+of\s+(?:the\s+|my\s+)?",
    r"^(?:have\s+i|did\s+i)\s+(?:finish|complete)(?:ed)?\s+(?:the\s+|my\s+)?",
    r"^is\s+(?:the\s+|my\s+)?",
]
_STATUS_SUFFIXES = [r"\s+(?:done|completed|complete|finished)\??$", r"\s+task$"]

# Used only for the "bare imperative" ADD_TASK fallback below (e.g. a plain
# "Buy groceries" with none of the _ADD_PREFIXES phrases). Keeps random,
# unrecognized text from silently becoming a task instead of surfacing the
# helpful UNKNOWN error message.
_COMMON_TASK_VERBS = {
    "add", "buy", "call", "email", "finish", "study", "complete", "submit",
    "pay", "book", "schedule", "clean", "write", "read", "review", "prepare",
    "fix", "plan", "meet", "visit", "renew", "cook", "walk", "water", "feed",
    "pack", "print", "apply", "order", "send", "update", "check", "organize",
    "organise", "file", "return", "attend", "practice", "practise", "revise",
    "get", "pick", "drop", "collect", "upload", "download", "backup",
    "charge", "tidy", "exercise", "workout", "install", "setup", "set",
    "configure", "text", "message", "reply", "confirm", "register", "sign",
    "start", "do",
}


def _strip_patterns(text: str, patterns: list[str]) -> str:
    out = text
    for pat in patterns:
        out = re.sub(pat, "", out, flags=re.IGNORECASE).strip()
    return out


def _extract_due(text: str) -> tuple[str, Optional[str]]:
    m = _DUE_PATTERN.search(text)
    if not m:
        return text, None
    due = re.sub(r"\s+", " ", m.group(1)).strip()
    remaining = (text[: m.start()] + " " + text[m.end():]).strip()
    remaining = re.sub(r"\s{2,}", " ", remaining).strip(" ,.-")
    return remaining, due


def parse_message(raw_text: str) -> Intent:
    text = raw_text.strip()
    lower = text.lower().strip().rstrip("?.!")

    if not lower:
        return Intent(action=UNKNOWN)

    # --- CLEAR_TASKS -------------------------------------------------
    if "clear" in lower and ("task" in lower or "list" in lower or "todo" in lower):
        only_completed = "complet" in lower  # "clear my completed tasks"
        return Intent(action=CLEAR_TASKS, show_completed=only_completed)

    # --- COMPLETE_TASK -------------------------------------------------
    complete_trigger = (
        lower.startswith(("mark ", "complete ", "finish ", "finished "))
        or re.search(r"\bi'?(?:ve|m)?\s*(?:finished|done|completed)\b", lower)
        or "as done" in lower
        or "as completed" in lower
    )
    if complete_trigger and not lower.startswith(("delete", "remove", "cancel")):
        query = _strip_patterns(lower, _COMPLETE_PREFIXES)
        query = _strip_patterns(query, _COMPLETE_SUFFIXES)
        query = query.strip()
        if query:
            return Intent(action=COMPLETE_TASK, task_query=query)

    # --- DELETE_TASK -------------------------------------------------
    if lower.startswith(("delete", "remove", "cancel")) or " delete " in lower or " remove " in lower:
        query = _strip_patterns(lower, _DELETE_PREFIXES)
        query = _strip_patterns(query, _DELETE_SUFFIXES)
        query = query.strip()
        if query:
            return Intent(action=DELETE_TASK, task_query=query)

    # --- TASK_STATUS ---------------------------------------------------
    if lower.startswith(("is ", "status of", "what's the status", "what is the status")) or \
       re.match(r"^(have|did)\s+i\s+(finish|complete)", lower):
        query = _strip_patterns(lower, _STATUS_PREFIXES)
        query = _strip_patterns(query, _STATUS_SUFFIXES)
        query = query.strip()
        if query:
            return Intent(action=TASK_STATUS, task_query=query)

    # --- LIST_TASKS ------------------------------------------------------
    list_phrases = (
        "what are my task", "what do i have to do", "what's on my list",
        "what is on my list", "show my task", "show tasks", "show my to-do",
        "show my todo", "list my task", "my tasks", "my to-do list",
        "my todo list", "pending tasks", "what tasks do i have",
        "what's pending", "what is pending",
    )
    if any(p in lower for p in list_phrases) or lower in ("tasks", "todo", "to-do", "to do list"):
        show_completed = "all" in lower or "completed" in lower and "pending" not in lower
        return Intent(action=LIST_TASKS, show_completed=show_completed)

    # --- ADD_TASK (default for task-like statements) --------------------
    add_signal = any(
        re.match(pat, lower) for pat in _ADD_PREFIXES
    ) or lower.startswith(("add ",))
    remainder, due = _extract_due(text)
    stripped = _strip_patterns(remainder.strip().rstrip("?.!"), _ADD_PREFIXES)

    if add_signal and stripped:
        return Intent(action=ADD_TASK, description=stripped, due_date=due)

    # Heuristic fallback: a bare imperative starting with a recognizable
    # task verb ("Buy groceries", "Call the dentist tomorrow") is still
    # ADD_TASK even without an explicit trigger phrase. Anything else that
    # didn't match a known pattern is UNKNOWN, so random/unrelated text gets
    # the helpful error message instead of silently becoming a task.
    first_word = re.match(r"[a-z']+", stripped.lower() or remainder.strip().lower())
    if first_word and first_word.group(0) in _COMMON_TASK_VERBS:
        return Intent(action=ADD_TASK, description=stripped or remainder.strip(), due_date=due)

    return Intent(action=UNKNOWN)
