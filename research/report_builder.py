"""
research/report_builder.py — "Generate citations" + "Create structured
report" stages, plus the Timeline and Mind Map generators.

The narrative sections (executive summary, detailed report, key findings,
etc.) go through the AI router. The mind map is built deterministically
from the plan's subtopic → source structure so it always exists even if
every AI call in this run fails — a graph from data you already have is
more useful than no graph at all.
"""
from __future__ import annotations

import json
import re

from .models import (
    Contradiction, ExtractedPaper, MindMapNode, ResearchPlan, ResearchReport,
    Source, TimelineEvent,
)
from .ranker import confidence_score

_REPORT_SYSTEM = (
    "You are writing a structured research report from source excerpts. "
    "Base every claim only on the material given — do not invent facts. "
    "Respond with ONLY valid JSON, no prose, no markdown fences, in this "
    "exact shape:\n"
    '{"executive_summary": "...", "technical_summary": "...", '
    '"detailed_report": "...", "key_findings": ["..."], '
    '"advantages": ["..."], "limitations": ["..."], '
    '"future_research": "...", "conclusion": "..."}'
)

_TIMELINE_SYSTEM = (
    "Extract a chronological timeline of notable dated events from these "
    "source excerpts, if any exist. Respond with ONLY valid JSON:\n"
    '{"events": [{"date": "...", "label": "...", "detail": "...", "source_url": "..."}]}\n'
    'If no dated events are present, respond {"events": []}.'
)


def build_references(sources: list[Source], papers: list[ExtractedPaper]) -> list[str]:
    paper_urls = {p.source_url: p for p in papers}
    refs = []
    seen_urls = set()
    for s in sources:
        if s.url in seen_urls:
            continue
        seen_urls.add(s.url)
        if s.url in paper_urls and paper_urls[s.url].citation:
            refs.append(paper_urls[s.url].citation)
        else:
            refs.append(f"{s.title or s.url} — {s.url}")
    return refs


def build_mind_map(plan: ResearchPlan) -> list[MindMapNode]:
    root = MindMapNode(id="root", label=plan.query, parent_id=None)
    nodes = [root]
    for sub in plan.subtopics:
        nodes.append(MindMapNode(id=sub.id, label=sub.title, parent_id="root"))
    return nodes


def build_timeline(sources: list[Source], max_sources: int = 10) -> list[TimelineEvent]:
    usable = [s for s in sources if s.fetched and (s.extracted_text or s.snippet)][:max_sources]
    if not usable:
        return []

    excerpt_block = "\n\n".join(
        f"SOURCE: {s.url}\nEXCERPT: {(s.extracted_text or s.snippet)[:1000]}" for s in usable
    )
    try:
        from core.ai import call_llm_text
        raw = call_llm_text(excerpt_block, system=_TIMELINE_SYSTEM, task="timeline_extraction")
    except Exception as e:
        print(f"[research/report_builder] ⚠️ timeline extraction unavailable: {e}")
        return []

    parsed = _extract_json(raw)
    if not parsed or not isinstance(parsed.get("events"), list):
        return []

    out = []
    for item in parsed["events"]:
        try:
            out.append(TimelineEvent(
                date=str(item.get("date", "")),
                label=str(item["label"]),
                detail=str(item.get("detail", "")),
                source_url=str(item.get("source_url", "")),
            ))
        except (KeyError, TypeError):
            continue
    return out


def _extractive_fallback_summary(query: str, sources: list[Source], max_items: int = 5) -> str:
    """No-AI-required fallback: pulls the opening sentences straight out of
    the top-ranked fetched sources. Used when every free model fails, so
    "summary unavailable" never actually means "nothing to read" — the user
    still gets real, sourced content instead of just a pointer to a
    references list they have no way to open.
    """
    usable = [s for s in sources if s.fetched and (s.extracted_text or s.snippet)][:max_items]
    if not usable:
        return ""

    lines = [f"Sir, an AI-written summary wasn't available this run, so here's what the top sources say about '{query}':"]
    for i, s in enumerate(usable, 1):
        text = (s.extracted_text or s.snippet or "").strip()
        # First couple of sentences, not the whole 1500-char excerpt — this
        # is meant to be a quick read, not a wall of raw scraped text.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        snippet = " ".join(sentences[:2]).strip()[:400]
        title = s.title or s.url
        lines.append(f"{i}. {title}: {snippet}")
    return "\n".join(lines)


