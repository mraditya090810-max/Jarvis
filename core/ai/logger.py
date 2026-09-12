"""
Append-only JSONL logger — one line per AI Router request.

Never raises: logging must not be able to break a real request. Every write
is best-effort; failures are printed to stdout and swallowed.
"""
from __future__ import annotations

import json
import time

from .config import LOG_PATH


def log_request(
    *,
    task: str,
    model: str | None,
    attempts: list[dict],
    duration_s: float,
    success: bool,
    cache_used: bool,
    error: str | None = None,
) -> None:
    """
    attempts: list of {"model": str, "outcome": "success"|"rate_limited"|"failed", "duration_s": float}
    """
    entry = {
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task":        task,
        "model":       model,
        "attempts":    attempts,
        "fallback_used": len(attempts) > 1,
        "cache_used":  cache_used,
        "duration_s":  round(duration_s, 3),
        "success":     success,
        "error":       error,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[AIRouter] WARN: failed to write log entry: {e}")
