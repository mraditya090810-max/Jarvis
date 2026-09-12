"""
Risk Estimator — a deliberately simple, deterministic (non-LLM) scoring
function. Risk assessment is safety-critical, so it must be predictable and
auditable rather than left to a model's judgement.

Score is built from three signals:
  1. Blast radius   — how many files change, and how "central" they are
                       (main.py / core/ / memory/ count as central).
  2. Change size     — total lines added+removed vs. original file size.
  3. Weakness severity — a HIGH severity finding (e.g. syntax error, security
                          smell) pushes risk up even for a tiny diff.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import RiskLevel, Severity

CENTRAL_PATH_PREFIXES = ("main.py", "core/", "memory/", "ui.py")


@dataclass
class RiskAssessment:
    level: RiskLevel
    complexity: str
    reasons: list


def _line_delta(original: str, new: str) -> int:
    old_lines = original.splitlines()
    new_lines = new.splitlines()
    # Cheap symmetric-difference style size estimate; good enough for risk banding.
    return abs(len(new_lines) - len(old_lines)) + sum(
        1 for a, b in zip(old_lines, new_lines) if a != b
    )


def assess(files: list, weakness_severity: Severity) -> RiskAssessment:
    """
    files: list of FileChange-like objects/dicts with path/original_content/new_content.
    """
    reasons = []
    total_delta = 0
    central_touch = False

    for f in files:
        path = f["path"] if isinstance(f, dict) else f.path
        original = f["original_content"] if isinstance(f, dict) else f.original_content
        new = f["new_content"] if isinstance(f, dict) else f.new_content

        total_delta += _line_delta(original, new)
        if any(path.startswith(p) for p in CENTRAL_PATH_PREFIXES):
            central_touch = True

    n_files = len(files)

    if n_files <= 1 and total_delta <= 15:
        complexity = "trivial"
    elif n_files <= 2 and total_delta <= 60:
        complexity = "small"
    elif n_files <= 5 and total_delta <= 200:
        complexity = "moderate"
    else:
        complexity = "large"
        reasons.append(f"Large change: {n_files} files, ~{total_delta} changed lines.")

    level = RiskLevel.LOW
    if central_touch:
        level = RiskLevel.MEDIUM
        reasons.append("Touches a central module (main.py / core / memory / ui.py).")
    if complexity == "large":
        level = RiskLevel.HIGH
    if weakness_severity == Severity.HIGH:
        level = RiskLevel.HIGH
        reasons.append("Underlying weakness severity is HIGH.")
    if central_touch and complexity in ("moderate", "large"):
        level = RiskLevel.CRITICAL
        reasons.append("Central module + large/moderate change — requires extra scrutiny.")

    if not reasons:
        reasons.append("Small, isolated change to a non-central file.")

    return RiskAssessment(level=level, complexity=complexity, reasons=reasons)
