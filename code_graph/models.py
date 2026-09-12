"""
Shared data structures for the Codebase Knowledge Engine.

Plain, JSON-serialisable dataclasses — same philosophy as
self_engineer.models: inspectable state, no hidden objects.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Symbol:
    """A top-level function or class defined in a module."""
    name: str
    kind: str          # "function" | "async_function" | "class"
    line: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModuleNode:
    """One project-internal Python file."""
    module: str                 # dotted module name, e.g. "actions.file_controller"
    file: str                   # path relative to project root, forward slashes
    loc: int                    # line count
    imports: set                # dotted names of project-internal modules this file imports
    external_imports: set       # top-level names of non-project imports (os, json, PyQt6...)
    symbols: list               # list[Symbol] — top-level functions/classes defined here

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "file": self.file,
            "loc": self.loc,
            "imports": sorted(self.imports),
            "external_imports": sorted(self.external_imports),
            "symbols": [s.to_dict() for s in self.symbols],
        }


@dataclass
class GraphStats:
    module_count: int
    edge_count: int
    external_dependency_count: int
    most_depended_on: list      # list[(module, dependent_count)], top N
    most_dependencies: list     # list[(module, dependency_count)], top N
    isolated_modules: list      # modules with zero project-internal imports in or out

    def to_dict(self) -> dict:
        return asdict(self)
