"""
AIRouter — the single entry point every AI-consuming module should call.

    from core.ai import call_llm_text
    text = call_llm_text(prompt, system="You are a...", timeout=90)

Pipeline for every call:
  1. Compute cache key (system + prompt + optional project_hash) → cache hit
     returns instantly, zero API requests, logged with cache_used=True.
  2. Discover free OpenRouter models (cached ≤24h) and rank them.
  3. Try the best-ranked free model; on failure (rate limit / timeout /
     network error / empty output) fall back to the next one.
  4. On success: cache the response, log the attempt chain, return the text.
  5. If every free model fails: raise NoFreeModelAvailable with the exact
     message required by spec, still logged.

Never touches a paid model. Never asks the user for a paid API key.
"""
from __future__ import annotations

import time

from . import cache as _cache
from .config import get_tuning
from .exceptions import NoFreeModelAvailable
from .logger import log_request
from .model_discovery import get_free_models
from .model_selector import rank_models, rank_vision_models
from .prompt_optimizer import optimize
from .providers.openrouter import OpenRouterProvider
from .retry import run_with_fallback


class AIRouter:
    def __init__(self, provider=None):
        self._provider = provider or OpenRouterProvider()

    def _ranked_free_models(self) -> list[str]:
        models = get_free_models()
        return rank_models(models)

    def _ranked_free_vision_models(self) -> list[str]:
        models = get_free_models()
        return rank_vision_models(models)

    def call_vision(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/png",
        timeout: int | None = None,
        task: str = "vision",
    ) -> str:
        """Same free-first/fallback/cache/log pipeline as call(), but for a
        request that includes an image. Only tries free models OpenRouter
        marks as accepting image input — raises NoFreeModelAvailable (same
        message as the text path) if none are currently offered for free."""
        tuning = get_tuning()
        timeout = timeout or tuning.get("request_timeout", 120)

        # Cache key includes the image bytes so a different screenshot never
        # returns a stale cached analysis, while an identical repeat (same
        # screenshot, same question) is free.
        cache_key = _cache.make_key(image_base64[:64] + image_base64[-64:], prompt, task)
        cached = _cache.get(cache_key)
        if cached is not None:
            log_request(task=task, model=None, attempts=[], duration_s=0.0, success=True, cache_used=True)
            return cached

        ranked = self._ranked_free_vision_models()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
            ],
        }]

        start = time.time()
        try:
            text, model_used, attempts = run_with_fallback(
                self._provider, ranked, messages, timeout,
                max_attempts=tuning.get("max_fallback_attempts", 0),
            )
        except NoFreeModelAvailable as e:
            log_request(
                task=task, model=None, attempts=[{"model": m, "outcome": "failed"} for m in e.attempts],
                duration_s=time.time() - start, success=False, cache_used=False, error=str(e),
            )
            raise

        _cache.set(cache_key, text, task=task)
        log_request(
            task=task, model=model_used, attempts=attempts,
            duration_s=time.time() - start, success=True, cache_used=False,
        )
        return text

    def call(
        self,
        prompt: str,
        system: str | None = None,
        timeout: int | None = None,
        project_hash: str = "",
        task: str = "generic",
        optimize_prompt: bool = True,
    ) -> str:
        tuning = get_tuning()
        timeout = timeout or tuning.get("request_timeout", 120)

        prompt_to_send = optimize(prompt) if optimize_prompt else prompt

        cache_key = _cache.make_key(system, prompt_to_send, project_hash)
        cached = _cache.get(cache_key)
        if cached is not None:
            log_request(
                task=task, model=None, attempts=[], duration_s=0.0,
                success=True, cache_used=True,
            )
            return cached

        ranked = self._ranked_free_models()
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt_to_send})

        start = time.time()
        try:
            text, model_used, attempts = run_with_fallback(
                self._provider, ranked, messages, timeout,
                max_attempts=tuning.get("max_fallback_attempts", 0),
            )
        except NoFreeModelAvailable as e:
            log_request(
                task=task, model=None, attempts=[{"model": m, "outcome": "failed"} for m in e.attempts],
                duration_s=time.time() - start, success=False, cache_used=False,
                error=str(e),
            )
            raise

        _cache.set(cache_key, text, task=task)
        log_request(
            task=task, model=model_used, attempts=attempts,
            duration_s=time.time() - start, success=True, cache_used=False,
        )
        return text


_default_router: AIRouter | None = None


def _get_default_router() -> AIRouter:
    global _default_router
    if _default_router is None:
        _default_router = AIRouter()
    return _default_router


def call_llm_text(
    prompt: str,
    system: str | None = None,
    timeout: int = 120,
    project_hash: str = "",
    task: str = "generic",
) -> str:
    """Drop-in replacement for core.llm_client.call_llm_text — same
    signature/return type, backed by free OpenRouter models instead of a
    local LLM server."""
    return _get_default_router().call(
        prompt, system=system, timeout=timeout, project_hash=project_hash, task=task,
    )


def call_llm_vision(
    prompt: str,
    image_base64: str,
    mime_type: str = "image/png",
    timeout: int = 120,
    task: str = "vision",
) -> str:
    """Drop-in replacement for the old direct Gemini vision call in
    code_helper's screen_debug — backed by whichever free OpenRouter model
    currently accepts image input."""
    return _get_default_router().call_vision(
        prompt, image_base64, mime_type=mime_type, timeout=timeout, task=task,
    )


def call_llm_chat(
    messages: list[dict],
    timeout: int = 120,
    task: str = "generic",
) -> str:
    """For callers that already have a full messages list (system+user
    turns) instead of a single prompt string."""
    system = None
    user_parts = []
    for m in messages:
        if m.get("role") == "system":
            system = (system or "") + m.get("content", "") + "\n"
        else:
            user_parts.append(m.get("content", ""))
    return call_llm_text("\n".join(user_parts), system=system, timeout=timeout, task=task)
