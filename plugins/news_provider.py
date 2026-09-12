"""
plugins/news_provider.py — JARVIS custom news provider.

A normal drop-in JARVIS plugin (PLUGIN + run() contract — see
plugins/_template.py). JARVIS discovers it automatically at startup; no
other file needs to change for the voice/text side to work.

What it does, in plain terms:
    - The user tells JARVIS what they care about ("I want all the latest
      updates about the Champions League", "keep me posted on Tesla stock").
      JARVIS remembers that as a "topic" in a small persistent list.
    - When the user later just says "give me the news" / "what's new" /
      "any updates for me", JARVIS reports news ONLY about the topics on
      that list — never generic world headlines, unless a topic was named
      in the same request. That's the whole point of this being a *custom*
      news provider instead of the general-purpose 'web_search' tool.
    - The topic list can also be managed from the UI (News Topics panel in
      the settings drawer) — main.py wires that panel's add/remove buttons
      straight into add_topic()/remove_topic() below.

Storage: a small JSON file, same pattern as plugins/todo_list.py.
Path resolution order:
    1. NEWS_TOPICS_STORAGE_PATH env var, if set.
    2. <project_root>/memory/news_topics.json.

Fetching: reuses actions/web_search.py's existing, already-hardened news
pipeline (_news — parallel Gemini-grounded search + DuckDuckGo news, with
automatic fallback between the two) instead of re-implementing HTTP calls
here. That module has no dependency on this one, so importing it does not
create a cycle.
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
    override = os.environ.get("NEWS_TOPICS_STORAGE_PATH")
    if override:
        return Path(override).expanduser()
    return _project_root() / "memory" / "news_topics.json"


def _load() -> dict:
    path = _storage_path()
    if not path.exists():
        return {"next_id": 1, "topics": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("next_id", 1)
        data.setdefault("topics", [])
        return data
    except Exception:
        # Corrupt/empty file — never crash the assistant over a bad JSON file.
        return {"next_id": 1, "topics": []}


def _save(data: dict) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX and Windows


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _clean_topic(text: str) -> str:
    text = text.strip().strip(".").strip()
    # Strip common carrier phrases so "I want updates about the Lakers"
    # and "the Lakers" both store as "the Lakers" -> normalised to "Lakers".
    text = re.sub(
        r"^(i want|i'd like|give me|show me|keep me posted on|keep me updated on|"
        r"follow|track|notify me about|updates? (on|about)|news (on|about)|"
        r"the latest( updates)? (on|about))\s+",
        "", text, flags=re.IGNORECASE,
    ).strip()
    text = re.sub(r"^(all the latest updates (on|about)|latest updates (on|about))\s+",
                   "", text, flags=re.IGNORECASE).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def _find_match(topics: list[dict], query: str) -> Optional[dict]:
    q = query.strip().lower()
    if not q:
        return None
    for t in topics:
        if t["topic"].lower() == q:
            return t
    matches = [t for t in topics if q in t["topic"].lower() or t["topic"].lower() in q]
    if matches:
        return sorted(matches, key=lambda t: len(t["topic"]))[0]
    q_words = set(re.findall(r"[a-z0-9]+", q))
    for t in topics:
        t_words = set(re.findall(r"[a-z0-9]+", t["topic"].lower()))
        if q_words and q_words & t_words:
            return t
    return None


# --------------------------------------------------------------------------
# Core API — used by both run() below and the UI's News Topics panel
# (main.py wires these straight to ui.on_news_topics_list / _add / _remove)
# --------------------------------------------------------------------------

def add_topic(topic: str) -> dict:
    topic = _clean_topic(topic)
    if not topic:
        raise ValueError("Topic cannot be empty.")
    with _LOCK:
        data = _load()
        for t in data["topics"]:
            if t["topic"].lower() == topic.lower():
                return t  # already tracked — no duplicate
        entry = {
            "id": data["next_id"],
            "topic": topic,
            "added_at": datetime.now().isoformat(timespec="seconds"),
        }
        data["topics"].append(entry)
        data["next_id"] += 1
        _save(data)
        return entry


def list_topics() -> list[dict]:
    return list(_load()["topics"])


def remove_topic(query: str) -> tuple[bool, Optional[dict]]:
    with _LOCK:
        data = _load()
        match = _find_match(data["topics"], query)
        if not match:
            return False, None
        data["topics"] = [t for t in data["topics"] if t["id"] != match["id"]]
        _save(data)
        return True, match


def clear_topics() -> int:
    with _LOCK:
        data = _load()
        before = len(data["topics"])
        data["topics"] = []
        _save(data)
        return before


def get_news(topic: Optional[str] = None) -> str:
    """
    topic given  -> news for that one topic (and auto-tracks it, since asking
                    about something once is exactly the "I want updates about
                    X" signal — this is what lets a bare "what's happening
                    with the Lakers" both answer AND start tracking it).
    topic is None -> news for every topic on the saved list, and ONLY those —
                    never a generic world-news dump. If the list is empty,
                    says so instead of guessing at "top world news".
    """
    from actions.web_search import _news as _fetch_news

    if topic:
        add_topic(topic)
        clean = _clean_topic(topic)
        result = _fetch_news(clean)
        return f"News about {clean}:\n\n{result}"

    topics = list_topics()
    if not topics:
        return (
            "You haven't told me what you'd like news about yet, sir. "
            "Say something like 'I want updates about the Champions League' "
            "and I'll start tracking it — then just ask for 'my news' anytime."
        )

    sections = []
    for t in topics:
        try:
            sections.append(f"— {t['topic']} —\n{_fetch_news(t['topic'])}")
        except Exception as e:
            sections.append(f"— {t['topic']} —\n(Couldn't fetch this one: {e})")
    names = ", ".join(t["topic"] for t in topics)
    header = f"Here's what's new on your tracked topics ({names}):\n\n"
    return header + "\n\n".join(sections)


# --------------------------------------------------------------------------
# JARVIS plugin contract (desktop voice/text assistant)
# --------------------------------------------------------------------------

PLUGIN = {
    "name": "news_provider",
    "description": (
        "The user's PERSONAL, topic-scoped news feed — completely different from "
        "the general 'web_search' tool. Use this whenever the user wants to be "
        "kept updated on specific things over time, or asks for 'my news' / "
        "'the news' / 'what's new' / 'any updates' without naming a topic. "
        "Trigger ADD_TOPIC on phrases like 'I want all the latest updates about "
        "X', 'keep me posted on X', 'follow X for me', 'track news about X' — "
        "this remembers X permanently, it does NOT fetch news by itself. "
        "Trigger GET_NEWS (with no topic) on a bare 'give me the news', 'what's "
        "new', 'any updates for me' — this returns news ONLY for topics the "
        "user has previously told JARVIS about via ADD_TOPIC; it must NEVER "
        "fall back to generic/world news. Trigger GET_NEWS with a topic when "
        "the user asks about one specific thing by name (e.g. 'what's "
        "happening with Tesla') — this also remembers that topic for next "
        "time. Trigger LIST_TOPICS on 'what are you tracking for me' / 'what "
        "topics do I follow'. Trigger REMOVE_TOPIC on 'stop tracking X' / "
        "'I don't care about X anymore'. Trigger CLEAR_TOPICS on 'clear all "
        "my news topics'. Do NOT use 'web_search' for any of these — that "
        "tool is for one-off general lookups, not the user's standing list."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: ADD_TOPIC, GET_NEWS, LIST_TOPICS, REMOVE_TOPIC, CLEAR_TOPICS."
                ),
            },
            "topic": {
                "type": "STRING",
                "description": (
                    "The subject, e.g. 'Champions League', 'Tesla stock', "
                    "'Elden Ring DLC'. Required for ADD_TOPIC and REMOVE_TOPIC. "
                    "Optional for GET_NEWS — omit it to get news for every "
                    "tracked topic at once."
                ),
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().upper()
    topic = parameters.get("topic", "")

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(f"JARVIS: {msg}")
            except Exception:
                pass

    try:
        if action == "ADD_TOPIC":
            if not topic.strip():
                return "What would you like me to keep you updated on, sir?"
            entry = add_topic(topic)
            reply = f"Got it — I'll keep you updated on {entry['topic']}."
            _log(reply)
            return reply

        if action == "GET_NEWS":
            reply = get_news(topic if topic.strip() else None)
            _log(f"Fetched news ({topic or 'all tracked topics'}).")
            return reply

        if action == "LIST_TOPICS":
            topics = list_topics()
            if not topics:
                return "You're not tracking any news topics yet, sir."
            names = "\n".join(f"{i}. {t['topic']}" for i, t in enumerate(topics, 1))
            return f"You're currently tracking:\n{names}"

        if action == "REMOVE_TOPIC":
            if not topic.strip():
                return "Which topic should I stop tracking?"
            ok, entry = remove_topic(topic)
            if not ok:
                return "I couldn't find that topic on your tracked list."
            reply = f"Stopped tracking {entry['topic']}."
            _log(reply)
            return reply

        if action == "CLEAR_TOPICS":
            count = clear_topics()
            reply = f"Cleared {count} tracked topic{'s' if count != 1 else ''}."
            _log(reply)
            return reply

        return (
            "I can track news topics for you and report on just those. Try "
            "'I want updates about the Champions League' or 'give me my news'."
        )
    except Exception as e:
        return f"Sir, the news provider ran into a problem: {e}"
