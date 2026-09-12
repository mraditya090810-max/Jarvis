"""
memory/action_memory.py — short-term file action memory.

Tracks the last few file operations (create/rename/move/copy/write/read)
so that a follow-up command referencing "it", "that file", "this",
"last file", "previous file", "new file", or "recent file" resolves to
the right absolute path without asking the user again.

This is deliberately separate from memory/long_term.json (that's durable,
user-fact memory). Action memory is short-term and scoped to file
references only.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

_REFERENCE_WORDS = {
    "it", "this", "that", "this file", "that file",
    "last file", "previous file", "new file", "recent file", "the file",
}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_STORE_PATH = _base_dir() / "memory" / "action_memory.json"
_MAX_ENTRIES = 20
_lock = threading.Lock()
_entries: deque[dict] = deque(maxlen=_MAX_ENTRIES)
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            for item in data[-_MAX_ENTRIES:]:
                _entries.append(item)
        except Exception:
            pass
        _loaded = True


def _persist() -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STORE_PATH.write_text(
            json.dumps(list(_entries), indent=2), encoding="utf-8"
        )
    except Exception:
        # Action memory must never break a file operation.
        pass


def is_reference(raw: str) -> bool:
    """True if raw looks like a pronoun/reference rather than a real name."""
    return (raw or "").strip().lower() in _REFERENCE_WORDS


def record(operation: str, path: Path) -> None:
    """Call after any successful create/rename/move/copy/write on `path`."""
    _ensure_loaded()
    entry = {
        "absolute_path": str(path.resolve()) if path.exists() else str(path),
        "filename": path.name,
        "folder": str(path.parent),
        "operation": operation,
        "timestamp": time.time(),
    }
    with _lock:
        _entries.append(entry)
    _persist()


def last(n: int = 1) -> list[dict]:
    """Most recent n entries, newest first."""
    _ensure_loaded()
    with _lock:
        return list(_entries)[-n:][::-1]


def resolve_reference(raw: str) -> Path | None:
    """Resolve a pronoun like 'it' / 'last file' to the most recently
    touched file's absolute path, or None if action memory is empty."""
    recent = last(1)
    if not recent:
        return None
    return Path(recent[0]["absolute_path"])


def recent_paths(n: int = 10) -> list[Path]:
    """Absolute paths of recently touched files, newest first — used as
    one of the search sources for bare filenames with no folder given."""
    return [Path(e["absolute_path"]) for e in last(n)]


def reset_for_tests() -> None:
    global _loaded
    with _lock:
        _entries.clear()
        _loaded = False
