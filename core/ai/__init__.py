"""
core.ai — Free-first OpenRouter AI Router for MARK XLIX.

Public API (drop-in replacement for the old direct Gemini calls):

    from core.ai import call_llm_text
    text = call_llm_text(prompt, system=None, timeout=120)

That's the only function most callers (dev_agent, self_engineer/patch_generator)
need. It automatically:
  - discovers every FREE model currently offered by OpenRouter
  - ranks them for coding quality / context size / reliability
  - checks the local cache before spending a request
  - tries the best free model, and falls back through the ranked list on
    failure (429 / timeout / network error / empty output)
  - logs every attempt to memory/ai_router_logs.jsonl
  - never touches a paid model and never asks for one

Raises core.ai.exceptions.NoFreeModelAvailable only when every single free
model has been tried and failed.
"""
from .router import AIRouter, call_llm_text, call_llm_vision, call_llm_chat
from .concrete_router import ConcreteAIRouter
from .exceptions import AIRouterError, NoFreeModelAvailable, ProviderError, RateLimitedError

__all__ = [
    "AIRouter",
    "ConcreteAIRouter",
    "call_llm_text",
    "call_llm_vision",
    "call_llm_chat",
    "AIRouterError",
    "NoFreeModelAvailable",
    "ProviderError",
    "RateLimitedError",
]
