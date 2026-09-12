"""
Runs a chat request across a ranked list of free models, falling back to the
next one on any failure (rate limit, timeout, network error, empty output).
Returns as soon as one succeeds. Raises NoFreeModelAvailable only once every
model in the list has been tried.
"""
from __future__ import annotations

import time

from .exceptions import NoFreeModelAvailable, ProviderError, RateLimitedError
from .providers.base import Provider


def run_with_fallback(
    provider: Provider,
    ranked_models: list[str],
    messages: list[dict],
    timeout: int,
    max_attempts: int = 0,
) -> tuple[str, str, list[dict]]:
    """
    Returns (response_text, model_used, attempts_log).
    attempts_log: list of {"model", "outcome", "duration_s"} for every model tried.
    """
    if not ranked_models:
        raise NoFreeModelAvailable([])

    limit = max_attempts if max_attempts and max_attempts > 0 else len(ranked_models)
    attempts_log: list[dict] = []

    for model in ranked_models[:limit]:
        start = time.time()
        try:
            text = provider.chat(model, messages, timeout=timeout)
            attempts_log.append({
                "model": model, "outcome": "success",
                "duration_s": round(time.time() - start, 3),
            })
            return text, model, attempts_log
        except RateLimitedError as e:
            attempts_log.append({
                "model": model, "outcome": "rate_limited",
                "duration_s": round(time.time() - start, 3), "error": str(e),
            })
            continue
        except ProviderError as e:
            attempts_log.append({
                "model": model, "outcome": "failed",
                "duration_s": round(time.time() - start, 3), "error": str(e),
            })
            continue

    raise NoFreeModelAvailable([a["model"] for a in attempts_log])
