"""
Persistence layer for the self-engineering pipeline.

Everything lives under memory/self_engineer/ as plain JSON — inspectable by
a human with a text editor, no database dependency, consistent with the rest
of the project's memory/ store (memory/long_term.json).

Layout:
  memory/self_engineer/weaknesses.json   — last analyze() results
  memory/self_engineer/patches/<id>.json — one file per patch, any status
  memory/self_engineer/history.json      — append-only log of every status
                                            transition, for the "documentation
                                            of every change" requirement
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Weakness, Patch, Severity, RiskLevel, PatchStatus, FileChange, BenchmarkResult


class Store:
    def __init__(self, project_root: Path):
        self.root = project_root
        self.base = project_root / "memory" / "self_engineer"
        self.patches_dir = self.base / "patches"
        self.base.mkdir(parents=True, exist_ok=True)
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.weaknesses_path = self.base / "weaknesses.json"
        self.history_path = self.base / "history.json"

    # ── weaknesses ──────────────────────────────────────────────────────────
    def save_weaknesses(self, weaknesses: list[Weakness]) -> None:
        data = [w.to_dict() for w in weaknesses]
        self.weaknesses_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_weaknesses(self) -> list[Weakness]:
        if not self.weaknesses_path.exists():
            return []
        data = json.loads(self.weaknesses_path.read_text(encoding="utf-8"))
        return [
            Weakness(
                id=d["id"], category=d["category"], file=d["file"],
                line_start=d["line_start"], line_end=d["line_end"],
                description=d["description"], severity=Severity(d["severity"]),
            )
            for d in data
        ]

    # ── patches ─────────────────────────────────────────────────────────────
    def save_patch(self, patch: Patch) -> None:
        path = self.patches_dir / f"{patch.id}.json"
        path.write_text(json.dumps(patch.to_dict(), indent=2), encoding="utf-8")
        self._append_history(patch, note=f"status -> {patch.status.value}")

    def load_patch(self, patch_id: str) -> Patch | None:
        path = self.patches_dir / f"{patch_id}.json"
        if not path.exists():
            return None
        d = json.loads(path.read_text(encoding="utf-8"))
        files = [
            FileChange(path=f["path"], original_content=f["original_content"], new_content=f["new_content"])
            for f in d["files"]
        ]
        benchmark = None
        if d.get("benchmark"):
            benchmark = BenchmarkResult(before=d["benchmark"].get("before", {}), after=d["benchmark"].get("after", {}))
        return Patch(
            id=d["id"], weakness_id=d["weakness_id"], summary=d["summary"],
            problem=d["problem"], solution=d["solution"], files=files,
            risk_level=RiskLevel(d["risk_level"]), complexity=d["complexity"],
            expected_gain=d["expected_gain"], rollback_plan=d["rollback_plan"],
            tests_added=d.get("tests_added", []), diff_text=d.get("diff_text", ""),
            static_analysis_passed=d.get("static_analysis_passed"),
            tests_passed=d.get("tests_passed"), benchmark=benchmark,
            status=PatchStatus(d["status"]), created_at=d["created_at"],
            applied_at=d.get("applied_at"), reject_reason=d.get("reject_reason"),
            security_notes=d.get("security_notes", []),
        )

    def list_patches(self) -> list[Patch]:
        out = []
        for path in sorted(self.patches_dir.glob("*.json")):
            p = self.load_patch(path.stem)
            if p:
                out.append(p)
        return out

    def _append_history(self, patch: Patch, note: str) -> None:
        entries = []
        if self.history_path.exists():
            try:
                entries = json.loads(self.history_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "patch_id": patch.id,
            "summary": patch.summary,
            "note": note,
        })
        self.history_path.write_text(json.dumps(entries[-500:], indent=2), encoding="utf-8")

    def history(self, n: int = 30) -> list[dict]:
        if not self.history_path.exists():
            return []
        entries = json.loads(self.history_path.read_text(encoding="utf-8"))
        return entries[-n:]