def build_report(
    query: str,
    sources: list[Source],
    papers: list[ExtractedPaper],
    contradictions: list[Contradiction],
    plan: ResearchPlan,
    on_progress=None,
) -> ResearchReport:
    usable = [s for s in sources if s.fetched and (s.extracted_text or s.snippet)]
    excerpt_block = "\n\n".join(
        f"SOURCE [{s.tier.value}]: {s.url}\nTITLE: {s.title}\n"
        f"EXCERPT: {(s.extracted_text or s.snippet)[:1500]}"
        for s in usable[:25]
    )

    narrative = {
        "executive_summary": "", "technical_summary": "", "detailed_report": "",
        "key_findings": [], "advantages": [], "limitations": [],
        "future_research": "", "conclusion": "",
    }

    failure_reason = ""
    if excerpt_block:
        try:
            from core.ai import call_llm_text
            raw = call_llm_text(
                f"Research topic: {query}\n\n{excerpt_block}",
                system=_REPORT_SYSTEM,
                task="report_generation",
            )
            parsed = _extract_json(raw)
            if parsed:
                for key in narrative:
                    if key in parsed and parsed[key]:
                        narrative[key] = parsed[key]
            else:
                # The model responded, but not with the JSON shape we asked
                # for — different failure mode than the AI call raising, so
                # surface it distinctly rather than folding it into a bare
                # "unavailable" message.
                failure_reason = "the AI response wasn't valid JSON"
        except Exception as e:
            # This used to be the ONLY place a report-generation failure
            # went — a bare print() that never reaches the activity log or
            # the user, so a real cause (rate limit, no free model, timeout,
            # bad API key, etc.) looked identical to "unavailable this run"
            # with no way to tell them apart. Surface the real reason both
            # to the log and into the fallback text below.
            failure_reason = str(e) or type(e).__name__
            print(f"[research/report_builder] ⚠️ report generation failed: {e}")

    if failure_reason and on_progress:
        try:
            on_progress("report_warning", f"AI narrative summary unavailable: {failure_reason}")
        except Exception:
            pass

    if not narrative["executive_summary"]:
        # Try an extractive (no-AI) fallback first — actual readable content
        # from the sources — before falling back further to a bare pointer
        # at the references list. This is what makes "give me the summary"
        # answerable even on a run where every free model failed.
        extractive = _extractive_fallback_summary(query, usable)
        if extractive:
            narrative["executive_summary"] = extractive
        else:
            reason_suffix = f" ({failure_reason})" if failure_reason else ""
            narrative["executive_summary"] = (
                f"Research on '{query}' gathered {len(sources)} sources across "
                f"{len(plan.subtopics)} subtopics. AI-generated narrative summary "
                f"was unavailable this run{reason_suffix} — see References for raw sources."
            )

    return ResearchReport(
        query=query,
        executive_summary=narrative["executive_summary"],
        technical_summary=narrative["technical_summary"],
        detailed_report=narrative["detailed_report"],
        key_findings=narrative["key_findings"] if isinstance(narrative["key_findings"], list) else [],
        advantages=narrative["advantages"] if isinstance(narrative["advantages"], list) else [],
        limitations=narrative["limitations"] if isinstance(narrative["limitations"], list) else [],
        future_research=narrative["future_research"],
        conclusion=narrative["conclusion"],
        references=build_references(sources, papers),
        confidence_score=confidence_score(sources),
        contradictions=contradictions,
        timeline=build_timeline(sources),
        mind_map=build_mind_map(plan),
    )


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
