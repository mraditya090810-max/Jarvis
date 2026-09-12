"""
research/sources/arxiv.py — academic paper search via arXiv's public
Atom API. No API key required. Marked as peer-reviewed-tier since arXiv
preprints are the closest free-to-query proxy for the "scientific papers"
category the pipeline needs; paper_parser.py refines actual per-paper
confidence later based on venue/DOI if present.
"""
from __future__ import annotations
import xml.etree.ElementTree as ET

from ..models import Source, SourceTier
from .base import SourceProvider

_API_URL = "http://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class ArxivSourceProvider(SourceProvider):
    name = "arxiv"
    default_tier = SourceTier.PEER_REVIEWED

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            import requests
        except Exception as e:
            print(f"[research/arxiv] ⚠️ requests unavailable: {e}")
            return []

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
        }
        try:
            resp = requests.get(_API_URL, params=params, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as e:
            print(f"[research/arxiv] ⚠️ search failed for {query!r}: {e}")
            return []

        out = []
        for entry in root.findall("atom:entry", _ATOM_NS):
            title_el = entry.find("atom:title", _ATOM_NS)
            summary_el = entry.find("atom:summary", _ATOM_NS)
            id_el = entry.find("atom:id", _ATOM_NS)
            if id_el is None:
                continue
            title = (title_el.text or "").strip() if title_el is not None else ""
            summary = (summary_el.text or "").strip() if summary_el is not None else ""
            out.append(Source(
                id=Source.new_id(),
                url=id_el.text.strip(),
                title=title,
                snippet=summary[:400],
                provider=self.name,
                tier=self.default_tier,
            ))
        return out
