"""
Simple JSON-file response cache.

Key = sha256(system + "\x00" + prompt + "\x00" + project_hash)
project_hash defaults to "" (global cache). Callers that want cache entries
to auto-invalidate when files change (as required for Self Engineer patches)
should pass a project_hash derived from the affected files' mtimes/content —
see core.ai.router.call_llm_text(..., project_hash=...).

A JSON file is plenty for this workload (Dev Agent is used "occasionally",
per the project's own spec — not high throughput), so we avoid the extra
complexity/locking of sqlite here.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .config import RESPONSE_CACHE_PATH, get_tuning

_LOCK = threading.Lock()


def make_key(system: str | None, prompt: str, project_hash: str = "") -> str:
    h = hashlib.sha256()
    h.update((system or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(project_hash.encode("utf-8"))
    return h.hexdigest()


def _load() -> dict:
    try:
        return json.loads(RESPONSE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        RESPONSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESPONSE_CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"[AIRouter] WARN: failed to persist cache: {e}")


def get(key: str) -> str | None:
    with _LOCK:
        data = _load()
    entry = data.get(key)
    return entry["response"] if entry else None


def set(key: str, response: str, task: str = "") -> None:
    tuning = get_tuning()
    max_entries = tuning.get("cache_max_entries", 500)
    with _LOCK:
        data = _load()
        data[key] = {"response": response, "task": task, "ts": time.time()}
        if len(data) > max_entries:
            # Evict oldest entries first.
            for k in sorted(data, key=lambda k: data[k].get("ts", 0))[: len(data) - max_entries]:
                del data[k]
        _save(data)


def invalidate_all() -> None:
    with _LOCK:
        _save({})
