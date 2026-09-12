"""
Shared data structures for the JARVIS Self-Engineering pipeline.

Every object here is a plain, JSON-serialisable dataclass so pipeline state
(pending patches, history, weaknesses) can be persisted to disk between runs
and inspected by a human without any special tooling.
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


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"   # never auto-suggested for apply; always blocked pending manual review


class PatchStatus(str, Enum):
    DRAFT = "draft"                     # generated, not yet through the pipeline
    BLOCKED_SECURITY = "blocked_security"
    FAILED_TESTS = "failed_tests"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


@dataclass
class Weakness:
    """A single finding produced by the Self Analyzer."""
    id: str
    category: str            # e.g. "large_function", "unused_import", "bare_except", "dead_code"
    file: str                # path relative to project root
    line_start: int
    line_end: int
    description: str
    severity: Severity = Severity.LOW

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class FileChange:
    """One file's proposed new content (full-file replace, not a line patch)."""
    path: str                # relative to project root
    original_content: str
    new_content: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkResult:
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Patch:
    """
    A full proposed change, tracked through the entire pipeline.
    Nothing in here is ever written to the real project until
    `status == AWAITING_APPROVAL` has been explicitly approved by the owner.
    """
    id: str
    weakness_id: str
    summary: str
    problem: str
    solution: str
    files: list           # list[FileChange]
    risk_level: RiskLevel
    complexity: str        # "trivial" | "small" | "moderate" | "large"
    expected_gain: str
    rollback_plan: str
    tests_added: list      # list[str] — test file paths generated for this patch
    diff_text: str = ""
    static_analysis_passed: Optional[bool] = None
    tests_passed: Optional[bool] = None
    benchmark: Optional[BenchmarkResult] = None
    status: PatchStatus = PatchStatus.DRAFT
    created_at: str = field(default_factory=_now_iso)
    applied_at: Optional[str] = None
    reject_reason: Optional[str] = None
    security_notes: list = field(default_factory=list)

    @staticmethod
    def new_id() -> str:
        return _new_id("patch")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["risk_level"] = self.risk_level.value
        d["status"] = self.status.value
        d["files"] = [f if isinstance(f, dict) else f.to_dict() for f in self.files]
        if self.benchmark is not None:
            d["benchmark"] = self.benchmark.to_dict() if hasattr(self.benchmark, "to_dict") else self.benchmark
        return d
