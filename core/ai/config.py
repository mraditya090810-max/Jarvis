"""
Configuration for the AI Router.

Reads the OpenRouter API key from config/api_keys.json (key: "openrouter_api_key"),
same file every other MARK XLIX module already uses — no new config file format
introduced. Optional tuning lives in config/ai_router.json and is entirely
optional; sane defaults are used if it's absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR           = get_base_dir()
API_KEYS_PATH       = BASE_DIR / "config" / "api_keys.json"
TUNING_PATH         = BASE_DIR / "config" / "ai_router.json"
MODELS_CACHE_PATH   = BASE_DIR / "memory" / "ai_router_models.json"
RESPONSE_CACHE_PATH = BASE_DIR / "memory" / "ai_router_cache.json"
LOG_PATH            = BASE_DIR / "memory" / "ai_router_logs.jsonl"
METRICS_PATH        = BASE_DIR / "memory" / "ai_router_metrics.json"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DEFAULT_TUNING = {
    # Free models are re-discovered from OpenRouter at most this often.
    "model_refresh_hours": 24,
    # Per-model request timeout in seconds.
    "request_timeout": 120,
    # Max free models to try before giving up (0 = try all discovered free models).
    "max_fallback_attempts": 0,
    # Response cache: how many entries to keep (oldest evicted first).
    "cache_max_entries": 500,
    # Referer / title sent to OpenRouter (recommended by their API, not required).
    "http_referer": "https://github.com/mark-xlix",
    "app_title": "Mark-XLIX JARVIS",
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_openrouter_api_key() -> str:
    """
    Returns the OpenRouter API key, or "" if not configured.
    An empty key still allows /models discovery (public endpoint) but chat
    completion calls will fail — the router surfaces that as ProviderError,
    which the fallback chain treats like any other per-model failure.
    """
    data = _load_json(API_KEYS_PATH)
    return (data.get("openrouter_api_key") or "").strip()


def get_tuning() -> dict:
    cfg = dict(_DEFAULT_TUNING)
    cfg.update(_load_json(TUNING_PATH))
    return cfg
