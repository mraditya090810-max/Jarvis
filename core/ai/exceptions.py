"""Exception hierarchy for core.ai."""


class AIRouterError(Exception):
    """Base class for all AI router errors."""


class ProviderError(AIRouterError):
    """A single provider/model call failed (network, HTTP error, bad output).
    The retry chain catches this and moves on to the next free model."""


class RateLimitedError(ProviderError):
    """The provider returned 429 / rate-limit for this model right now.
    Treated the same as ProviderError by the fallback chain (try next model),
    but kept distinct so callers/logs can tell rate limits apart from
    genuine failures."""


class NoFreeModelAvailable(AIRouterError):
    """Every discovered free model failed for this request. This is the only
    error that should ever escape the router — it means fallback is exhausted."""

    def __init__(self, attempts: list[str] | None = None):
        self.attempts = attempts or []
        super().__init__(
            "No free coding model is currently available. Please try again later."
        )
