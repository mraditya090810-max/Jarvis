"""
Smoke tests for the self_engineer package.

These import only stdlib + self_engineer (no PyQt6/sounddevice/etc.), so they
run in any environment — including inside the sandbox copies self_engineer
itself creates when testing a proposed patch.
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from self_engineer import security
from self_engineer.models import Severity, FileChange
from self_engineer.self_analyzer import SelfAnalyzer
from self_engineer.risk import assess
from self_engineer.models import RiskLevel


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_self_analyzer_finds_unused_import(tmp_path):
    _write(tmp_path, "sample.py", """
        import os
        import sys

        def hello():
            print(sys.argv)
    """)
    findings = SelfAnalyzer(tmp_path).analyze()
    categories = {f.category for f in findings}
    assert "unused_import" in categories
    unused = [f for f in findings if f.category == "unused_import"]
    assert any(f.description.startswith("Imported name 'os'") for f in unused)


def test_self_analyzer_finds_bare_except(tmp_path):
    _write(tmp_path, "sample2.py", """
        def risky():
            try:
                1 / 0
            except:
                pass
    """)
    findings = SelfAnalyzer(tmp_path).analyze()
    assert any(f.category == "bare_except" for f in findings)


def test_security_blocks_protected_paths(tmp_path):
    fc = FileChange(path="self_engineer/security.py", original_content="x = 1\n", new_content="x = 2\n")
    verdict = security.check_patch(tmp_path, [fc])
    assert not verdict.allowed
    assert any("protected path" in n for n in verdict.notes)


def test_security_allows_clean_change(tmp_path):
    fc = FileChange(path="ok.py", original_content="x = 1\n", new_content="x = 2\n")
    verdict = security.check_patch(tmp_path, [fc])
    assert verdict.allowed


def test_security_only_flags_newly_introduced_banned_calls(tmp_path):
    # Pre-existing (already shipped) winreg usage must not block an unrelated change.
    original = "import winreg\n\ndef read_key():\n    return winreg.OpenKey(0, 'x')\n"
    unrelated_change = original + "\n\ndef extra():\n    return 1\n"
    fc = FileChange(path="legacy.py", original_content=original, new_content=unrelated_change)
    verdict = security.check_patch(tmp_path, [fc])
    assert verdict.allowed, verdict.notes


def test_security_flags_newly_added_banned_call(tmp_path):
    original = "def f():\n    return 1\n"
    new = "import os\n\ndef f():\n    os.system('echo hi')\n    return 1\n"
    fc = FileChange(path="new_danger.py", original_content=original, new_content=new)
    verdict = security.check_patch(tmp_path, [fc])
    assert not verdict.allowed
    assert any("os.system" in n for n in verdict.notes)


def test_risk_assessment_flags_central_files():
    fc = FileChange(path="main.py", original_content="a\nb\nc\n", new_content="a\nb\nX\n")
    result = assess([fc], Severity.LOW)
    assert result.level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_risk_assessment_low_for_small_isolated_change():
    fc = FileChange(path="actions/weather_report.py", original_content="a\nb\n", new_content="a\nX\n")
    result = assess([fc], Severity.LOW)
    assert result.level == RiskLevel.LOW
