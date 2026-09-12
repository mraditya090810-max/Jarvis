"""
research/sources/github.py — repository search via GitHub's public REST
API. Works unauthenticated at a low rate limit; if config/api_keys.json
has a "github_token" key it's used automatically for a much higher limit.
"""
from __future__ import annotations
import json
from pathlib import Path

from ..models import Source, SourceTier
from .base import SourceProvider

_API_URL = "https://api.github.com/search/repositories"


def _get_token() -> str | None:
    try:
        base = Path(__file__).resolve().parent.parent.parent
        cfg = json.loads((base / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return cfg.get("github_token") or None
    except Exception:
        return None


class GitHubSourceProvider(SourceProvider):
    name = "github"
    default_tier = SourceTier.TECHNICAL_DOCS

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            import requests
        except Exception as e:
            print(f"[research/github] ⚠️ requests unavailable: {e}")
            return []

        headers = {"Accept": "application/vnd.github+json"}
        token = _get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params = {"q": query, "sort": "stars", "order": "desc", "per_page": max_results}
        try:
            resp = requests.get(_API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[research/github] ⚠️ search failed for {query!r}: {e}")
            return []

        out = []
        for item in data.get("items", [])[:max_results]:
            out.append(Source(
                id=Source.new_id(),
                url=item.get("html_url", ""),
                title=item.get("full_name", ""),
                snippet=(item.get("description") or "")[:300],
                provider=self.name,
                tier=self.default_tier,
            ))
        return out
