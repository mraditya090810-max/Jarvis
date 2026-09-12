"""
research/models.py — shared data structures for the Research Workspace pipeline.

Same philosophy as self_engineer/models.py: plain, JSON-serialisable
dataclasses so a session (plan, sources, papers, images, report) can be
persisted to disk between runs and inspected by a human with a text editor.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class SourceTier(str, Enum):
    """Confidence ranking — lower value = more trusted."""
    OFFICIAL_DOCS = "official_docs"
    PEER_REVIEWED = "peer_reviewed"
    GOVERNMENT = "government"
    UNIVERSITY = "university"
    TECHNICAL_DOCS = "technical_docs"
    INDUSTRY = "industry"
    COMMUNITY = "community"
    BLOG = "blog"
    UNKNOWN = "unknown"


# Lower index = higher trust. Used by ranker.py.
SOURCE_TIER_ORDER = [
    SourceTier.OFFICIAL_DOCS,
    SourceTier.PEER_REVIEWED,
    SourceTier.GOVERNMENT,
    SourceTier.UNIVERSITY,
    SourceTier.TECHNICAL_DOCS,
    SourceTier.INDUSTRY,
    SourceTier.COMMUNITY,
    SourceTier.BLOG,
    SourceTier.UNKNOWN,
]


class ResearchDepth(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


@dataclass
class Source:
    """One discovered source (web page, doc, repo, paper landing page, etc)."""
    id: str
    url: str
    title: str
    snippet: str
    provider: str                    # which SourceProvider found it, e.g. "web", "arxiv", "github"
    subtopic: str = ""
    tier: SourceTier = SourceTier.UNKNOWN
    extracted_text: str = ""
    fetched: bool = False
    error: Optional[str] = None

    @staticmethod
    def new_id() -> str:
        return _new_id("src")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Source":
        d = dict(d)
        d["tier"] = SourceTier(d.get("tier", "unknown"))
        return Source(**d)


@dataclass
class ExtractedImage:
    id: str
    source_url: str
    image_url: str
    caption: str = ""
    kind: str = "figure"             # figure | chart | diagram | photo | infographic

    @staticmethod
    def new_id() -> str:
        return _new_id("img")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExtractedImage":
        return ExtractedImage(**d)


@dataclass
class ExtractedPaper:
    id: str
    source_url: str
    title: str
    authors: list = field(default_factory=list)
    publication: str = ""
    abstract: str = ""
    method: str = ""
    results: str = ""
    discussion: str = ""
    conclusion: str = ""
    future_work: str = ""
    doi: str = ""
    citation: str = ""
    ai_summary: str = ""

    @staticmethod
    def new_id() -> str:
        return _new_id("paper")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExtractedPaper":
        return ExtractedPaper(**d)


@dataclass
class Subtopic:
    id: str
    title: str
    queries: list = field(default_factory=list)   # generated search queries
    source_ids: list = field(default_factory=list)

    @staticmethod
    def new_id() -> str:
        return _new_id("sub")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Subtopic":
        return Subtopic(**d)


@dataclass
class ResearchPlan:
    query: str
    depth: ResearchDepth
    subtopics: list = field(default_factory=list)   # list[Subtopic]
    max_sources: int = 20
    max_time_s: int = 300

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "depth": self.depth.value,
            "subtopics": [s.to_dict() if isinstance(s, Subtopic) else s for s in self.subtopics],
            "max_sources": self.max_sources,
            "max_time_s": self.max_time_s,
        }

    @staticmethod
    def from_dict(d: dict) -> "ResearchPlan":
        return ResearchPlan(
            query=d["query"],
            depth=ResearchDepth(d.get("depth", "standard")),
            subtopics=[Subtopic.from_dict(s) for s in d.get("subtopics", [])],
            max_sources=d.get("max_sources", 20),
            max_time_s=d.get("max_time_s", 300),
        )


@dataclass
class Contradiction:
    claim_a: str
    claim_b: str
    source_a: str
    source_b: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimelineEvent:
    date: str            # free-form ("2023", "March 2024") — not always a full ISO date
    label: str
    detail: str = ""
    source_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MindMapNode:
    id: str
    label: str
    parent_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResearchReport:
    query: str
    executive_summary: str = ""
    technical_summary: str = ""
    detailed_report: str = ""
    key_findings: list = field(default_factory=list)
    advantages: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    future_research: str = ""
    conclusion: str = ""
    references: list = field(default_factory=list)     # list[str] formatted citations
    confidence_score: float = 0.0
    contradictions: list = field(default_factory=list)  # list[Contradiction]
    timeline: list = field(default_factory=list)        # list[TimelineEvent]
    mind_map: list = field(default_factory=list)        # list[MindMapNode]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "ResearchReport":
        d = dict(d)
        d["contradictions"] = [Contradiction(**c) for c in d.get("contradictions", [])]
        d["timeline"] = [TimelineEvent(**t) for t in d.get("timeline", [])]
        d["mind_map"] = [MindMapNode(**m) for m in d.get("mind_map", [])]
        return ResearchReport(**d)


class SessionStatus(str, Enum):
    PLANNING = "planning"
    SEARCHING = "searching"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    DONE = "done"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class ResearchSession:
    """Everything about one research run — the unit that gets saved/reopened."""
    id: str
    query: str
    status: SessionStatus = SessionStatus.PLANNING
    plan: Optional[ResearchPlan] = None
    sources: list = field(default_factory=list)      # list[Source]
    papers: list = field(default_factory=list)        # list[ExtractedPaper]
    images: list = field(default_factory=list)        # list[ExtractedImage]
    notes: str = ""
    report: Optional[ResearchReport] = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    error: Optional[str] = None

    @staticmethod
    def new_id() -> str:
        return _new_id("session")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "sources": [s.to_dict() for s in self.sources],
            "papers": [p.to_dict() for p in self.papers],
            "images": [i.to_dict() for i in self.images],
            "notes": self.notes,
            "report": self.report.to_dict() if self.report else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @staticmethod
    def from_dict(d: dict) -> "ResearchSession":
        return ResearchSession(
            id=d["id"],
            query=d["query"],
            status=SessionStatus(d.get("status", "planning")),
            plan=ResearchPlan.from_dict(d["plan"]) if d.get("plan") else None,
            sources=[Source.from_dict(s) for s in d.get("sources", [])],
            papers=[ExtractedPaper.from_dict(p) for p in d.get("papers", [])],
            images=[ExtractedImage.from_dict(i) for i in d.get("images", [])],
            notes=d.get("notes", ""),
            report=ResearchReport.from_dict(d["report"]) if d.get("report") else None,
            created_at=d.get("created_at", _now_iso()),
            updated_at=d.get("updated_at", _now_iso()),
            error=d.get("error"),
        )
