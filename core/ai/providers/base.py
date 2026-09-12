"""Abstract base class every AI provider backend implements."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    @abstractmethod
    def chat(self, model: str, messages: list[dict], timeout: int) -> str:
        """
        Send a chat-completion request to `model` and return the assistant's
        text content.

        Must raise core.ai.exceptions.RateLimitedError on HTTP 429, and
        core.ai.exceptions.ProviderError for every other failure (network
        error, timeout, non-200 status, empty/malformed response). Never
        raise a bare Exception — the router's fallback chain only knows how
        to handle these two.
        """
        raise NotImplementedError
