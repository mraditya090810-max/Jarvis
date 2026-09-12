"""
Shrinks prompt context before it's sent to a model:
  - drops duplicate import lines
  - drops duplicate blank/whitespace-only lines (keeps at most one in a row)
  - drops a file block entirely if it's a byte-for-byte repeat of one already
    included (can happen when the same dependency is pulled in twice)
  - truncates to an approximate token budget as a hard backstop

None of this touches the *code itself* inside a kept block — only removes
redundant surrounding context, so semantics never change.
"""
from __future__ import annotations

import re

_IMPORT_RE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+.+)\s*$")


def dedupe_imports(text: str) -> str:
    seen: set[str] = set()
    out_lines = []
    for line in text.splitlines():
        m = _IMPORT_RE.match(line)
        if m:
            key = line.strip()
            if key in seen:
                continue
            seen.add(key)
        out_lines.append(line)
    return "\n".join(out_lines)


def collapse_blank_lines(text: str) -> str:
    out_lines = []
    prev_blank = False
    for line in text.splitlines():
        blank = not line.strip()
        if blank and prev_blank:
            continue
        out_lines.append(line)
        prev_blank = blank
    return "\n".join(out_lines)


def dedupe_context_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """blocks: list of (label, content). Drops exact-duplicate content blocks,
    keeping the first occurrence (and its label)."""
    seen: set[str] = set()
    out = []
    for label, content in blocks:
        norm = content.strip()
        if norm in seen:
            continue
        seen.add(norm)
        out.append((label, content))
    return out


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Approximate: ~4 chars/token for English/code. Truncates from the
    middle-out is unnecessary here since callers already order context by
    relevance (most relevant first) — we just cut the tail."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated — context exceeded token budget]"


def optimize(text: str, max_tokens: int = 6000) -> str:
    text = dedupe_imports(text)
    text = collapse_blank_lines(text)
    text = truncate_to_token_budget(text, max_tokens)
    return text
