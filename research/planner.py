"""
research/planner.py — "Understand request" + "Create research plan" +
"Split into subtopics" + "Generate search queries" stages of the pipeline.

Uses core.ai.call_llm_text (the existing free-first AI router) so this
automatically benefits from whatever provider/fallback logic that module
has today, and from the multi-provider router once Phase 1 lands — no
change needed here when that happens.
"""
from __future__ import annotations

import json
import re

from .models import ResearchDepth, ResearchPlan, Subtopic

_DEPTH_SUBTOPIC_COUNT = {
    ResearchDepth.QUICK: 2,
    ResearchDepth.STANDARD: 4,
    ResearchDepth.DEEP: 6,
}

_DEPTH_MAX_SOURCES = {
    ResearchDepth.QUICK: 8,
    ResearchDepth.STANDARD: 20,
    ResearchDepth.DEEP: 40,
}

_DEPTH_MAX_TIME_S = {
    ResearchDepth.QUICK: 60,
    ResearchDepth.STANDARD: 300,
    ResearchDepth.DEEP: 900,
}

_PLAN_SYSTEM = (
    "You are a research planning assistant. Given a research topic, break it "
    "into distinct subtopics that together cover the topic well, and 2-3 "
    "concrete web-search queries per subtopic. Respond with ONLY valid JSON, "
    "no prose, no markdown fences, in this exact shape:\n"
    '{"subtopics": [{"title": "...", "queries": ["...", "..."]}]}'
)


def _fallback_plan(query: str, n_subtopics: int) -> list[Subtopic]:
    """Deterministic plan used if the AI call fails or returns bad JSON —
    the pipeline must never hard-fail just because planning couldn't use
    an LLM this run."""
    generic_angles = [
        "overview and background",
        "current state and recent developments",
        "advantages and limitations",
        "comparisons and alternatives",
        "real-world applications",
        "future outlook and open problems",
    ]
    subtopics = []
    for angle in generic_angles[:n_subtopics]:
        title = f"{query} — {angle}"
        subtopics.append(Subtopic(
            id=Subtopic.new_id(),
            title=title,
            queries=[f"{query} {angle}"],
        ))
    return subtopics


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # strip markdown fences if the model added them despite instructions
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        # last resort: grab the outermost {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None


def create_plan(
    query: str,
    depth: ResearchDepth = ResearchDepth.STANDARD,
    max_sources: int | None = None,
    max_time_s: int | None = None,
) -> ResearchPlan:
    n_subtopics = _DEPTH_SUBTOPIC_COUNT.get(depth, 4)

    subtopics: list[Subtopic] = []
    try:
        from core.ai import call_llm_text
        raw = call_llm_text(
            f"Research topic: {query}\nTarget subtopic count: {n_subtopics}",
            system=_PLAN_SYSTEM,
            task="research_planning",
        )
        parsed = _extract_json(raw)
        if parsed and isinstance(parsed.get("subtopics"), list):
            for item in parsed["subtopics"][:n_subtopics]:
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                queries = [str(q).strip() for q in item.get("queries", []) if str(q).strip()]
                if not queries:
                    queries = [title]
                subtopics.append(Subtopic(id=Subtopic.new_id(), title=title, queries=queries))
    except Exception as e:
        print(f"[research/planner] ⚠️ AI planning failed, using fallback plan: {e}")

    if not subtopics:
        subtopics = _fallback_plan(query, n_subtopics)

    return ResearchPlan(
        query=query,
        depth=depth,
        subtopics=subtopics,
        max_sources=max_sources or _DEPTH_MAX_SOURCES.get(depth, 20),
        max_time_s=max_time_s or _DEPTH_MAX_TIME_S.get(depth, 300),
    )
