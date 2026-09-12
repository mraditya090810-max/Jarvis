"""
Patch Generator — turns one Weakness into a concrete set of file changes.

Two paths, in order of preference:
  1. LLM-assisted: ask the AI Router (core.ai — free OpenRouter models with
     automatic discovery/ranking/fallback/caching, also used by dev_agent)
     to rewrite the affected file, given the weakness description and the
     full original source. The model is asked to return the COMPLETE new
     file content only — no explanations mixed in — so the result can be
     safely parsed and tested like any other patch.
  2. Deterministic fallback: for a handful of mechanical weakness categories
     (unused_import, todo_marker) we can produce a correct fix without any
     model at all. This keeps the pipeline fully testable even when no local
     LLM server is running, and guarantees these simple cases never depend on
     model quality.

Either path returns a list[FileChange]; nothing is written to disk here.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from .models import Weakness, FileChange

# Make the project's core/ package importable for call_llm_text without
# requiring this package to be installed — self_engineer lives at the
# project root alongside core/, so this just needs the root on sys.path.
_PROJECT_ROOT_MARKER = Path(__file__).resolve().parent.parent


def _import_ai_router():
    root_str = str(_PROJECT_ROOT_MARKER)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from core import ai as ai_router  # type: ignore
    return ai_router


_PATCH_SYSTEM_PROMPT = (
    "You are a senior Python engineer patching a single file in an existing project. "
    "You will be given the full current file content and a description of one specific "
    "weakness to fix. Rewrite the ENTIRE file with the minimal change needed to fix that "
    "weakness — preserve everything else exactly (formatting, unrelated logic, imports "
    "still in use, comments). Do not fix unrelated issues. Do not add new features. "
    "Respond with ONLY the complete new file content, no markdown fences, no commentary."
)


def _try_llm_rewrite(weakness: Weakness, original: str) -> str | None:
    try:
        ai_router = _import_ai_router()
    except Exception:
        return None

    prompt = (
        f"File: {weakness.file}\n"
        f"Weakness category: {weakness.category}\n"
        f"Lines {weakness.line_start}-{weakness.line_end}\n"
        f"Description: {weakness.description}\n\n"
        f"--- CURRENT FILE CONTENT ---\n{original}\n--- END FILE ---\n\n"
        "Return the complete corrected file content only."
    )
    # project_hash ties the cache entry to this exact file content, so a
    # cached patch auto-invalidates the moment the file changes again.
    project_hash = str(hash(original))
    try:
        text = ai_router.call_llm_text(
            prompt, system=_PATCH_SYSTEM_PROMPT, timeout=90,
            project_hash=project_hash, task="self_engineer_patch",
        )
    except Exception:
        return None

    text = text.strip()
    if text.startswith("```"):
        # Strip an accidental markdown fence if the model added one anyway.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    if not text or "def " not in text and "import" not in text and len(text) < 10:
        return None  # obviously not a real file rewrite

    try:
        ast.parse(text)
    except SyntaxError:
        return None  # model produced invalid Python — reject, fall through to deterministic path

    return text


def _validate_syntax(new_content: str) -> bool:
    """A mechanical line-deletion can leave a block statement with an empty
    body (e.g. deleting the only line inside a try:) — always re-parse before
    accepting a deterministic fix, and reject it if that happens."""
    try:
        ast.parse(new_content)
        return True
    except SyntaxError:
        return False


def _deterministic_fix(weakness: Weakness, original: str) -> str | None:
    lines = original.splitlines(keepends=True)

    if weakness.category == "unused_import":
        idx = weakness.line_start - 1
        if 0 <= idx < len(lines) and ("import " in lines[idx]):
            candidate = lines[:idx] + lines[idx + 1:]
            new_content = "".join(candidate)
            return new_content if _validate_syntax(new_content) else None
        return None

    if weakness.category == "todo_marker":
        idx = weakness.line_start - 1
        if 0 <= idx < len(lines):
            candidate = lines[:idx] + lines[idx + 1:]
            new_content = "".join(candidate)
            return new_content if _validate_syntax(new_content) else None
        return None

    return None


def generate(weakness: Weakness, original_content: str) -> tuple[list[FileChange], str, str, str]:
    """
    Returns (files_changed, summary, problem, solution).
    Raises ValueError if no fix could be produced at all.
    """
    new_content = _try_llm_rewrite(weakness, original_content)
    source = "LLM-assisted rewrite"
    if new_content is None:
        new_content = _deterministic_fix(weakness, original_content)
        source = "deterministic mechanical fix"

    if new_content is None or new_content == original_content:
        raise ValueError(
            f"Could not generate a patch for weakness {weakness.id} "
            f"({weakness.category} in {weakness.file}) — no free AI model reachable and "
            "no deterministic rule covers this category. Try again once "
            "\"openrouter_api_key\" is configured in config/api_keys.json."
        )

    file_change = FileChange(
        path=weakness.file,
        original_content=original_content,
        new_content=new_content,
    )

    problem = weakness.description
    solution = f"Applied a {source} to '{weakness.file}' addressing: {weakness.category}."
    summary = f"Fix {weakness.category} in {weakness.file} (lines {weakness.line_start}-{weakness.line_end})"

    return [file_change], summary, problem, solution
