"""
JARVIS Self-Engineering System
===============================

An autonomous-but-supervised software engineering pipeline that lets JARVIS
analyze its own source code, propose fixes, test and benchmark them in an
isolated sandbox, and — only after explicit owner approval — apply them to
the real project with git-based backup and automatic rollback on failure.

Nothing in this package writes to the real project directory except
`SelfEngineer.approve()`, which runs only after a human has reviewed a
patch report and explicitly approved that specific patch ID.

Entry point:
    from self_engineer.orchestrator import SelfEngineer
    engineer = SelfEngineer(project_root=".")
    weaknesses = engineer.analyze()
    patch = engineer.propose(weaknesses[0].id)
    print(engineer.get_report(patch.id))
    # ... human reviews the report ...
    engineer.approve(patch.id)   # or engineer.reject(patch.id, "not needed")
"""
from .orchestrator import SelfEngineer  # noqa: F401

__all__ = ["SelfEngineer"]
