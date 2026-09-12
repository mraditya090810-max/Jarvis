"""
Tests for the code_graph package (Codebase Knowledge Engine).

Pure stdlib + code_graph — no PyQt6/sounddevice/google-genai, so these run
anywhere, same as test_self_engineer.py.
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code_graph import CodeGraphEngine
from code_graph.builder import build_graph, build_module_map


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _make_toy_project(root: Path) -> None:
    _write(root, "app.py", """
        from pkg.util import helper
        import pkg.models

        def main():
            return helper()
    """)
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/util.py", """
        from .models import Thing

        def helper():
            return Thing()
    """)
    _write(root, "pkg/models.py", """
        class Thing:
            pass
    """)
    _write(root, "standalone.py", """
        import os

        def unused_by_anyone():
            return os.getcwd()
    """)


def test_builder_resolves_absolute_and_relative_imports(tmp_path):
    _make_toy_project(tmp_path)
    nodes = build_graph(tmp_path)
    assert "pkg.util" in nodes["app"].imports
    assert "pkg.models" in nodes["app"].imports
    assert "pkg.models" in nodes["pkg.util"].imports


def test_builder_tracks_external_imports_separately(tmp_path):
    _make_toy_project(tmp_path)
    nodes = build_graph(tmp_path)
    assert "os" in nodes["standalone"].external_imports
    assert not nodes["standalone"].imports


def test_module_map_includes_init_as_package_name(tmp_path):
    _make_toy_project(tmp_path)
    mapping = build_module_map(tmp_path)
    assert mapping.get("pkg") == "pkg/__init__.py"


def test_engine_dependents_is_reverse_of_dependencies(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    assert "app" in eng.dependents("pkg.util")
    assert "pkg.util" in eng.dependents("pkg.models")
    assert "app" in eng.dependents("pkg.models") or "app" in eng.impact("pkg.models")


def test_engine_impact_is_transitive(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    affected = eng.impact("pkg.models")
    assert "pkg.util" in affected
    assert "app" in affected  # app doesn't import pkg.models's dependents directly but transitively


def test_engine_impact_empty_for_leaf_module(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    assert eng.impact("standalone") == []


def test_engine_find_symbol(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    hits = eng.find_symbol("Thing")
    assert any(h["module"] == "pkg.models" and h["kind"] == "class" for h in hits)


def test_engine_detects_cycle(tmp_path):
    _write(tmp_path, "a.py", "import b\n")
    _write(tmp_path, "b.py", "import a\n")
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    cycles = eng.cycles()
    assert any(set(c) >= {"a", "b"} for c in cycles)


def test_engine_no_false_cycle_in_toy_project(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    assert eng.cycles() == []


def test_engine_rebuilds_when_file_changes(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    assert "pkg.models" not in eng.dependencies("standalone")
    # standalone.py now imports pkg.models — mtime must move forward for the
    # staleness check to notice, so nudge it explicitly rather than relying
    # on wall-clock resolution during a fast test run.
    import os, time
    target = tmp_path / "standalone.py"
    target.write_text("import pkg.models\n", encoding="utf-8")
    os.utime(target, (time.time() + 2, time.time() + 2))
    assert "pkg.models" in eng.dependencies("standalone")


def test_engine_resolve_module_by_basename(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    eng.build()
    assert eng.resolve_module("models") == "pkg.models"
    assert eng.resolve_module("pkg/models.py") == "pkg.models"


def test_engine_stats_shape(tmp_path):
    _make_toy_project(tmp_path)
    eng = CodeGraphEngine(tmp_path)
    stats = eng.build()
    assert stats.module_count == 5  # app, pkg (__init__), pkg.util, pkg.models, standalone
    assert stats.edge_count >= 3
    assert "os" not in [m for m, _ in stats.most_dependencies]  # external deps never in module stats


def test_runs_against_real_project_without_crashing():
    """End-to-end sanity check against the actual Mark-XLIX source tree."""
    project_root = Path(__file__).resolve().parent.parent
    eng = CodeGraphEngine(project_root)
    stats = eng.build()
    assert stats.module_count > 10  # sanity: real project has many modules
    # self_engineer/orchestrator.py imports several sibling modules — spot check
    deps = eng.dependencies("self_engineer.orchestrator")
    assert any(d.startswith("self_engineer.") for d in deps)
    # main.py should show up as depending on file_controller
    assert "actions.file_controller" in eng.dependencies("main")
