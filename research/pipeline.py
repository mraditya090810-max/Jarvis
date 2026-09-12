"""
research/pipeline.py — orchestrates the full pipeline from the spec:

  Understand request -> Create research plan -> Split into subtopics ->
  Generate search queries -> Search multiple sources -> Read pages ->
  Extract text -> Extract images -> Read PDFs -> Read research papers ->
  Compare sources -> Detect contradictions -> Verify claims -> Rank sources
  -> Generate citations -> Create structured report -> Save session

This module is headless and voice/UI-agnostic on purpose (per the phased
build order): it exposes run_research() as a plain function so Phase 3
(voice commands) and Phase 4 (workspace UI) can both call into it without
duplicating pipeline logic. An optional `on_progress` callback lets either
of those layers show live status without this module knowing about voice
or UI at all.

Search and page-fetch stages run in parallel via a thread pool (network-
bound work, not CPU-bound, so threads are the right tool here and match
the pattern already used in actions/web_search.py's _news()). A
process-lifetime URL cache avoids re-downloading a page seen earlier in
the same run or a prior subtopic's search results.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from . import fetcher, planner, report_builder, verifier
from .models import (
    ExtractedImage, ExtractedPaper, ResearchDepth, ResearchSession, Source,
    SessionStatus,
)
from .paper_parser import looks_like_paper, parse_paper
from .ranker import rank_sources
from .sources import get_providers
from .store import ResearchStore

_MAX_SEARCH_WORKERS = 6
_MAX_FETCH_WORKERS = 6
_MAX_PAPERS_PARSED = 8    # cap AI-heavy paper parsing per run, regardless of depth

ProgressCB = Optional[Callable[[str, str], None]]   # (stage, message) -> None


def _emit(cb: ProgressCB, stage: str, message: str) -> None:
    if cb:
        try:
            cb(stage, message)
        except Exception:
            pass   # a broken progress callback must never break the pipeline
    print(f"[research/pipeline] [{stage}] {message}")


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def run_research(
    query: str,
    depth: ResearchDepth = ResearchDepth.STANDARD,
    provider_names: list[str] | None = None,
    max_sources: int | None = None,
    on_progress: ProgressCB = None,
    session_id: str | None = None,
) -> ResearchSession:
    """Runs the entire pipeline synchronously and returns the saved
    ResearchSession. Callers that want non-blocking behavior should run
    this in their own worker thread — kept out of this function so it
    stays trivially testable."""
    store = ResearchStore(_get_project_root())
    session = ResearchSession(id=session_id or ResearchSession.new_id(), query=query)

    try:
        _emit(on_progress, "understand", f"Understanding request: {query!r}")
        session.status = SessionStatus.PLANNING
        store.save(session)

        plan = planner.create_plan(query, depth=depth, max_sources=max_sources)
        session.plan = plan
        _emit(on_progress, "plan", f"Plan created: {len(plan.subtopics)} subtopics")
        store.save(session)

        session.status = SessionStatus.SEARCHING
        sources = _search_all(plan, provider_names, on_progress)
        sources = _dedupe_sources(sources)[: plan.max_sources]
        session.sources = sources
        _emit(on_progress, "search", f"Found {len(sources)} unique sources")
        store.save(session)

        session.status = SessionStatus.EXTRACTING
        _fetch_all(session.sources, on_progress)
        session.images = _collect_images(session.sources)
        store.save(session)

        papers = _parse_papers(session.sources, on_progress)
        session.papers = papers
        store.save(session)

        session.status = SessionStatus.VERIFYING
        _emit(on_progress, "verify", "Checking sources for contradictions")
        contradictions = verifier.detect_contradictions(session.sources)
        store.save(session)

        session.sources = rank_sources(session.sources)
        _emit(on_progress, "rank", "Sources ranked by confidence tier")

        session.status = SessionStatus.REPORTING
        _emit(on_progress, "report", "Building structured report")
        session.report = report_builder.build_report(
            query, session.sources, session.papers, contradictions, plan,
            on_progress=lambda stage, message: _emit(on_progress, stage, message),
        )

        session.status = SessionStatus.DONE
        store.save(session)
        _emit(on_progress, "done", "Research session complete")
        return session

    except Exception as e:
        session.status = SessionStatus.FAILED
        session.error = str(e)
        store.save(session)
        _emit(on_progress, "failed", f"Pipeline error: {e}")
        raise


def _search_all(plan, provider_names, on_progress: ProgressCB) -> list[Source]:
    providers = get_providers(provider_names)
    jobs = []
    for sub in plan.subtopics:
        for query in sub.queries:
            for provider in providers:
                jobs.append((sub, provider, query))

    results: list[Source] = []
    with ThreadPoolExecutor(max_workers=_MAX_SEARCH_WORKERS) as pool:
        futures = {
            pool.submit(_safe_provider_search, provider, query): (sub, provider)
            for sub, provider, query in jobs
        }
        for future in as_completed(futures):
            sub, provider = futures[future]
            found = future.result()
            for src in found:
                src.subtopic = sub.title
            results.extend(found)
            _emit(on_progress, "search", f"{provider.name}: {len(found)} results for '{sub.title}'")

    return results


def _safe_provider_search(provider, query: str) -> list[Source]:
    try:
        return provider.search(query)
    except Exception as e:
        print(f"[research/pipeline] ⚠️ provider {provider.name} raised unexpectedly: {e}")
        return []


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    seen = set()
    out = []
    for s in sources:
        if s.url in seen:
            continue
        seen.add(s.url)
        out.append(s)
    return out


def _fetch_all(sources: list[Source], on_progress: ProgressCB) -> None:
    with ThreadPoolExecutor(max_workers=_MAX_FETCH_WORKERS) as pool:
        futures = {pool.submit(fetcher.fetch_and_extract, s): s for s in sources}
        for future in as_completed(futures):
            s = futures[future]
            try:
                text, images = future.result()
                s.extracted_text = text
                s.fetched = True
                s._images = images   # stashed for _collect_images, not persisted directly
            except Exception as e:
                s.fetched = False
                s.error = str(e)
            _emit(on_progress, "extract", f"Fetched {s.url}")


def _collect_images(sources: list[Source]) -> list[ExtractedImage]:
    images: list[ExtractedImage] = []
    for s in sources:
        images.extend(getattr(s, "_images", []) or [])
    return images


def _parse_papers(sources: list[Source], on_progress: ProgressCB) -> list[ExtractedPaper]:
    candidates = [s for s in sources if s.fetched and looks_like_paper(s)][:_MAX_PAPERS_PARSED]
    papers: list[ExtractedPaper] = []
    for s in candidates:
        _emit(on_progress, "papers", f"Parsing paper: {s.title or s.url}")
        try:
            papers.append(parse_paper(s))
        except Exception as e:
            print(f"[research/pipeline] ⚠️ paper parse failed for {s.url}: {e}")
    return papers
