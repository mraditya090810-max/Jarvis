"""OpenRouter chat-completions provider (free models only — enforced upstream
by model_discovery/model_selector, this class just makes the HTTP call)."""
from __future__ import annotations

import requests

from ..config import OPENROUTER_BASE_URL, get_openrouter_api_key, get_tuning
from ..exceptions import ProviderError, RateLimitedError
from .base import Provider

_SESSION = requests.Session()


class OpenRouterProvider(Provider):
    def chat(self, model: str, messages: list[dict], timeout: int) -> str:
        api_key = get_openrouter_api_key()
        if not api_key:
            raise ProviderError(
                "No OpenRouter API key configured — set \"openrouter_api_key\" "
                "in config/api_keys.json (a free key from https://openrouter.ai/keys "
                "is enough; free models never charge it)."
            )

        tuning = get_tuning()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": tuning.get("http_referer", ""),
            "X-Title": tuning.get("app_title", "Mark-XLIX JARVIS"),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        payload = {"model": model, "messages": messages}

        try:
            resp = _SESSION.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            raise ProviderError(f"{model}: request timed out after {timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise ProviderError(f"{model}: connection error ({e})")
        except Exception as e:
            raise ProviderError(f"{model}: request failed ({e})")

        if resp.status_code == 429:
            raise RateLimitedError(f"{model}: rate limited (429)")
        if resp.status_code >= 400:
            raise ProviderError(f"{model}: HTTP {resp.status_code} — {resp.text[:300]}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            raise ProviderError(f"{model}: malformed response ({e})")

        if not content or not content.strip():
            raise ProviderError(f"{model}: empty response")

        return content.strip()
