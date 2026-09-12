"""
Local Ollama provider for core.ai.

This lets every core.ai consumer — self_engineer (patch_generator,
autonomous/*), dev_agent, research/*, and the plugins — run against a local
Ollama model instead of (or before) OpenRouter's free models, with zero
manual setup: the server is auto-launched on first use via
core.llm_client.ensure_ollama_running(), exactly like the voice assistant
already does.

Model ids passed to .chat() must be the *bare* Ollama model name (e.g.
"qwen2.5-coder:7b") — core.ai.router strips the "ollama/" scheme prefix it
uses internally to route between this provider and OpenRouter before
calling here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from ..exceptions import ProviderError, RateLimitedError
from .base import Provider

_SESSION = requests.Session()

# core/ai/providers/ollama.py -> core/ai/providers -> core/ai -> core -> project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _import_llm_client():
    """core.llm_client already knows how to find/launch a local Ollama
    server — reuse it instead of duplicating that logic here."""
    root_str = str(_PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from core import llm_client  # type: ignore
    return llm_client


class OllamaProvider(Provider):
    def chat(self, model: str, messages: list[dict], timeout: int) -> str:
        try:
            llm_client = _import_llm_client()
        except Exception as e:
            raise ProviderError(f"ollama/{model}: could not load core.llm_client ({e})")

        # Auto-start: launches `ollama serve` if it isn't already running and
        # waits for it to come up. This is what makes Ollama usable here
        # without the person ever starting it by hand.
        if not llm_client.ensure_ollama_running():
            raise ProviderError(
                f"ollama/{model}: Ollama is not reachable and could not be "
                "auto-started. Install it from https://ollama.com and make "
                "sure the 'ollama' command is on PATH."
            )

        from ..config import get_ollama_settings
        url, _ = get_ollama_settings()

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,
            # Self-engineering/dev-agent/research prompts often ask for a
            # full file rewrite or a long report — unlike the voice
            # assistant's short spoken replies, these must not be truncated.
            "options": {"num_predict": -1, "num_gpu": 99},
        }

        try:
            resp = _SESSION.post(f"{url}/api/chat", json=payload, timeout=timeout)
        except requests.exceptions.Timeout:
            raise ProviderError(f"ollama/{model}: request timed out after {timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise ProviderError(f"ollama/{model}: connection error ({e})")
        except Exception as e:
            raise ProviderError(f"ollama/{model}: request failed ({e})")

        if resp.status_code == 404:
            raise ProviderError(
                f"ollama/{model}: model not pulled locally. Run: ollama pull {model}"
            )
        if resp.status_code == 429:
            raise RateLimitedError(f"ollama/{model}: rate limited (429)")
        if resp.status_code >= 400:
            raise ProviderError(f"ollama/{model}: HTTP {resp.status_code} — {resp.text[:300]}")

        try:
            content = resp.json().get("message", {}).get("content", "")
        except Exception as e:
            raise ProviderError(f"ollama/{model}: malformed response ({e})")

        if not content or not content.strip():
            raise ProviderError(f"ollama/{model}: empty response")

        return content.strip()
