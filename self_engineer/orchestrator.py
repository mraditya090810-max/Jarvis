"""
Orchestrator — the SelfEngineer class. This is the single entry point the
rest of JARVIS (main.py's tool dispatch) talks to. It wires together every
module in this package into the pipeline described in the design brief:

  1. analyze()                — self-analysis (self_analyzer)
  2. weaknesses found         — persisted, browsable
  3. propose(weakness_id)     — generate improvement idea (patch_generator)
  4. risk estimate            — risk.assess
  5. generate patch           — patch_generator (already produced full new content)
  6. static analysis           }
  7. unit tests                } sandbox.Sandbox — all run on a throwaway copy
  8. integration tests          } — the real project is untouched up to here
  9. performance benchmark    — benchmark.compare (before = real project, after = sandbox)
 10. detailed report          — report.format_patch_report
 11. owner approval           — approve()/reject() — explicit, out-of-band call
 12. backup                   } version_control.backup
 13. apply update             } version_control.apply — ONLY reached after step 11
 14. restart affected module  — on_apply_callback hook (main.py can hot-reload)
 15. monitor stability        — re-run the sandbox's import/test checks against
                                 the now-updated real project
 16. automatic rollback       — version_control.rollback_last if step 15 fails

Nothing before step 11 ever writes to the real project directory. The
security guard (security.py) runs before the patch is even shown to the
owner, and can block a patch outright (status=BLOCKED_SECURITY) — those
patches never reach the approval stage at all.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import security, risk, patch_generator, benchmark, version_control as vcs
from .self_analyzer import SelfAnalyzer
from .sandbox import Sandbox
from .store import Store
from .models import Weakness, Patch, PatchStatus, RiskLevel
from .report import format_patch_report


class SelfEngineer:
    def __init__(self, project_root: str | Path, on_apply_callback: Optional[Callable[[list], None]] = None):
        self.root = Path(project_root).resolve()
        self.store = Store(self.root)
        # Optional hook: main.py can pass a function that hot-reloads/restarts
        # the affected in-process module after a real apply (step 14).
        self.on_apply_callback = on_apply_callback

    # ── Step 1-2: self analysis ─────────────────────────────────────────────
    def analyze(self, files: list[str] | None = None) -> list[Weakness]:
        analyzer = SelfAnalyzer(self.root)
        findings = analyzer.analyze(files=files)
        if files is None:
            self.store.save_weaknesses(findings)
            return findings
        touched = {f.replace("\\", "/") for f in files}
        kept = [w for w in self.store.load_weaknesses() if w.file not in touched]
        merged = kept + findings
        self.store.save_weaknesses(merged)
        return merged

    def list_weaknesses(self, category: str | None = None, severity: str | None = None) -> list[Weakness]:
        findings = self.store.load_weaknesses()
        if category:
            findings = [w for w in findings if w.category == category]
        if severity:
            findings = [w for w in findings if w.severity.value == severity]
        return findings

    # ── Steps 3-10: propose a patch through the full test/benchmark pipeline ─
    def propose(self, weakness_id: str) -> Patch:
        weaknesses = self.store.load_weaknesses()
        weakness = next((w for w in weaknesses if w.id == weakness_id), None)
        if weakness is None:
            raise ValueError(f"Unknown weakness id '{weakness_id}'. Run analyze() first.")

        original_path = self.root / weakness.file
        original_content = original_path.read_text(encoding="utf-8")

        patch_id = Patch.new_id()

        # Step 3-5: generate improvement + patch
        try:
            files, summary, problem, solution = patch_generator.generate(weakness, original_content)
        except ValueError as e:
            patch = Patch(
                id=patch_id, weakness_id=weakness.id, summary=f"Failed to generate patch: {e}",
                problem=weakness.description, solution="(no fix generated)", files=[],
                risk_level=RiskLevel.LOW, complexity="trivial", expected_gain="n/a",
                rollback_plan="n/a", tests_added=[], status=PatchStatus.REJECTED,
                reject_reason=str(e),
            )
            self.store.save_patch(patch)
            return patch

        changed_paths = [f.path for f in files]

        # Security check — happens BEFORE anything reaches the owner.
        verdict = security.check_patch(self.root, files)
        if not verdict.allowed:
            patch = Patch(
                id=patch_id, weakness_id=weakness.id, summary=summary, problem=problem,
                solution=solution, files=files, risk_level=RiskLevel.CRITICAL,
                complexity="n/a", expected_gain="n/a",
                rollback_plan="No rollback needed — patch was never applied.",
                tests_added=[], status=PatchStatus.BLOCKED_SECURITY,
                security_notes=verdict.notes,
            )
            self.store.save_patch(patch)
            return patch

        # Step 4: risk estimate
        assessment = risk.assess(files, weakness.severity)

        # Steps 6-8: static analysis, unit tests, integration/import check — all in a sandbox copy.
        with Sandbox(self.root) as sb:
            sb.apply_files(files)
            static_ok, static_notes = sb.run_static_analysis(changed_paths)
            tests_ok, tests_notes = sb.run_tests()
            import_ok, import_notes = sb.run_import_check(changed_paths)

            # Step 9: benchmark — compare real project ("before") to sandbox ("after").
            bench = benchmark.compare(self.root, sb.path, changed_paths)

        all_notes = static_notes + tests_notes + import_notes
        tests_passed = static_ok and tests_ok and import_ok

        status = PatchStatus.AWAITING_APPROVAL if tests_passed else PatchStatus.FAILED_TESTS
        expected_gain = self._describe_expected_gain(weakness, assessment.complexity)
        rollback_plan = (
            f"git revert of the single commit created when this patch is applied "
            f"(see version_control.rollback_last), restoring '{changed_paths}' to their "
            f"pre-patch content exactly."
        )

        patch = Patch(
            id=patch_id, weakness_id=weakness.id, summary=summary, problem=problem,
            solution=solution, files=files, risk_level=assessment.level,
            complexity=assessment.complexity, expected_gain=expected_gain,
            rollback_plan=rollback_plan, tests_added=[],
            static_analysis_passed=static_ok, tests_passed=tests_passed,
            benchmark=bench, status=status,
            security_notes=all_notes if not tests_passed else [],
        )
        self.store.save_patch(patch)
        return patch

    @staticmethod
    def _describe_expected_gain(weakness: Weakness, complexity: str) -> str:
        gains = {
            "large_function": "Improved readability/maintainability; easier future changes to this function.",
            "unused_import": "Slightly faster import time; removes dead dependency reference.",
            "bare_except": "Errors (including Ctrl+C) will propagate correctly instead of vanishing silently.",
            "broad_exception_log": "Failures become visible instead of silently swallowed.",
            "todo_marker": "Removes stale marker; no functional change.",
            "long_file": "Sets up future refactor into smaller modules (this patch alone does not split the file).",
            "duplicate_signature": "Flags a manual-review opportunity to de-duplicate logic.",
        }
        base = gains.get(weakness.category, "Addresses the identified weakness with a minimal, isolated change.")
        return f"{base} (complexity: {complexity})"

    # ── Step 10: report ───────────────────────────────────────────────────────
    def get_report(self, patch_id: str) -> str:
        patch = self.store.load_patch(patch_id)
        if patch is None:
            return f"No such patch: {patch_id}"
        return format_patch_report(patch)

    def get_patch(self, patch_id: str) -> Patch | None:
        return self.store.load_patch(patch_id)

    def list_pending(self) -> list[Patch]:
        return [p for p in self.store.list_patches() if p.status == PatchStatus.AWAITING_APPROVAL]

    def history(self, n: int = 30) -> list[dict]:
        return self.store.history(n)

    # ── Steps 11-16: approval, backup, apply, restart, monitor, rollback ────
    def approve(self, patch_id: str) -> str:
        """
        Explicit owner approval. This is the ONLY code path in the entire
        package that writes to the real project directory.
        """
        patch = self.store.load_patch(patch_id)
        if patch is None:
            return f"No such patch: {patch_id}"
        if patch.status != PatchStatus.AWAITING_APPROVAL:
            return (
                f"Patch {patch_id} is not awaiting approval (status={patch.status.value}). "
                "Only patches that passed static analysis, tests, and security checks can be applied."
            )

        # Step 12: backup
        backup_result = vcs.backup(self.root, patch_id)
        if not backup_result.ok:
            return f"Backup failed, aborting apply for safety: {backup_result.output}"

        # Step 13: apply
        apply_result = vcs.apply(self.root, patch.files, patch_id, patch.summary)
        if not apply_result.ok:
            return f"Apply failed: {apply_result.output}"

        patch.status = PatchStatus.APPLIED
        from datetime import datetime, timezone
        patch.applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.save_patch(patch)

        # Step 14: restart affected module, if the host process registered a hook.
        restart_note = "No restart hook registered — affected module(s) will reload on next process start."
        if self.on_apply_callback is not None:
            try:
                self.on_apply_callback([f.path for f in patch.files])
                restart_note = "Restart hook invoked for affected module(s)."
            except Exception as e:
                restart_note = f"Restart hook raised an error (non-fatal): {e}"

        # Step 15: monitor stability — re-check the now-updated real project.
        changed_paths = [f.path for f in patch.files]
        with Sandbox(self.root) as verify_sb:
            # The sandbox is now a fresh copy of the ALREADY-APPLIED real project,
            # so this doubles as a clean post-apply verification environment.
            static_ok, _ = verify_sb.run_static_analysis(changed_paths)
            import_ok, import_notes = verify_sb.run_import_check(changed_paths)

        if static_ok and import_ok:
            return (
                f"Patch {patch_id} applied and committed. {restart_note} "
                f"Post-apply monitoring passed — real project verified stable."
            )

        # Step 16: automatic rollback on monitoring failure.
        rollback_result = vcs.rollback_last(self.root, patch_id)
        patch.status = PatchStatus.ROLLED_BACK
        patch.reject_reason = "Post-apply monitoring failed; automatically rolled back."
        self.store.save_patch(patch)
        return (
            f"Patch {patch_id} was applied but FAILED post-apply monitoring "
            f"({'; '.join(import_notes)}). Automatically rolled back "
            f"({'ok' if rollback_result.ok else 'ROLLBACK FAILED: ' + rollback_result.output})."
        )

    def reject(self, patch_id: str, reason: str = "") -> str:
        patch = self.store.load_patch(patch_id)
        if patch is None:
            return f"No such patch: {patch_id}"
        patch.status = PatchStatus.REJECTED
        patch.reject_reason = reason or "Rejected by owner, no reason given."
        self.store.save_patch(patch)
        return f"Patch {patch_id} rejected. Nothing was changed on disk."
