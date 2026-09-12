"""
Rolling aggregate metrics for the AI Router: selected-model counts, cache
hit rate, fallback rate, average latency, success rate. Recomputed cheaply
from the JSONL log rather than kept as separate mutable state, so it can
never drift from what actually happened.
"""
from __future__ import annotations

import json

from .config import LOG_PATH, METRICS_PATH


def compute_metrics(max_lines: int = 5000) -> dict:
    if not LOG_PATH.exists():
        return {
            "total_requests": 0, "successes": 0, "failures": 0,
            "success_rate": None, "cache_hits": 0, "cache_hit_rate": None,
            "fallback_rate": None, "avg_duration_s": None, "model_usage": {},
        }

    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-max_lines:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    total = len(entries)
    successes = sum(1 for e in entries if e.get("success"))
    cache_hits = sum(1 for e in entries if e.get("cache_used"))
    fallbacks = sum(1 for e in entries if e.get("fallback_used"))
    durations = [e["duration_s"] for e in entries if isinstance(e.get("duration_s"), (int, float))]

    model_usage: dict[str, int] = {}
    for e in entries:
        m = e.get("model")
        if m:
            model_usage[m] = model_usage.get(m, 0) + 1

    metrics = {
        "total_requests": total,
        "successes": successes,
        "failures": total - successes,
        "success_rate": (successes / total) if total else None,
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / total) if total else None,
        "fallback_rate": (fallbacks / total) if total else None,
        "avg_duration_s": (sum(durations) / len(durations)) if durations else None,
        "model_usage": model_usage,
    }

    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[AIRouter] WARN: failed to persist metrics: {e}")

    return metrics
