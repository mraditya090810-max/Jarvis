"""
research/sources/wikipedia.py — Wikipedia's public REST search API.
No API key required.
"""
from __future__ import annotations

from ..models import Source, SourceTier
from .base import SourceProvider

_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


class WikipediaSourceProvider(SourceProvider):
    name = "wikipedia"
    default_tier = SourceTier.UNIVERSITY

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            import requests
        except Exception as e:
            print(f"[research/wikipedia] ⚠️ requests unavailable: {e}")
            return []

        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": max_results,
        }
        try:
            resp = requests.get(_SEARCH_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[research/wikipedia] ⚠️ search failed for {query!r}: {e}")
            return []

        out = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "")
            if not title:
                continue
            snippet = item.get("snippet", "")
            # strip the <span class="searchmatch"> highlighting tags
            snippet = snippet.replace('<span class="searchmatch">', "").replace("</span>", "")
            url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
            out.append(Source(
                id=Source.new_id(),
                url=url,
                title=title,
                snippet=snippet,
                provider=self.name,
                tier=self.default_tier,
            ))
        return out
