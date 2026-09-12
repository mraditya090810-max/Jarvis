"""
Offline tests for the research/ package — no network access required.
Every network-facing call (core.ai.call_llm_text, requests.get) is mocked
or replaced with a fake, matching the style of tests/test_ai_router.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.models import (
    ExtractedPaper, ResearchDepth, ResearchPlan, ResearchSession, Source,
    SourceTier, Subtopic, SessionStatus,
)
from research.store import ResearchStore
from research import planner, ranker, report_builder, verifier
from research.paper_parser import looks_like_paper, parse_paper
from research.sources.base import SourceProvider


# ── models + store ──────────────────────────────────────────────────────────

def test_source_roundtrip_through_dict():
    s = Source(id="src_1", url="https://x.com", title="T", snippet="s",
               provider="web", tier=SourceTier.INDUSTRY)
    d = s.to_dict()
    assert d["tier"] == "industry"
    s2 = Source.from_dict(d)
    assert s2.tier == SourceTier.INDUSTRY
    assert s2.url == "https://x.com"


def test_session_roundtrip_through_store(tmp_path):
    store = ResearchStore(tmp_path)
    plan = ResearchPlan(query="q", depth=ResearchDepth.QUICK, subtopics=[
        Subtopic(id="sub_1", title="Overview", queries=["q overview"]),
    ])
    session = ResearchSession(id=ResearchSession.new_id(), query="q", plan=plan)
    session.sources.append(Source(id="src_1", url="https://x.com", title="T",
                                   snippet="s", provider="web"))
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.query == "q"
    assert loaded.plan.subtopics[0].title == "Overview"
    assert loaded.sources[0].url == "https://x.com"

    history = store.list_sessions()
    assert history[0]["id"] == session.id

    assert store.delete(session.id) is True
    assert store.load(session.id) is None


# ── planner ──────────────────────────────────────────────────────────────────

def test_planner_uses_ai_json_when_available(monkeypatch):
    import core.ai as ai_module

    def fake_call(prompt, system=None, task="generic", **kw):
        return (
            '{"subtopics": ['
            '{"title": "Background", "queries": ["topic background"]},'
            '{"title": "Applications", "queries": ["topic uses", "topic applications"]}'
            ']}'
        )

    monkeypatch.setattr(ai_module, "call_llm_text", fake_call)
    plan = planner.create_plan("quantum batteries", depth=ResearchDepth.QUICK)
    assert plan.query == "quantum batteries"
    assert len(plan.subtopics) == 2
    assert plan.subtopics[0].title == "Background"
    assert plan.subtopics[1].queries == ["topic uses", "topic applications"]


def test_planner_falls_back_when_ai_returns_garbage(monkeypatch):
    import core.ai as ai_module
    monkeypatch.setattr(ai_module, "call_llm_text", lambda *a, **kw: "not json at all")

    plan = planner.create_plan("solar panels", depth=ResearchDepth.STANDARD)
    assert plan.query == "solar panels"
    assert len(plan.subtopics) == 4          # STANDARD depth fallback count
    assert all(sp.queries for sp in plan.subtopics)


def test_planner_falls_back_when_ai_raises(monkeypatch):
    import core.ai as ai_module

    def boom(*a, **kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(ai_module, "call_llm_text", boom)
    plan = planner.create_plan("fusion energy", depth=ResearchDepth.DEEP)
    assert len(plan.subtopics) == 6


# ── ranker ───────────────────────────────────────────────────────────────────

def test_rank_sources_orders_by_tier():
    sources = [
        Source(id="a", url="a", title="a", snippet="", provider="web", tier=SourceTier.BLOG, fetched=True),
        Source(id="b", url="b", title="b", snippet="", provider="arxiv", tier=SourceTier.PEER_REVIEWED, fetched=True),
        Source(id="c", url="c", title="c", snippet="", provider="web", tier=SourceTier.OFFICIAL_DOCS, fetched=True),
    ]
    ranked = ranker.rank_sources(sources)
    assert [s.id for s in ranked] == ["c", "b", "a"]


def test_rank_sources_prefers_fetched_within_same_tier():
    sources = [
        Source(id="a", url="a", title="a", snippet="", provider="web", tier=SourceTier.INDUSTRY, fetched=False, error="timeout"),
        Source(id="b", url="b", title="b", snippet="", provider="web", tier=SourceTier.INDUSTRY, fetched=True),
    ]
    ranked = ranker.rank_sources(sources)
    assert ranked[0].id == "b"


def test_confidence_score_zero_for_no_sources():
    assert ranker.confidence_score([]) == 0.0


def test_confidence_score_higher_for_better_sources():
    weak = [Source(id="a", url="a", title="", snippet="", provider="web",
                    tier=SourceTier.BLOG, fetched=False)]
    strong = [Source(id="b", url="b", title="", snippet="", provider="web",
                      tier=SourceTier.OFFICIAL_DOCS, fetched=True)]
    assert ranker.confidence_score(strong) > ranker.confidence_score(weak)


# ── paper_parser ─────────────────────────────────────────────────────────────

def test_looks_like_paper_true_for_arxiv():
    s = Source(id="a", url="https://arxiv.org/abs/1234", title="T", snippet="",
               provider="arxiv")
    assert looks_like_paper(s) is True


def test_looks_like_paper_false_for_generic_blog():
    s = Source(id="a", url="https://blog.example.com/post", title="T",
               snippet="", provider="web", extracted_text="just a casual blog post about cats")
    assert looks_like_paper(s) is False


def test_parse_paper_extracts_fields(monkeypatch):
    import core.ai as ai_module

    calls = {"n": 0}

    def fake_call(prompt, system=None, task="generic", **kw):
        calls["n"] += 1
        if task == "paper_extraction":
            return (
                '{"authors": ["A. Author", "B. Writer"], "publication": "NeurIPS 2024", '
                '"abstract": "We study X.", "method": "We do Y.", "results": "Z improved.", '
                '"discussion": "", "conclusion": "X works.", "future_work": "More Y.", "doi": ""}'
            )
        return "This paper studies X and shows Z improved using method Y."

    monkeypatch.setattr(ai_module, "call_llm_text", fake_call)
    s = Source(id="a", url="https://arxiv.org/abs/1234", title="Great Paper",
               snippet="abstract text", provider="arxiv", extracted_text="full paper text " * 50)
    paper = parse_paper(s)
    assert isinstance(paper, ExtractedPaper)
    assert paper.publication == "NeurIPS 2024"
    assert "A. Author" in paper.citation
    assert paper.ai_summary
    assert calls["n"] == 2


def test_parse_paper_survives_ai_failure(monkeypatch):
    import core.ai as ai_module
    monkeypatch.setattr(ai_module, "call_llm_text", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    s = Source(id="a", url="https://arxiv.org/abs/1234", title="Paper",
               snippet="fallback abstract", provider="arxiv")
    paper = parse_paper(s)
    assert paper.abstract == "fallback abstract"
    assert paper.ai_summary == ""


# ── verifier ─────────────────────────────────────────────────────────────────

def test_detect_contradictions_needs_two_usable_sources():
    only_one = [Source(id="a", url="a", title="a", snippet="text", provider="web", fetched=True)]
    assert verifier.detect_contradictions(only_one) == []


def test_detect_contradictions_parses_ai_response(monkeypatch):
    import core.ai as ai_module

    def fake_call(prompt, system=None, task="generic", **kw):
        return (
            '{"contradictions": [{"claim_a": "X is safe", "claim_b": "X is dangerous", '
            '"source_a": "https://a.com", "source_b": "https://b.com", "note": "conflict"}]}'
        )

    monkeypatch.setattr(ai_module, "call_llm_text", fake_call)
    sources = [
        Source(id="a", url="https://a.com", title="A", snippet="X is safe", provider="web", fetched=True),
        Source(id="b", url="https://b.com", title="B", snippet="X is dangerous", provider="web", fetched=True),
    ]
    contradictions = verifier.detect_contradictions(sources)
    assert len(contradictions) == 1
    assert contradictions[0].source_a == "https://a.com"


# ── report_builder ───────────────────────────────────────────────────────────

def test_build_references_prefers_paper_citation():
    sources = [Source(id="a", url="https://arxiv.org/abs/1", title="Paper Title",
                       snippet="", provider="arxiv")]
    papers = [ExtractedPaper(id="p1", source_url="https://arxiv.org/abs/1",
                              title="Paper Title", citation="Author. \"Paper Title\". Venue")]
    refs = report_builder.build_references(sources, papers)
    assert refs == ['Author. "Paper Title". Venue']


def test_build_mind_map_has_root_and_subtopic_children():
    plan = ResearchPlan(query="AI safety", depth=ResearchDepth.STANDARD, subtopics=[
        Subtopic(id="sub_1", title="Alignment", queries=["q"]),
        Subtopic(id="sub_2", title="Governance", queries=["q"]),
    ])
    nodes = report_builder.build_mind_map(plan)
    assert nodes[0].id == "root"
    assert nodes[0].label == "AI safety"
    child_parents = {n.parent_id for n in nodes[1:]}
    assert child_parents == {"root"}


def test_build_report_falls_back_gracefully_with_no_sources(monkeypatch):
    plan = ResearchPlan(query="empty topic", depth=ResearchDepth.QUICK, subtopics=[])
    report = report_builder.build_report("empty topic", [], [], [], plan)
    assert "empty topic" in report.executive_summary
    assert report.confidence_score == 0.0


# ── pipeline (end-to-end with fake providers) ───────────────────────────────

class _FakeProvider(SourceProvider):
    name = "fake"
    default_tier = SourceTier.INDUSTRY

    def __init__(self, urls):
        self._urls = urls

    def search(self, query, max_results=5):
        return [
            Source(id=Source.new_id(), url=url, title=f"Result for {query}",
                   snippet="a snippet", provider=self.name, tier=self.default_tier)
            for url in self._urls
        ]


def test_full_pipeline_runs_end_to_end(tmp_path, monkeypatch):
    import core.ai as ai_module
    from research import pipeline as pipeline_module

    monkeypatch.setattr(
        ai_module, "call_llm_text",
        lambda prompt, system=None, task="generic", **kw: (
            '{"subtopics": [{"title": "Sub A", "queries": ["sub a query"]}]}'
            if task == "research_planning" else ""
        ),
    )
    monkeypatch.setattr(pipeline_module, "_get_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        pipeline_module, "get_providers",
        lambda names=None: [_FakeProvider(["https://example.com/page1"])],
    )
    monkeypatch.setattr(
        pipeline_module.fetcher, "fetch_and_extract",
        lambda source: ("some extracted page text", []),
    )

    progress_events = []
    session = pipeline_module.run_research(
        "test topic",
        depth=ResearchDepth.QUICK,
        on_progress=lambda stage, msg: progress_events.append(stage),
    )

    assert session.status == SessionStatus.DONE
    assert len(session.sources) == 1
    assert session.sources[0].fetched is True
    assert session.report is not None
    assert session.report.query == "test topic"
    assert "understand" in progress_events
    assert "done" in progress_events

    store = ResearchStore(tmp_path)
    reloaded = store.load(session.id)
    assert reloaded is not None
    assert reloaded.status == SessionStatus.DONE


def test_pipeline_marks_session_failed_on_unexpected_error(tmp_path, monkeypatch):
    from research import pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_get_project_root", lambda: tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(pipeline_module.planner, "create_plan", boom)

    try:
        pipeline_module.run_research("bad topic")
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass


def test_research_manager_cancel_marks_session_canceled(tmp_path, monkeypatch):
    import time
    import core.research_manager as rm_mod
    from research import ResearchDepth, SessionStatus
    import research.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_get_project_root", lambda: tmp_path)

    def fake_run_research(query, depth=None, provider_names=None, max_sources=None, on_progress=None, session_id=None):
        import research.pipeline as pipeline
        for i in range(10):
            pipeline._emit(on_progress, "working", f"step {i}")
            time.sleep(0.1)
        return None

    monkeypatch.setattr(rm_mod, "run_research", fake_run_research)

    manager = rm_mod.ResearchManager(project_root=tmp_path)
    session_id = manager.start(
        query="test query",
        depth=ResearchDepth.QUICK,
        on_progress=lambda sid, q, stage, msg: None,
    )

    time.sleep(0.15)
    result = manager.cancel(session_id)
    assert "cancellation requested" in result.lower()

    assert manager.wait_for_completion(session_id, timeout=5.0)

    session = manager.store.load(session_id)
    assert session is not None
    assert session.status == SessionStatus.CANCELED
    assert session.error == "Canceled by user."


def test_research_mode_cancel_updates_ui_and_returns_message(tmp_path, monkeypatch):
    class FakePlayer:
        def __init__(self):
            self.logs = []
            self.content = []
        def write_log(self, text):
            self.logs.append(text)
        def show_content(self, title, text):
            self.content.append((title, text))
        def report_research_progress(self, session_id, query, stage, message):
            pass

    fake_player = FakePlayer()
    from actions.research_mode import research_mode

    result = research_mode(parameters={"action": "cancel"}, player=fake_player)
    assert "No active research session" in result
    assert fake_player.logs
    assert fake_player.content


def test_research_manager_cancel_when_no_research_running_returns_message(tmp_path):
    import core.research_manager as rm_mod

    manager = rm_mod.ResearchManager(project_root=tmp_path)
    result = manager.cancel()
    assert "No active research session" in result


def test_research_manager_cancel_multiple_times(tmp_path, monkeypatch):
    import time
    import core.research_manager as rm_mod
    from research import ResearchDepth, SessionStatus
    import research.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_get_project_root", lambda: tmp_path)

    def fake_run_research(query, depth=None, provider_names=None, max_sources=None, on_progress=None, session_id=None):
        import research.pipeline as pipeline
        for i in range(10):
            pipeline._emit(on_progress, "working", f"step {i}")
            time.sleep(0.1)
        return None

    monkeypatch.setattr(rm_mod, "run_research", fake_run_research)

    manager = rm_mod.ResearchManager(project_root=tmp_path)
    session_id = manager.start(
        query="test query",
        depth=ResearchDepth.QUICK,
        on_progress=lambda sid, q, stage, msg: None,
    )

    time.sleep(0.15)
    first = manager.cancel(session_id)
    second = manager.cancel(session_id)

    assert "cancellation requested" in first.lower()
    assert "cancellation already requested" in second.lower()
    assert manager.wait_for_completion(session_id, timeout=5.0)

    session = manager.store.load(session_id)
    assert session.status == SessionStatus.CANCELED


def test_research_manager_can_start_new_session_immediately_after_cancel(tmp_path, monkeypatch):
    import time
    import core.research_manager as rm_mod
    from research import ResearchDepth
    import research.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_get_project_root", lambda: tmp_path)

    def fake_run_research(query, depth=None, provider_names=None, max_sources=None, on_progress=None, session_id=None):
        import research.pipeline as pipeline
        for i in range(5):
            pipeline._emit(on_progress, "working", f"step {i}")
            time.sleep(0.1)
        return None

    monkeypatch.setattr(rm_mod, "run_research", fake_run_research)

    manager = rm_mod.ResearchManager(project_root=tmp_path)
    old_id = manager.start(query="first", depth=ResearchDepth.QUICK, on_progress=lambda sid, q, stage, msg: None)
    time.sleep(0.15)
    cancel_result = manager.cancel(old_id)
    assert "cancellation requested" in cancel_result.lower()

    new_id = manager.start(query="second", depth=ResearchDepth.QUICK, on_progress=lambda sid, q, stage, msg: None)
    assert new_id != old_id
    assert manager.current_session_id == new_id
    assert new_id in manager._active_threads

    assert manager.wait_for_completion(old_id, timeout=5.0)
    assert manager.wait_for_completion(new_id, timeout=5.0)


if __name__ == "__main__":
    print("This test module uses pytest fixtures (tmp_path, monkeypatch) throughout "
          "— run with `pytest tests/test_research_pipeline.py`.")
