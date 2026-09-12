"""
research/verifier.py — "Compare sources" / "Detect contradictions" /
"Verify claims" stages.

Deliberately conservative: only flags a contradiction when the AI call
succeeds and returns a well-formed pair; any failure just means "no
contradictions detected this run" rather than blocking the pipeline.
"""
from __future__ import annotations

import json
import re

from .models import Contradiction, Source

_VERIFY_SYSTEM = (
    "You are fact-checking research sources against each other. Given "
    "excerpts from several sources on the same topic, identify factual "
    "contradictions between them (not just differences in emphasis or "
    "wording). Respond with ONLY valid JSON, no prose, no markdown fences:\n"
    '{"contradictions": [{"claim_a": "...", "claim_b": "...", '
    '"source_a": "<url>", "source_b": "<url>", "note": "..."}]}\n'
    'If there are no real contradictions, respond {"contradictions": []}.'
)


def detect_contradictions(sources: list[Source], max_sources: int = 8) -> list[Contradiction]:
    usable = [s for s in sources if s.fetched and (s.extracted_text or s.snippet)][:max_sources]
    if len(usable) < 2:
        return []

    excerpt_block = "\n\n".join(
        f"SOURCE: {s.url}\nTITLE: {s.title}\nEXCERPT: {(s.extracted_text or s.snippet)[:1200]}"
        for s in usable
    )

    try:
        from core.ai import call_llm_text
        raw = call_llm_text(excerpt_block, system=_VERIFY_SYSTEM, task="verification")
    except Exception as e:
        print(f"[research/verifier] ⚠️ AI verification unavailable: {e}")
        return []

    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("contradictions"), list):
        return []

    out = []
    for item in parsed["contradictions"]:
        try:
            out.append(Contradiction(
                claim_a=str(item["claim_a"]),
                claim_b=str(item["claim_b"]),
                source_a=str(item["source_a"]),
                source_b=str(item["source_b"]),
                note=str(item.get("note", "")),
            ))
        except (KeyError, TypeError):
            continue
    return out


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
