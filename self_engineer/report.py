"""
Report formatting — turns a Patch object into the exact structure the brief
requires before owner approval: Problem / Solution / Risk / Files / Expected
gain / Tests / Rollback, plus the pipeline's test and benchmark results.
"""
from __future__ import annotations

from .models import Patch


def format_patch_report(patch: Patch) -> str:
    files_list = "\n".join(f"  - {f.path if hasattr(f, 'path') else f['path']}" for f in patch.files)
    tests_list = "\n".join(f"  - {t}" for t in patch.tests_added) or "  (none generated)"

    bench_lines = []
    if patch.benchmark:
        before = patch.benchmark.before if hasattr(patch.benchmark, "before") else patch.benchmark.get("before", {})
        after = patch.benchmark.after if hasattr(patch.benchmark, "after") else patch.benchmark.get("after", {})
        for mod in before:
            b = before.get(mod, {})
            a = after.get(mod, {})
            if "error" in b or "error" in a:
                bench_lines.append(f"  - {mod}: benchmark error (before={b.get('error')} after={a.get('error')})")
                continue
            bt = b.get("import_time_s")
            at = a.get("import_time_s")
            bm = b.get("peak_rss_kb")
            am = a.get("peak_rss_kb")
            delta_t = f"{(at - bt):+.4f}s" if bt is not None and at is not None else "n/a"
            delta_m = f"{(am - bm):+d} KB" if bm is not None and am is not None else "n/a"
            bench_lines.append(
                f"  - {mod}: import time {bt}s -> {at}s ({delta_t}), peak RSS {bm}KB -> {am}KB ({delta_m})"
            )
    bench_text = "\n".join(bench_lines) or "  (no benchmarkable modules)"

    security = "\n".join(f"  - {n}" for n in patch.security_notes) or "  (no issues)"

    return f"""\
================= SELF-ENGINEERING PATCH REPORT =================
Patch ID     : {patch.id}
Status       : {patch.status.value}
Created      : {patch.created_at}

PROBLEM
  {patch.problem}

SOLUTION
  {patch.solution}

RISK
  Level        : {patch.risk_level.value}
  Complexity   : {patch.complexity}

FILES CHANGED
{files_list}

EXPECTED GAIN
  {patch.expected_gain}

TESTS
{tests_list}
  Static analysis passed : {patch.static_analysis_passed}
  Tests passed           : {patch.tests_passed}

BENCHMARK (before -> after)
{bench_text}

SECURITY NOTES
{security}

ROLLBACK PLAN
  {patch.rollback_plan}
===================================================================
This patch has NOT been applied. Nothing on disk has changed.
To apply it, the owner must explicitly approve patch ID '{patch.id}'.
"""
