"""
Self Analyzer — reads JARVIS's own source tree and reports concrete,
file-and-line-anchored weaknesses. Pure static analysis (stdlib `ast` only,
no network, no execution of project code), so it is safe to run at any time,
unattended.

Checks implemented:
  * large_function      — function body longer than a threshold (readability/maintainability)
  * long_file           — module longer than a threshold (modularity)
  * unused_import       — imported name never referenced in the module
  * bare_except         — `except:` / `except Exception:` swallowing errors silently
  * todo_marker         — TODO / FIXME / XXX comments left in code
  * broad_exception_log — except blocks that neither log nor re-raise
  * duplicate_signature — two functions in the same file sharing an identical
                           argument signature and near-identical body length
                           (cheap proxy for duplicated code, flagged for a human/LLM
                           to look at rather than auto-merged)
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from .models import Weakness, Severity, _new_id

# Directories never scanned — third-party/venv/build artifacts, not "our" code.
_EXCLUDE_DIRS = {
    "venv", ".venv", "env", "__pycache__", ".git", "node_modules",
    "self_engineer",  # the analyzer does not flag itself as a target for auto-patching
    "build", "dist", ".idea", ".vscode",
}

LARGE_FUNCTION_LINES = 80
LONG_FILE_LINES = 600


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


class SelfAnalyzer:
    def __init__(self, project_root: Path):
        self.root = project_root
        # Per-file cache: rel_path -> (mtime, findings) — skip unchanged files.
        self._file_cache: dict[str, tuple[float, list[Weakness]]] = {}
        self._ast_cache: dict[str, tuple[float, ast.Module]] = {}

    def analyze(self, files: list[str] | None = None) -> list[Weakness]:
        """Scan the project (or only ``files``) for weaknesses.

        When ``files`` is None, unchanged files are served from cache.
        Pass a list of relative paths (e.g. ``["actions/dev_agent.py"]``)
        for incremental analysis after a patch or edit.
        """
        findings: list[Weakness] = []

        if files is not None:
            targets = []
            for rel in files:
                p = self.root / rel.replace("/", os.sep)
                if p.is_file() and p.suffix == ".py":
                    targets.append(p)
        else:
            targets = list(_iter_python_files(self.root))

        seen_rels: set[str] = set()
        for file_path in targets:
            rel = _rel(self.root, file_path)
            seen_rels.add(rel)
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue

            if files is None:
                cached = self._file_cache.get(rel)
                if cached and cached[0] == mtime:
                    findings.extend(cached[1])
                    continue

            try:
                source = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            file_findings = self._analyze_file(rel, source, mtime)
            self._file_cache[rel] = (mtime, file_findings)
            findings.extend(file_findings)

        if files is None:
            # Drop cache entries for deleted files.
            stale = set(self._file_cache) - seen_rels
            for rel in stale:
                self._file_cache.pop(rel, None)
                self._ast_cache.pop(rel, None)

        return findings

    def _analyze_file(self, rel_path: str, source: str, mtime: float = 0.0) -> list[Weakness]:
        out: list[Weakness] = []
        lines = source.splitlines()

        if len(lines) > LONG_FILE_LINES:
            out.append(Weakness(
                id=_new_id("wk"), category="long_file", file=rel_path,
                line_start=1, line_end=len(lines),
                description=(
                    f"File is {len(lines)} lines long (threshold {LONG_FILE_LINES}). "
                    "Consider splitting into smaller, single-responsibility modules."
                ),
                severity=Severity.LOW,
            ))

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith(("# TODO", "#TODO", "# FIXME", "#FIXME", "# XXX")):
                out.append(Weakness(
                    id=_new_id("wk"), category="todo_marker", file=rel_path,
                    line_start=i, line_end=i,
                    description=f"Unresolved marker left in code: {stripped[:100]}",
                    severity=Severity.INFO,
                ))

        try:
            cached_ast = self._ast_cache.get(rel_path)
            if cached_ast and cached_ast[0] == mtime:
                tree = cached_ast[1]
            else:
                tree = ast.parse(source)
                self._ast_cache[rel_path] = (mtime, tree)
        except SyntaxError as e:
            out.append(Weakness(
                id=_new_id("wk"), category="syntax_error", file=rel_path,
                line_start=getattr(e, "lineno", 1) or 1, line_end=getattr(e, "lineno", 1) or 1,
                description=f"File fails to parse: {e}",
                severity=Severity.HIGH,
            ))
            return out

        imported_names: dict[str, int] = {}
        used_names: set[str] = set()
        function_signatures: dict[tuple, list[tuple[str, int, int]]] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    imported_names[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    imported_names[name] = node.lineno
            elif isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                pass  # handled via Name at the base of the attribute chain

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body_len = (node.end_lineno or node.lineno) - node.lineno
                if body_len > LARGE_FUNCTION_LINES:
                    out.append(Weakness(
                        id=_new_id("wk"), category="large_function", file=rel_path,
                        line_start=node.lineno, line_end=node.end_lineno or node.lineno,
                        description=(
                            f"Function '{node.name}' is {body_len} lines long "
                            f"(threshold {LARGE_FUNCTION_LINES}). Consider extracting helpers."
                        ),
                        severity=Severity.MEDIUM,
                    ))

                sig = tuple(a.arg for a in node.args.args)
                if sig:
                    function_signatures.setdefault(sig, []).append((node.name, node.lineno, body_len))

            if isinstance(node, ast.ExceptHandler):
                is_bare = node.type is None
                is_broad_exception = (
                    isinstance(node.type, ast.Name) and node.type.id == "Exception"
                )
                body_is_pass_or_silent = (
                    len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                ) or not any(
                    isinstance(n, (ast.Raise,)) or
                    (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and
                     isinstance(n.value.func, (ast.Name, ast.Attribute)) and
                     "print" in ast.dump(n.value.func))
                    for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                )
                if is_bare and body_is_pass_or_silent:
                    out.append(Weakness(
                        id=_new_id("wk"), category="bare_except", file=rel_path,
                        line_start=node.lineno, line_end=node.end_lineno or node.lineno,
                        description="Bare 'except:' silently swallows all errors, including KeyboardInterrupt/SystemExit.",
                        severity=Severity.MEDIUM,
                    ))
                elif is_broad_exception and body_is_pass_or_silent:
                    out.append(Weakness(
                        id=_new_id("wk"), category="broad_exception_log", file=rel_path,
                        line_start=node.lineno, line_end=node.end_lineno or node.lineno,
                        description="'except Exception' with no logging or re-raise — failures vanish silently.",
                        severity=Severity.LOW,
                    ))

        for name, lineno in imported_names.items():
            if name not in used_names and name != "_":
                out.append(Weakness(
                    id=_new_id("wk"), category="unused_import", file=rel_path,
                    line_start=lineno, line_end=lineno,
                    description=f"Imported name '{name}' does not appear to be used in this file.",
                    severity=Severity.INFO,
                ))

        for sig, occurrences in function_signatures.items():
            if len(occurrences) < 2:
                continue
            lengths = [b for _, _, b in occurrences]
            if max(lengths) - min(lengths) <= 3 and min(lengths) > 5:
                names = ", ".join(n for n, _, _ in occurrences)
                out.append(Weakness(
                    id=_new_id("wk"), category="duplicate_signature", file=rel_path,
                    line_start=occurrences[0][1], line_end=occurrences[-1][1],
                    description=(
                        f"Functions with identical signature and near-identical length "
                        f"may be duplicated logic: {names}. Worth a manual look before merging."
                    ),
                    severity=Severity.INFO,
                ))

        return out
