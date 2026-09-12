"""
MultiProvider — dispatches a chat request to the right backend based on a
scheme prefix on the model id.

The router builds model ids like "ollama/qwen2.5-coder:7b" for the local
Ollama backend, and bare ids like "vendor/model:free" for OpenRouter. This
class just looks at the prefix and forwards to the matching Provider — it
has no fallback/ranking logic of its own, that lives in router.py.
"""
from __future__ import annotations

from .base import Provider
from .ollama import OllamaProvider

OLLAMA_PREFIX = "ollama/"


class MultiProvider(Provider):
    def __init__(self, fallback: Provider):
        self._ollama = OllamaProvider()
        self._fallback = fallback

    def chat(self, model: str, messages: list[dict], timeout: int) -> str:
        if model.startswith(OLLAMA_PREFIX):
            return self._ollama.chat(model[len(OLLAMA_PREFIX):], messages, timeout)
        return self._fallback.chat(model, messages, timeout)
