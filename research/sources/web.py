"""
research/sources/web.py — general web search, reusing the existing DDG
helpers in actions/web_search.py instead of duplicating them.
"""
from __future__ import annotations

from ..models import Source, SourceTier
from .base import SourceProvider


class WebSourceProvider(SourceProvider):
    name = "web"
    default_tier = SourceTier.COMMUNITY

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            from actions.web_search import _ddg_search
        except Exception as e:
            print(f"[research/web] ⚠️ could not import web search backend: {e}")
            return []

        try:
            raw = _ddg_search(query, max_results=max_results)
        except Exception as e:
            print(f"[research/web] ⚠️ search failed for {query!r}: {e}")
            return []

        out = []
        for r in raw:
            if not r.get("url"):
                continue
            out.append(Source(
                id=Source.new_id(),
                url=r["url"],
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                provider=self.name,
                tier=self.default_tier,
            ))
        return out


class NewsSourceProvider(SourceProvider):
    name = "news"
    default_tier = SourceTier.INDUSTRY

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            from actions.web_search import _ddg_news
        except Exception as e:
            print(f"[research/news] ⚠️ could not import news backend: {e}")
            return []

        try:
            raw = _ddg_news(query, max_results=max_results)
        except Exception as e:
            print(f"[research/news] ⚠️ news search failed for {query!r}: {e}")
            return []

        out = []
        for r in raw:
            if not r.get("url"):
                continue
            out.append(Source(
                id=Source.new_id(),
                url=r["url"],
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                provider=self.name,
                tier=self.default_tier,
            ))
        return out
