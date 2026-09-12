"""
research/sources/base.py — the contract every source provider implements.

Adding a new provider (e.g. a "government publications" or "PubMed" source)
means: create a new file in research/sources/, subclass SourceProvider,
register it in research/sources/__init__.py's PROVIDERS list. Nothing in
research/pipeline.py needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Source, SourceTier


class SourceProvider(ABC):
    """One searchable source type (web, academic, github, docs, ...)."""

    name: str = "base"
    default_tier: SourceTier = SourceTier.UNKNOWN

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[Source]:
        """Return normalized Source objects. Must never raise — on any
        internal failure, return an empty list and log a warning, so one
        broken provider never blocks the rest of the pipeline."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Override if a provider needs an API key / network check before
        being included in a run (e.g. missing config). Default: always on."""
        return True
