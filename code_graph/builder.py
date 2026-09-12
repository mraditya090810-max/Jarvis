"""
Builder — walks the project source tree once and produces a dict of
ModuleNode objects keyed by dotted module name. Pure `ast` static analysis,
same exclude-dir convention as self_engineer.self_analyzer so the two stay
consistent about what counts as "our code".

Import resolution is intentionally conservative: an import only becomes a
graph edge when it resolves to a real file in this project. Anything that
doesn't resolve (third-party packages, stdlib) is recorded in
`external_imports` for visibility but never turned into an edge — a missed
internal edge is a worse failure mode than a stray external one, since the
whole point of this graph is accurate blast-radius analysis for
self_engineer.
"""
from __future__ import annotations

import ast
from pathlib import Path

from .models import ModuleNode, Symbol

EXCLUDE_DIRS = {
    "venv", ".venv", "env", "__pycache__", ".git", "node_modules",
    "build", "dist", ".idea", ".vscode",
}


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _module_name(rel_path: str) -> str:
    """'actions/file_controller.py' -> 'actions.file_controller'; '__init__.py' -> package name."""
    parts = rel_path[:-3].split("/") if rel_path.endswith(".py") else rel_path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _current_package(module_name: str, is_init: bool) -> str:
    if is_init:
        return module_name
    return module_name.rsplit(".", 1)[0] if "." in module_name else ""


def _resolve_relative(current_package: str, level: int, module: str | None) -> str:
    parts = current_package.split(".") if current_package else []
    # level 1 == current package itself; each extra level strips one more component
    strip = max(0, level - 1)
    base_parts = parts[: len(parts) - strip] if strip <= len(parts) else []
    if module:
        base_parts = base_parts + module.split(".")
    return ".".join(p for p in base_parts if p)


def _extract_symbols(tree: ast.Module) -> list[Symbol]:
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.append(Symbol(name=node.name, kind="function", line=node.lineno))
        elif isinstance(node, ast.AsyncFunctionDef):
            out.append(Symbol(name=node.name, kind="async_function", line=node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append(Symbol(name=node.name, kind="class", line=node.lineno))
    return out


def build_module_map(root: Path) -> dict[str, str]:
    """dotted module name -> file rel path, for every .py file in the project."""
    mapping: dict[str, str] = {}
    for path in _iter_python_files(root):
        rel = _rel(root, path)
        mapping[_module_name(rel)] = rel
    return mapping


def _resolve_dotted(name: str, module_map: dict[str, str]) -> str | None:
    """Longest-prefix match of a dotted name against known project modules."""
    if name in module_map:
        return name
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in module_map:
            return candidate
    return None


def build_graph(root: Path) -> dict[str, ModuleNode]:
    module_map = build_module_map(root)
    nodes: dict[str, ModuleNode] = {}

    for path in _iter_python_files(root):
        rel = _rel(root, path)
        module_name = _module_name(rel)
        is_init = path.name == "__init__.py"
        package = _current_package(module_name, is_init)

        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            continue

        imports: set[str] = set()
        external: set[str] = set()

        try:
            tree = ast.parse(source)
        except SyntaxError:
            nodes[module_name] = ModuleNode(
                module=module_name, file=rel, loc=len(source.splitlines()),
                imports=imports, external_imports=external, symbols=[],
            )
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = _resolve_dotted(alias.name, module_map)
                    if resolved and resolved != module_name:
                        imports.add(resolved)
                    elif not resolved:
                        external.add(alias.name.split(".")[0])

            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base = _resolve_relative(package, node.level, node.module)
                    if base in module_map and base != module_name:
                        imports.add(base)
                    matched_any = base in module_map
                    for alias in node.names:
                        candidate = f"{base}.{alias.name}" if base else alias.name
                        if candidate in module_map and candidate != module_name:
                            imports.add(candidate)
                            matched_any = True
                    if not matched_any and base:
                        external.add(base.split(".")[0])
                else:
                    mod = node.module or ""
                    resolved = _resolve_dotted(mod, module_map) if mod else None
                    matched_any = False
                    if resolved and resolved != module_name:
                        imports.add(resolved)
                        matched_any = True
                    for alias in node.names:
                        candidate = f"{mod}.{alias.name}" if mod else alias.name
                        if candidate in module_map and candidate != module_name:
                            imports.add(candidate)
                            matched_any = True
                    if not matched_any and mod:
                        external.add(mod.split(".")[0])

        nodes[module_name] = ModuleNode(
            module=module_name, file=rel, loc=len(source.splitlines()),
            imports=imports, external_imports=external,
            symbols=_extract_symbols(tree),
        )

    return nodes
