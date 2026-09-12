"""
actions/code_graph.py — voice/text-facing wrapper around code_graph.CodeGraphEngine.

Same calling convention as the other action modules (file_controller, reminder,
etc.): a single entry point taking `parameters`/`response`/`player`/`session_memory`
and returning a formatted string. Read-only — this module never writes to the
project; it only reports on it.
"""
from __future__ import annotations

from pathlib import Path

from code_graph import CodeGraphEngine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_engine: CodeGraphEngine | None = None


def _get_engine() -> CodeGraphEngine:
    global _engine
    if _engine is None:
        _engine = CodeGraphEngine(project_root=_PROJECT_ROOT)
    return _engine


def _resolve_or_message(eng: CodeGraphEngine, raw: str) -> tuple[str | None, str | None]:
    if not raw:
        return None, "Missing 'module' — give a file path (e.g. actions/file_controller.py) or module name."
    resolved = eng.resolve_module(raw)
    if not resolved:
        return None, f"Couldn't find a project module matching '{raw}'. Try 'build' first, or check the path."
    return resolved, None


def code_graph(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "stats").lower().strip()
    module_hint = params.get("module", "")

    if player:
        player.write_log(f"[code_graph] {action} {module_hint}".rstrip())

    eng = _get_engine()

    try:
        if action == "build":
            stats = eng.build(force=True)
            return (
                f"Rebuilt the codebase graph — {stats.module_count} modules, "
                f"{stats.edge_count} internal import edges, {stats.external_dependency_count} "
                f"distinct external dependencies."
            )

        if action == "stats":
            stats = eng.build()
            top_dep = ", ".join(f"{m} ({n})" for m, n in stats.most_depended_on[:5]) or "none"
            top_deps = ", ".join(f"{m} ({n})" for m, n in stats.most_dependencies[:5]) or "none"
            iso = ", ".join(stats.isolated_modules[:8]) or "none"
            more_iso = f" (+{len(stats.isolated_modules) - 8} more)" if len(stats.isolated_modules) > 8 else ""
            return (
                f"{stats.module_count} modules, {stats.edge_count} internal import edges, "
                f"{stats.external_dependency_count} external dependencies.\n"
                f"Most depended-on: {top_dep}\n"
                f"Most dependencies: {top_deps}\n"
                f"Isolated modules (no internal in/out edges): {iso}{more_iso}"
            )

        if action == "dependencies":
            resolved, err = _resolve_or_message(eng, module_hint)
            if err:
                return err
            deps = eng.dependencies(resolved)
            return f"{resolved} imports: {', '.join(deps) if deps else 'nothing project-internal'}."

        if action == "dependents":
            resolved, err = _resolve_or_message(eng, module_hint)
            if err:
                return err
            deps = eng.dependents(resolved)
            return f"Directly imported by: {', '.join(deps) if deps else 'nothing — no internal importers'}."

        if action == "impact":
            resolved, err = _resolve_or_message(eng, module_hint)
            if err:
                return err
            affected = eng.impact(resolved)
            if not affected:
                return f"Changing {resolved} would affect no other module in the project directly or indirectly."
            preview = ", ".join(affected[:15])
            more = f" (+{len(affected) - 15} more)" if len(affected) > 15 else ""
            return f"Changing {resolved} could ripple into {len(affected)} module(s): {preview}{more}"

        if action == "symbol":
            name = params.get("symbol_name", "") or module_hint
            if not name:
                return "Missing 'symbol_name' — the function or class name to look up."
            hits = eng.find_symbol(name)
            if not hits:
                return f"No function or class named '{name}' found at module top level anywhere in the project."
            lines = [f"{h['file']}:{h['line']} — {h['kind']} {h['module']}.{name}" for h in hits]
            return "\n".join(lines)

        if action == "symbols":
            resolved, err = _resolve_or_message(eng, module_hint)
            if err:
                return err
            syms = eng.list_symbols(resolved)
            if not syms:
                return f"{resolved} defines no top-level functions or classes."
            return "\n".join(f"{s['line']}: {s['kind']} {s['name']}" for s in syms)

        if action == "cycles":
            found = eng.cycles()
            if not found:
                return "No circular imports found."
            lines = [" -> ".join(c) for c in found]
            return f"{len(found)} circular import chain(s):\n" + "\n".join(lines)

        return (
            f"Unknown code_graph action: '{action}'. Use "
            "build | stats | dependencies | dependents | impact | symbol | symbols | cycles."
        )

    except Exception as e:
        return f"code_graph error: {e}"
