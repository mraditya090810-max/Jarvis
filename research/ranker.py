"""
research/ranker.py — "Rank sources" stage.

Ranks by confidence tier (official docs > peer reviewed > government >
university > technical docs > industry > community > blog), matching the
priority list in the feature spec. Ties broken by whether the source was
actually fetched successfully (a source we couldn't read is worth less
than one we could, regardless of tier).
"""
from __future__ import annotations

from .models import SOURCE_TIER_ORDER, Source


def rank_sources(sources: list[Source]) -> list[Source]:
    def sort_key(s: Source):
        tier_rank = SOURCE_TIER_ORDER.index(s.tier) if s.tier in SOURCE_TIER_ORDER else len(SOURCE_TIER_ORDER)
        fetched_rank = 0 if s.fetched and not s.error else 1
        return (tier_rank, fetched_rank)

    return sorted(sources, key=sort_key)


def confidence_score(sources: list[Source]) -> float:
    """0-1 score for the whole session — higher when more sources are
    high-tier and successfully fetched."""
    if not sources:
        return 0.0
    total = 0.0
    for s in sources:
        tier_rank = SOURCE_TIER_ORDER.index(s.tier) if s.tier in SOURCE_TIER_ORDER else len(SOURCE_TIER_ORDER)
        tier_score = 1.0 - (tier_rank / max(1, len(SOURCE_TIER_ORDER) - 1))
        fetch_score = 1.0 if (s.fetched and not s.error) else 0.3
        total += tier_score * fetch_score
    return round(min(1.0, total / len(sources)), 2)
