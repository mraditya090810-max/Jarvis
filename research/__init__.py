"""
research — headless Research Workspace pipeline for Mark-XLIX.

    from research import run_research, ResearchDepth
    session = run_research("quantum batteries", depth=ResearchDepth.STANDARD)
    print(session.report.executive_summary)

This package has no dependency on main.py, ui.py, or dashboard/server.py —
voice command wiring (Phase 3) and the workspace UI (Phase 4) both call
into run_research()/ResearchStore from here without this package knowing
either of them exists.
"""
from .models import (
    Contradiction, ExtractedImage, ExtractedPaper, MindMapNode,
    ResearchDepth, ResearchPlan, ResearchReport, ResearchSession,
    SessionStatus, Source, SourceTier, Subtopic, TimelineEvent,
)
from .pipeline import run_research
from .store import ResearchStore

__all__ = [
    "run_research",
    "ResearchStore",
    "ResearchSession",
    "ResearchPlan",
    "ResearchReport",
    "Source",
    "SourceTier",
    "Subtopic",
    "ExtractedPaper",
    "ExtractedImage",
    "Contradiction",
    "TimelineEvent",
    "MindMapNode",
    "ResearchDepth",
    "SessionStatus",
]
