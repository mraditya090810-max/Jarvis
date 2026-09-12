"""
Discovers every currently-FREE model on OpenRouter.

OpenRouter's GET /api/v1/models returns every model it offers, each with a
"pricing" block like:

    "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"}

A model is free when prompt+completion pricing are both "0" (some providers
also expose the same model under a ":free" id suffix — we treat either
signal as free). Anything else is a paid model and is never considered,
per project requirements — no hardcoded model names, no paid fallback.

Results are cached locally for `model_refresh_hours` (default 24h) so we
don't hit the discovery endpoint on every single request.
"""
from __future__ import annotations

import json
import time

import requests

from .config import MODELS_CACHE_PATH, OPENROUTER_BASE_URL, get_tuning

# OpenRouter sits behind Cloudflare, which routinely 403s the bare
# `python-requests/x.y` user-agent requests' library sends by default —
# with no headers at all this endpoint can fail every single time even
# though nothing is actually wrong with the key or the network. A normal
# browser-shaped User-Agent + Accept header avoids that false block.
_DISCOVERY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _is_free(model: dict) -> bool:
    pricing = model.get("pricing", {}) or {}

    def _z(v) -> bool:
        try:
            return float(v) == 0.0
        except (TypeError, ValueError):
            return False

    if _z(pricing.get("prompt")) and _z(pricing.get("completion")):
        return True
    return str(model.get("id", "")).endswith(":free")


def _fetch_from_openrouter(timeout: int = 20) -> list[dict]:
    resp = requests.get(f"{OPENROUTER_BASE_URL}/models", headers=_DISCOVERY_HEADERS, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    models = payload.get("data", payload if isinstance(payload, list) else [])
    return [m for m in models if _is_free(m)]


def _load_cache() -> dict | None:
    try:
        data = json.loads(MODELS_CACHE_PATH.read_text(encoding="utf-8"))
        if "fetched_at" in data and "models" in data:
            return data
    except Exception:
        pass
    return None


def _save_cache(models: list[dict]) -> None:
    try:
        MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODELS_CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "models": models}),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[AIRouter] WARN: failed to persist model cache: {e}")


def get_free_models(force_refresh: bool = False) -> list[dict]:
    """
    Returns the cached (or freshly fetched) list of free OpenRouter models.
    Never raises — on any discovery failure, falls back to whatever is in
    the local cache (even if stale), and only returns [] if there is no
    cache at all and discovery failed.
    """
    tuning = get_tuning()
    max_age_s = tuning.get("model_refresh_hours", 24) * 3600

    cached = _load_cache()
    if not force_refresh and cached and (time.time() - cached["fetched_at"]) < max_age_s:
        return cached["models"]

    try:
        models = _fetch_from_openrouter()
        if models:
            _save_cache(models)
            return models
    except Exception as e:
        print(f"[AIRouter] Model discovery failed ({e}); using cached/stale list if available.")

    return cached["models"] if cached else []
