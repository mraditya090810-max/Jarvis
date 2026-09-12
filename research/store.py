"""
research/store.py — persistence layer for the Research Workspace pipeline.

Same convention as self_engineer/store.py: plain JSON under memory/, one
file per session, inspectable with a text editor, no database dependency.

Layout:
  memory/research_sessions/<session_id>.json  — full session state
  memory/research_sessions/index.json         — lightweight list for history UI
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import ResearchSession


class ResearchStore:
    def __init__(self, project_root: Path):
        self.root = project_root
        self.base = project_root / "memory" / "research_sessions"
        self.base.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base / "index.json"

    def _session_path(self, session_id: str) -> Path:
        return self.base / f"{session_id}.json"

    def save(self, session: ResearchSession) -> None:
        path = self._session_path(session.id)
        path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        self._update_index(session)

    def load(self, session_id: str) -> ResearchSession | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ResearchSession.from_dict(data)

    def list_sessions(self, n: int = 50) -> list[dict]:
        """Lightweight history list — id, query, status, timestamps only."""
        if not self.index_path.exists():
            return []
        try:
            entries = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
        return entries[-n:][::-1]   # most recent first

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        existed = path.exists()
        if existed:
            path.unlink()
        entries = []
        if self.index_path.exists():
            try:
                entries = json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries = [e for e in entries if e.get("id") != session_id]
        self.index_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        return existed

    def _update_index(self, session: ResearchSession) -> None:
        entries = []
        if self.index_path.exists():
            try:
                entries = json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries = [e for e in entries if e.get("id") != session.id]
        entries.append({
            "id": session.id,
            "query": session.query,
            "status": session.status.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "num_sources": len(session.sources),
            "num_papers": len(session.papers),
        })
        self.index_path.write_text(json.dumps(entries[-500:], indent=2), encoding="utf-8")
