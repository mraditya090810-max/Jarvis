"""
research/sources — modular multi-source search providers.

To add a new provider: write a SourceProvider subclass in its own file,
import it below, and add it to PROVIDERS (and the default sets if it
should run automatically for certain subtopic kinds). Nothing in
research/pipeline.py needs to change.
"""
from __future__ import annotations

from .arxiv import ArxivSourceProvider
from .base import SourceProvider
from .github import GitHubSourceProvider
from .web import NewsSourceProvider, WebSourceProvider
from .wikipedia import WikipediaSourceProvider

PROVIDERS: dict[str, SourceProvider] = {
    "web": WebSourceProvider(),
    "news": NewsSourceProvider(),
    "wikipedia": WikipediaSourceProvider(),
    "arxiv": ArxivSourceProvider(),
    "github": GitHubSourceProvider(),
}

# Providers used automatically for a general research query. "news" is
# deliberately excluded from the default set — it's opt-in per subtopic
# since most research topics aren't news-driven.
DEFAULT_PROVIDER_NAMES = ["web", "wikipedia", "arxiv", "github"]


def get_providers(names: list[str] | None = None) -> list[SourceProvider]:
    names = names or DEFAULT_PROVIDER_NAMES
    return [PROVIDERS[n] for n in names if n in PROVIDERS]


__all__ = ["SourceProvider", "PROVIDERS", "DEFAULT_PROVIDER_NAMES", "get_providers"]
