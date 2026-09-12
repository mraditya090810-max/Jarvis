"""
Codebase Knowledge Engine — a static, in-project dependency graph of JARVIS's
own source tree.

Answers questions like:
  * "What does actions/file_controller.py depend on?"
  * "What would break if I change self_engineer/security.py?" (blast radius)
  * "Where is `run_in_executor` used?" / "which file defines `SelfAnalyzer`?"
  * "Are there any circular imports?"

Pure stdlib `ast`, no execution of project code, no new dependencies —
same safety posture as self_engineer.self_analyzer. Recomputed on demand
(no persistent cache file needed: parsing ~40-100 small .py files takes a
fraction of a second) with a cheap mtime check so repeat calls in the same
session skip rebuilding when nothing has changed on disk.

>>> from code_graph import CodeGraphEngine
>>> eng = CodeGraphEngine(project_root=".")
>>> eng.stats()
"""
from __future__ import annotations

from .engine import CodeGraphEngine

__all__ = ["CodeGraphEngine"]
