from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.paths import get_base_dir
from research import ResearchDepth, run_research
from research.models import ResearchSession, SessionStatus
from research.store import ResearchStore


class ResearchCancelled(Exception):
    pass


_manager_instance: ResearchManager | None = None


def get_research_manager(project_root: str | Path | None = None) -> "ResearchManager":
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ResearchManager(project_root=project_root)
    return _manager_instance


class ResearchManager:
    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or get_base_dir()).resolve()
        self.store = ResearchStore(self.project_root)
        self._active_threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.current_session_id: str | None = None
        self.research_mode_active: bool = False

    def start(
        self,
        query: str,
        depth: ResearchDepth | str = ResearchDepth.STANDARD,
        provider_names: list[str] | None = None,
        max_sources: int | None = None,
        on_progress: Callable[[str, str, str, str], None] | None = None,
        on_speak: Callable[[str], None] | None = None,
    ) -> str:
        query = (query or "").strip()
        if not query:
            raise ValueError("Missing research topic. Provide a non-empty query.")

        if isinstance(depth, str):
            try:
                depth = ResearchDepth(depth.lower())
            except ValueError:
                depth = ResearchDepth.STANDARD

        session = ResearchSession(id=ResearchSession.new_id(), query=query)
        session.status = SessionStatus.PLANNING
        session.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.save(session)

        def _emit_progress(stage: str, message: str) -> None:
            if on_progress:
                try:
                    on_progress(session.id, query, stage, message)
                except Exception:
                    pass

        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._run_session,
            args=(session.id, query, depth, provider_names, max_sources, _emit_progress, cancel_event, on_speak),
            daemon=True,
        )

        with self._lock:
            self._active_threads[session.id] = thread
            self._cancel_events[session.id] = cancel_event
            self.current_session_id = session.id
            self.research_mode_active = True

        thread.start()
        return session.id

    def cancel(self, session_id: str | None = None) -> str:
        session_id = session_id or self.current_session_id
        if not session_id:
            return "No active research session to cancel."

        with self._lock:
            cancel_event = self._cancel_events.get(session_id)

        session = self.store.load(session_id)
        if session is None:
            return f"Unknown session: {session_id}."

        if cancel_event is None:
            if session.status in (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.CANCELED):
                return f"Research session {session_id} is already {session.status.value}."
            session.status = SessionStatus.CANCELED
            session.error = "Canceled by user."
            session.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.store.save(session)
            return f"Research session {session_id} marked canceled."

        if cancel_event.is_set():
            return f"Research session {session_id} cancellation already requested."

        cancel_event.set()
        session.status = SessionStatus.CANCELED
        session.error = "Cancellation requested by user."
        session.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.save(session)

        return f"Research session {session_id} cancellation requested."

    def status(self, session_id: str | None = None) -> dict[str, str]:
        session_id = session_id or self.current_session_id
        if not session_id:
            return {"status": "no active session"}

        session = self.store.load(session_id)
        if session is None:
            return {"status": "unknown session", "session_id": session_id}

        active = session_id in self._active_threads and self._active_threads[session_id].is_alive()
        return {
            "session_id": session.id,
            "query": session.query,
            "status": session.status.value,
            "active": str(active),
            "error": session.error or "",
        }

    def history(self, n: int = 50) -> list[dict]:
        return self.store.list_sessions(n)

    def summary(self, session_id: str | None = None) -> str:
        session = self._find_session(session_id)
        if session is None:
            return "No research session found."

        if session.report:
            summary = session.report.executive_summary or session.report.technical_summary
            if summary:
                return summary
            return "Session has a report, but no executive summary is available."

        return f"Session {session.id} is {session.status.value}. No report is available yet."

    def export(self, session_id: str | None = None, path: str | None = None) -> str:
        session = self._find_session(session_id)
        if session is None:
            return "No research session found to export."

        export_path = Path(path) if path else self.project_root / "memory" / "research_sessions" / f"{session.id}-export.json"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        return f"Exported session {session.id} to {export_path}."

    def open_paper(self, session_id: str | None = None, paper_id: str | None = None) -> str:
        session = self._find_session(session_id)
        if session is None:
            return "No research session found."

        papers = session.papers or []
        if not papers:
            return "No parsed papers in this session."

        paper = self._find_item_by_id(papers, paper_id)
        if paper is None:
            paper = papers[0]

        if not getattr(paper, "source_url", ""):
            return "Paper has no source URL to open."

        webbrowser.open(paper.source_url)
        return f"Opened paper URL: {paper.source_url}"

    def open_image(self, session_id: str | None = None, image_id: str | None = None) -> str:
        session = self._find_session(session_id)
        if session is None:
            return "No research session found."

        images = session.images or []
        if not images:
            return "No extracted images in this session."

        image = self._find_item_by_id(images, image_id)
        if image is None:
            image = images[0]

        if not getattr(image, "image_url", ""):
            return "Image has no URL to open."

        webbrowser.open(image.image_url)
        return f"Opened image URL: {image.image_url}"

    def list_sources(self, session_id: str | None = None) -> str:
        """Returns the actual reference list (title + URL per source) so the
        user has something concrete to read/open even when the AI narrative
        summary is unavailable — previously this data was captured in
        session.report.references but no tool action ever exposed it, so
        JARVIS could only say "see references" with no way to actually show
        them."""
        session = self._find_session(session_id)
        if session is None:
            return "No research session found."

        refs = session.report.references if session.report else []
        if not refs:
            # Fall back to raw sources if the report itself never built
            # (e.g. still running, or failed before reaching that stage).
            refs = [f"{s.title or s.url} — {s.url}" for s in (session.sources or [])]
        if not refs:
            return "No sources are available for this session yet."

        lines = [f"{i}. {ref}" for i, ref in enumerate(refs, 1)]
        return "\n".join(lines)

    def open_sources(self, session_id: str | None = None, max_open: int = 5) -> str:
        """Opens up to max_open source URLs in the browser (most-trusted
        first — sources are already rank-sorted by the time the report is
        built). Answers "open all the sources" requests, which previously
        had no handler at all."""
        session = self._find_session(session_id)
        if session is None:
            return "No research session found."

        sources = session.sources or []
        urls = [s.url for s in sources if getattr(s, "url", "")]
        if not urls:
            return "No source URLs are available for this session."

        max_open = max(1, min(int(max_open or 5), 20))
        to_open = urls[:max_open]
        for url in to_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        remaining = len(urls) - len(to_open)
        msg = f"Opened {len(to_open)} source" + ("" if len(to_open) == 1 else "s") + " in your browser."
        if remaining > 0:
            msg += f" {remaining} more are available — ask to open more if you want them."
        return msg

    def save_session(self, session_id: str | None = None) -> str:
        session = self._find_session(session_id)
        if session is None:
            return "No research session found to save."
        self.store.save(session)
        return f"Saved session {session.id}."

    def restore_session(self, session_id: str) -> str:
        session = self.store.load(session_id)
        if session is None:
            return f"No research session found with id {session_id}."

        self.current_session_id = session.id
        return f"Restored research session {session.id}."

    def enter_mode(self) -> str:
        self.research_mode_active = True
        return "Research mode activated. You can start a new topic."

    def exit_mode(self) -> str:
        self.research_mode_active = False
        return "Research mode exited."

    def _find_session(self, session_id: str | None) -> ResearchSession | None:
        session_id = session_id or self.current_session_id
        if not session_id:
            return None
        return self.store.load(session_id)

    def _find_item_by_id(self, items: list, item_id: str | None):
        if not item_id:
            return None
        for item in items:
            if getattr(item, "id", None) == item_id:
                return item
        return None

    def _run_session(
        self,
        session_id: str,
        query: str,
        depth: ResearchDepth,
        provider_names: list[str] | None,
        max_sources: int | None,
        on_progress: Callable[[str, str], None] | None,
        cancel_event: threading.Event,
        on_speak: Callable[[str], None] | None = None,
    ) -> None:
        import research.pipeline as pipeline_module

        original_emit = pipeline_module._emit

        def _emit_with_cancel(cb: Callable[[str, str], None], stage: str, message: str) -> None:
            if cancel_event.is_set():
                raise ResearchCancelled("Research canceled by user.")
            return original_emit(cb, stage, message)

        pipeline_module._emit = _emit_with_cancel

        try:
            run_research(
                query,
                depth=depth,
                provider_names=provider_names,
                max_sources=max_sources,
                on_progress=on_progress,
                session_id=session_id,
            )
            self._speak_result(query, session_id, on_speak)
        except ResearchCancelled:
            session = self.store.load(session_id)
            if session:
                session.status = SessionStatus.CANCELED
                session.error = "Canceled by user."
                session.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self.store.save(session)
        except Exception:
            # run_research persists failed status before re-raising.
            self._speak_result(query, session_id, on_speak)
        finally:
            pipeline_module._emit = original_emit
            with self._lock:
                self._active_threads.pop(session_id, None)
                self._cancel_events.pop(session_id, None)
                # Keep current_session_id pointing at this session even after
                # it finishes — it doubles as "the session the user most
                # recently asked about", so a later "what's the result"
                # without an explicit session_id can still resolve to it.
                # (Previously this was reset to None here, which is why a
                # completed research session became unreachable the moment
                # it finished — status()/summary() calls with no session_id
                # fell through to "no active session".)
                self.research_mode_active = bool(self._active_threads)

    def _speak_result(
        self,
        query: str,
        session_id: str,
        on_speak: Callable[[str], None] | None,
    ) -> None:
        """Proactively announce that a background research session finished,
        instead of leaving the result sitting silently in the session store
        until the user happens to ask again."""
        if on_speak is None:
            return
        session = self.store.load(session_id)
        if session is None:
            return
        try:
            if session.status == SessionStatus.DONE and session.report:
                summary = session.report.executive_summary or session.report.technical_summary
                if summary:
                    on_speak(f"Sir, I've finished researching {query}. {summary}")
                else:
                    on_speak(f"Sir, I've finished researching {query}. The report is ready for you.")
            elif session.status == SessionStatus.FAILED:
                on_speak(f"Sir, my research on {query} ran into a problem: {session.error or 'unknown error'}.")
        except Exception:
            pass

    def wait_for_completion(self, session_id: str, timeout: float = 10.0) -> bool:
        thread = self._active_threads.get(session_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()
