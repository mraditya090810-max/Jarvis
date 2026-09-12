"""
Version Control — every applied patch is preceded by a git commit (the
"backup") and followed by another commit (the "update"), so rollback is
always a single, well-understood git operation, never a hand-written undo.

If the project directory isn't already a git repo, one is initialised the
first time this module runs — this is a prerequisite for the whole safe-patch
system, since "backup" and "rollback" are meaningless without version history.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class VCSResult:
    ok: bool
    output: str


def _run(root: Path, *args: str, timeout: int = 30) -> VCSResult:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        ok = proc.returncode == 0
        return VCSResult(ok=ok, output=(proc.stdout + proc.stderr).strip())
    except FileNotFoundError:
        return VCSResult(ok=False, output="git is not installed on this system.")
    except subprocess.TimeoutExpired:
        return VCSResult(ok=False, output="git command timed out.")


def ensure_repo(root: Path) -> VCSResult:
    git_dir = root / ".git"
    if git_dir.exists():
        return VCSResult(ok=True, output="Repository already initialised.")

    init = _run(root, "init")
    if not init.ok:
        return init
    _run(root, "config", "user.email", "jarvis-self-engineer@local")
    _run(root, "config", "user.name", "JARVIS Self-Engineer")
    add = _run(root, "add", "-A")
    if not add.ok:
        return add
    commit = _run(root, "commit", "-m", "Baseline snapshot before self-engineering pipeline was enabled")
    return commit


def backup(root: Path, patch_id: str) -> VCSResult:
    """Commit the current state of the real project as a pre-patch checkpoint."""
    ensure_repo(root)
    _run(root, "add", "-A")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _run(root, "commit", "--allow-empty", "-m", f"[self-engineer] backup before {patch_id} @ {ts}")


def apply(root: Path, files: list, patch_id: str, summary: str) -> VCSResult:
    """Write approved file changes to the REAL project and commit them."""
    for f in files:
        rel = f["path"] if isinstance(f, dict) else f.path
        content = f["new_content"] if isinstance(f, dict) else f.new_content
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    _run(root, "add", "-A")
    return _run(root, "commit", "-m", f"[self-engineer] {patch_id}: {summary}")


def rollback_last(root: Path, patch_id: str) -> VCSResult:
    """Revert the most recent commit (the applied patch) without losing history."""
    return _run(root, "revert", "--no-edit", "HEAD", "-m", f"[self-engineer] rollback of {patch_id}")


def log_tail(root: Path, n: int = 10) -> VCSResult:
    return _run(root, "log", f"-{n}", "--oneline")
