"""
Sandbox — every proposed patch is applied to a throwaway COPY of the project
first. Static analysis, unit tests, and benchmarks all run against that copy.
The real project directory is never touched until the owner has approved the
patch (see version_control.py / orchestrator.py).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import py_compile
from dataclasses import dataclass, field
from pathlib import Path

_EXCLUDE_DIRS = {
    "venv", ".venv", "env", "__pycache__", ".git", "node_modules",
    "build", "dist", ".idea", ".vscode",
}


@dataclass
class SandboxResult:
    static_analysis_passed: bool
    static_analysis_notes: list = field(default_factory=list)
    tests_passed: bool = True
    tests_notes: list = field(default_factory=list)
    import_ok: bool = True
    import_notes: list = field(default_factory=list)


def _ignore(dir_path, names):
    return [n for n in names if n in _EXCLUDE_DIRS]


class Sandbox:
    """Context manager: creates a temp copy of the project, cleans it up after."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self.path: Path | None = None

    def __enter__(self) -> "Sandbox":
        self._tmpdir = tempfile.TemporaryDirectory(prefix="jarvis_self_engineer_")
        dest = Path(self._tmpdir.name) / "project"
        shutil.copytree(self.project_root, dest, ignore=_ignore)
        self.path = dest
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        return False

    def apply_files(self, files: list) -> None:
        """Write each FileChange's new_content into the sandbox copy."""
        assert self.path is not None
        for f in files:
            rel = f["path"] if isinstance(f, dict) else f.path
            content = f["new_content"] if isinstance(f, dict) else f.new_content
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def run_static_analysis(self, changed_paths: list) -> tuple[bool, list]:
        """py_compile every changed .py file — catches syntax errors immediately."""
        assert self.path is not None
        notes = []
        ok = True
        for rel in changed_paths:
            if not rel.endswith(".py"):
                continue
            target = self.path / rel
            try:
                py_compile.compile(str(target), doraise=True)
                notes.append(f"OK: {rel} compiles cleanly.")
            except py_compile.PyCompileError as e:
                ok = False
                notes.append(f"FAIL: {rel} — {e.msg}")
        return ok, notes

    def run_tests(self, timeout: int = 60) -> tuple[bool, list]:
        """Run pytest inside the sandbox copy, if a tests/ dir exists."""
        assert self.path is not None
        tests_dir = self.path / "tests"
        if not tests_dir.exists() or not any(tests_dir.glob("test_*.py")):
            return True, ["No tests/ directory or no test_*.py files found — skipping (nothing to run)."]

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests", "-q", "--maxfail=5"],
                cwd=str(self.path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            passed = proc.returncode == 0
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
            return passed, [tail]
        except subprocess.TimeoutExpired:
            return False, [f"pytest timed out after {timeout}s"]
        except FileNotFoundError:
            return True, ["pytest is not installed in this environment — skipping test execution."]

    def run_import_check(self, changed_paths: list, timeout: int = 20) -> tuple[bool, list]:
        """
        Integration smoke test: try importing each changed module in a fresh
        subprocess (isolated interpreter) so an import-time crash is caught
        before it ever reaches the running assistant.
        """
        assert self.path is not None
        notes = []
        ok = True
        for rel in changed_paths:
            if not rel.endswith(".py"):
                continue
            module = rel[:-3].replace("/", ".").replace("\\", ".")
            if module.endswith("__init__"):
                module = module.rsplit(".", 1)[0]
            code = f"import sys; sys.path.insert(0, r'{self.path}'); import {module}"
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True, text=True, timeout=timeout,
                )
                if proc.returncode != 0:
                    ok = False
                    notes.append(f"FAIL importing {module}: {proc.stderr.strip()[-300:]}")
                else:
                    notes.append(f"OK: {module} imports cleanly in isolation.")
            except subprocess.TimeoutExpired:
                ok = False
                notes.append(f"FAIL importing {module}: timed out after {timeout}s")
        return ok, notes
