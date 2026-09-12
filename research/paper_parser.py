"""
research/paper_parser.py — "Read research papers" stage: turns a Source
(with fetched text) that looks like an academic paper into a structured
ExtractedPaper, plus an AI-generated summary.
"""
from __future__ import annotations

import json
import re

from .models import ExtractedPaper, Source

_SECTION_SYSTEM = (
    "You are extracting structured fields from an academic paper's text. "
    "Respond with ONLY valid JSON, no prose, no markdown fences, in this "
    "exact shape (use empty string for anything not found):\n"
    '{"authors": ["..."], "publication": "...", "abstract": "...", '
    '"method": "...", "results": "...", "discussion": "...", '
    '"conclusion": "...", "future_work": "...", "doi": "..."}'
)

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def looks_like_paper(source: Source) -> bool:
    """Cheap heuristic — arXiv is always a paper; otherwise look for
    academic signal words in the fetched text."""
    if source.provider == "arxiv":
        return True
    text = (source.extracted_text or "")[:3000].lower()
    signals = ("abstract", "doi:", "references", "et al.", "methodology")
    return sum(1 for s in signals if s in text) >= 2


def parse_paper(source: Source) -> ExtractedPaper:
    text = source.extracted_text or source.snippet or ""
    doi_match = _DOI_RE.search(text)
    doi = doi_match.group(0) if doi_match else ""

    fields = {
        "authors": [], "publication": "", "abstract": source.snippet or "",
        "method": "", "results": "", "discussion": "", "conclusion": "",
        "future_work": "", "doi": doi,
    }

    try:
        from core.ai import call_llm_text
        raw = call_llm_text(
            text[:8000],
            system=_SECTION_SYSTEM,
            task="paper_extraction",
        )
        parsed = _extract_json(raw)
        if parsed:
            for key in fields:
                if key in parsed and parsed[key]:
                    fields[key] = parsed[key]
    except Exception as e:
        print(f"[research/paper_parser] ⚠️ AI section extraction failed for {source.url}: {e}")

    summary = ""
    try:
        from core.ai import call_llm_text
        summary = call_llm_text(
            f"Title: {source.title}\n\nAbstract/Text: {(fields['abstract'] or text)[:6000]}",
            system="Summarize this paper's contribution in 3-4 plain-language sentences.",
            task="paper_summary",
        )
    except Exception as e:
        print(f"[research/paper_parser] ⚠️ AI summary failed for {source.url}: {e}")

    citation = _format_citation(source.title, fields["authors"], fields["publication"], fields["doi"])

    return ExtractedPaper(
        id=ExtractedPaper.new_id(),
        source_url=source.url,
        title=source.title,
        authors=fields["authors"] if isinstance(fields["authors"], list) else [],
        publication=fields["publication"],
        abstract=fields["abstract"],
        method=fields["method"],
        results=fields["results"],
        discussion=fields["discussion"],
        conclusion=fields["conclusion"],
        future_work=fields["future_work"],
        doi=fields["doi"],
        citation=citation,
        ai_summary=summary.strip(),
    )


def _format_citation(title: str, authors: list, publication: str, doi: str) -> str:
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "") if authors else ""
    parts = [p for p in (author_str, f'"{title}"' if title else "", publication) if p]
    citation = ". ".join(parts)
    if doi:
        citation += f". DOI: {doi}"
    return citation.strip(". ")


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None
