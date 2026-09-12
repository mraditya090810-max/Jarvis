"""
Builds minimal, relevant context for a coding request using Code Graph,
instead of sending whole files or the whole repository.

This is an optional utility — existing callers with their own working
context assembly (e.g. dev_agent's own dependency-aware prompt builder) are
not required to switch to it. It's provided so Self Engineer and any future
AI Router caller can pull in "only what's relevant" (imports, dependents,
symbols, impact) the same way, without duplicating Code Graph traversal
logic in every module that needs it.
"""
from __future__ import annotations

from .prompt_optimizer import dedupe_context_blocks


def build_context(
    graph,
    target_modules: list[str],
    max_symbols_per_module: int = 25,
) -> str:
    """
    graph: a code_graph.engine.CodeGraphEngine instance (already built).
    target_modules: dotted module names the request is about (changed files,
        files that errored, etc.)

    Returns a compact text block containing, for each target module:
      - its direct dependencies (what it imports)
      - its dependents (what would be impacted by changing it)
      - its top-level symbols (functions/classes), capped per module

    Duplicate blocks (the same module pulled in twice via two different
    target modules) are collapsed to one.
    """
    blocks: list[tuple[str, str]] = []

    for module in target_modules:
        resolved = graph.resolve_module(module) or module
        try:
            deps = graph.dependencies(resolved)
        except Exception:
            deps = []
        try:
            dependents = graph.dependents(resolved)
        except Exception:
            dependents = []
        try:
            symbols = graph.list_symbols(resolved)[:max_symbols_per_module]
        except Exception:
            symbols = []

        sym_lines = "\n".join(
            f"  - {s.get('kind', 'symbol')} {s.get('name', '?')}"
            + (f" (line {s['line']})" if s.get("line") else "")
            for s in symbols
        )

        content = (
            f"Module: {resolved}\n"
            f"Depends on: {', '.join(deps) if deps else '(none)'}\n"
            f"Depended on by: {', '.join(dependents) if dependents else '(none)'}\n"
            f"Symbols:\n{sym_lines if sym_lines else '  (none found)'}"
        )
        blocks.append((resolved, content))

    blocks = dedupe_context_blocks(blocks)
    return "\n\n".join(f"--- {label} ---\n{content}" for label, content in blocks)
