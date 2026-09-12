"""
CodeGraphEngine — public API for the Codebase Knowledge Engine.

Caching model: the graph is rebuilt the first time it's queried, then kept
in memory for the lifetime of the process. A cheap directory-mtime check
(same idea as search_index's incremental scanner) invalidates the cache
automatically if any .py file under the project changed since the last
build, so a stale graph is never silently served — but repeated queries in
one session (e.g. self_engineer checking impact for several weaknesses in a
row) don't pay the parse cost more than once.
"""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from .builder import EXCLUDE_DIRS, build_graph
from .models import GraphStats, ModuleNode

# Avoid rglob+stat on every query — throttle to at most once per interval.
# A zero TTL ensures filesystem changes are detected immediately while still
# preserving the cached graph once it is rebuilt.
_FINGERPRINT_TTL = 0.0


class CodeGraphEngine:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self._nodes: dict[str, ModuleNode] = {}
        self._reverse: dict[str, set] = {}
        self._built = False
        self._last_fingerprint: int | None = None
        self._file_mtimes: dict[str, float] = {}
        self._fingerprint_checked_at: float = 0.0

    # ── build / cache ──────────────────────────────────────────────────────
    def _fingerprint(self) -> int:
        """Sum of mtimes of every .py file we'd scan — cheap staleness check."""
        now = time.monotonic()
        if (
            self._file_mtimes
            and now - self._fingerprint_checked_at < _FINGERPRINT_TTL
        ):
            return int(sum(self._file_mtimes.values()))

        total = 0.0
        new_mtimes: dict[str, float] = {}
        for path in self.root.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in path.relative_to(self.root).parts):
                continue
            try:
                m = path.stat().st_mtime
                new_mtimes[str(path)] = m
                total += m
            except OSError:
                continue
        self._file_mtimes = new_mtimes
        self._fingerprint_checked_at = now
        return int(total)

    def build(self, force: bool = False) -> "GraphStats":
        fp = self._fingerprint()
        if not force and self._built and fp == self._last_fingerprint:
            return self.stats()
        self._nodes = build_graph(self.root)
        self._reverse = {m: set() for m in self._nodes}
        for m, node in self._nodes.items():
            for dep in node.imports:
                self._reverse.setdefault(dep, set()).add(m)
        self._built = True
        self._last_fingerprint = fp
        return self.stats()

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()
        else:
            # cheap staleness check on every query, same as search_index's freshness throttle
            fp = self._fingerprint()
            if fp != self._last_fingerprint:
                self.build(force=True)

    # ── queries ─────────────────────────────────────────────────────────────
    def dependencies(self, module: str) -> list[str]:
        """Modules this module directly imports (project-internal only)."""
        self._ensure_built()
        node = self._nodes.get(module)
        return sorted(node.imports) if node else []

    def dependents(self, module: str) -> list[str]:
        """Modules that directly import this module (project-internal only)."""
        self._ensure_built()
        return sorted(self._reverse.get(module, set()))

    def impact(self, module: str) -> list[str]:
        """Transitive dependents — every module that would be affected, directly
        or indirectly, by a breaking change to `module`. BFS over the reverse graph."""
        self._ensure_built()
        seen: set[str] = set()
        queue = deque(self._reverse.get(module, set()))
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            queue.extend(self._reverse.get(cur, set()) - seen)
        return sorted(seen)

    def find_symbol(self, name: str) -> list[dict]:
        """Every module that defines a top-level function/class with this name."""
        self._ensure_built()
        out = []
        for node in self._nodes.values():
            for sym in node.symbols:
                if sym.name == name:
                    out.append({"module": node.module, "file": node.file, "kind": sym.kind, "line": sym.line})
        return out

    def list_symbols(self, module: str) -> list[dict]:
        self._ensure_built()
        node = self._nodes.get(module)
        if not node:
            return []
        return [s.to_dict() for s in node.symbols]

    def resolve_module(self, hint: str) -> str | None:
        """Best-effort match of a user-typed file path or module name to a real module."""
        self._ensure_built()
        hint = hint.strip().replace("\\", "/")
        if hint.endswith(".py"):
            hint = hint[:-3]
        hint_dotted = hint.replace("/", ".")
        if hint_dotted in self._nodes:
            return hint_dotted
        # fall back to matching on the file's basename (e.g. user just says "file_controller")
        candidates = [m for m, n in self._nodes.items() if Path(n.file).stem == hint or n.file.endswith(hint)]
        return candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)

    def cycles(self) -> list[list[str]]:
        """Circular import chains, found via DFS with a recursion stack."""
        self._ensure_built()
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()
        found: list[list[str]] = []

        def dfs(m: str):
            visited.add(m)
            stack.append(m)
            on_stack.add(m)
            for dep in self._nodes.get(m, ModuleNode(m, "", 0, set(), set(), [])).imports:
                if dep not in visited:
                    dfs(dep)
                elif dep in on_stack:
                    idx = stack.index(dep)
                    cycle = stack[idx:] + [dep]
                    if sorted(cycle) not in (sorted(c) for c in found):
                        found.append(cycle)
            stack.pop()
            on_stack.discard(m)

        for m in self._nodes:
            if m not in visited:
                dfs(m)
        return found

    def stats(self) -> GraphStats:
        edge_count = sum(len(n.imports) for n in self._nodes.values())
        ext = set()
        for n in self._nodes.values():
            ext |= n.external_imports

        by_dependents = sorted(
            ((m, len(self._reverse.get(m, set()))) for m in self._nodes), key=lambda t: -t[1]
        )[:10]
        by_deps = sorted(
            ((m, len(n.imports)) for m, n in self._nodes.items()), key=lambda t: -t[1]
        )[:10]
        isolated = sorted(
            m for m in self._nodes
            if not self._nodes[m].imports and not self._reverse.get(m)
        )

        return GraphStats(
            module_count=len(self._nodes),
            edge_count=edge_count,
            external_dependency_count=len(ext),
            most_depended_on=[t for t in by_dependents if t[1] > 0],
            most_dependencies=[t for t in by_deps if t[1] > 0],
            isolated_modules=isolated,
        )
