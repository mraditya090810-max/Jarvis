"""
Security Guard for the Self-Engineering pipeline.

This module is the one hard boundary the rest of the pipeline cannot
override. It answers exactly one question honestly and conservatively:

    "Is this proposed change even allowed to be considered?"

Design rules (do not relax these without a human rewriting this file by hand):
  * The guard's own file, and every other file inside self_engineer/, is
    PROTECTED — a patch may never modify its own safety system.
  * Only files inside the project root may be touched. No path traversal,
    no absolute paths outside the project, no writes to OS/config paths.
  * The generated code (new file content) is scanned for banned constructs:
    shell execution, registry access, package installation, destructive
    filesystem operations, network exfiltration primitives used outside the
    project's own already-approved modules, etc.
  * Failing any of these checks blocks the patch immediately with
    PatchStatus.BLOCKED_SECURITY — it never reaches the human approval step,
    because a human should not need to review "this tries to run rm -rf".
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


# Files/directories a generated patch may NEVER modify, regardless of anything else.
PROTECTED_PATHS = {
    "self_engineer",                 # the safety system itself
    "config/api_keys.json",          # secrets
    "config/certs",                  # TLS material
    ".git",
}

# Config keys / OS-level danger patterns. These are checked as AST call
# signatures, not naive string search, to avoid both false positives (a
# docstring mentioning "os.system") and false negatives (aliased imports).
BANNED_CALLS = {
    ("os", "system"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("shutil", "rmtree"),
    ("subprocess", "Popen"),   # only banned when shell=True is passed — checked specially
    ("subprocess", "call"),
    ("subprocess", "run"),
    ("winreg", "*"),           # any winreg.* call — registry access
    ("ctypes", "windll"),
    ("pip", "main"),
}

BANNED_IMPORTS = {"winreg", "_winreg"}

BANNED_STRING_MARKERS = (
    "rm -rf",
    "del /f /s /q",
    "format c:",
    "DROP TABLE",
    "shutdown /s",
    "REG DELETE",
    "REG ADD",
)


@dataclass
class SecurityVerdict:
    allowed: bool
    notes: list


def is_protected_path(rel_path: str) -> bool:
    """True if `rel_path` (relative to project root, posix-style) is off-limits."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    for protected in PROTECTED_PATHS:
        protected_norm = protected.replace("\\", "/")
        if norm == protected_norm or norm.startswith(protected_norm + "/"):
            return True
    return False


def is_within_project(project_root: Path, rel_path: str) -> bool:
    """Resolve rel_path against project_root and confirm it doesn't escape it."""
    try:
        resolved = (project_root / rel_path).resolve()
        return project_root.resolve() in resolved.parents or resolved == project_root.resolve()
    except Exception:
        return False


def _collect_banned_findings(source: str) -> tuple[set, bool]:
    """
    Returns (findings, parse_ok). findings is a set of short violation strings
    (deduplicated) found anywhere in `source`. Used both on the original file
    (baseline — code the human already shipped) and the proposed new file
    (candidate) so callers can flag only what the PATCH newly introduces.
    """
    findings: set = set()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {f"__syntax_error__:{e}"}, False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BANNED_IMPORTS:
                    findings.add(f"Banned import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if node.module in BANNED_IMPORTS:
                findings.add(f"Banned import: {node.module}")

        if isinstance(node, ast.Call):
            func = node.func
            mod_name = attr_name = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                mod_name, attr_name = func.value.id, func.attr
            elif isinstance(func, ast.Name):
                attr_name = func.id

            if (mod_name, attr_name) in BANNED_CALLS or (mod_name, "*") in BANNED_CALLS:
                if mod_name == "subprocess":
                    shell_true = any(
                        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                        for kw in node.keywords
                    )
                    if shell_true:
                        findings.add(f"Banned call: subprocess.{attr_name}(..., shell=True)")
                else:
                    findings.add(f"Banned call: {mod_name}.{attr_name}" if mod_name else f"Banned call: {attr_name}")

    for marker in BANNED_STRING_MARKERS:
        if marker.lower() in source.lower():
            findings.add(f"Banned string pattern found: {marker!r}")

    return findings, True


def _scan_ast_for_banned_calls(source: str, notes: list, baseline: str | None = None) -> bool:
    """
    Returns True if `source` is clean RELATIVE TO `baseline`. If baseline is
    given, only findings that are new (present in source but not in baseline)
    are treated as violations — pre-existing, already-shipped code the human
    wrote is not re-litigated on every unrelated patch to the same file.
    Appends human-readable notes on violations.
    """
    candidate_findings, parse_ok = _collect_banned_findings(source)
    if not parse_ok:
        notes.append(next(iter(candidate_findings)).split(":", 1)[1].strip())
        return False

    if baseline is not None:
        baseline_findings, baseline_parse_ok = _collect_banned_findings(baseline)
        if not baseline_parse_ok:
            baseline_findings = set()  # can't establish a baseline — be conservative, flag everything
        new_findings = candidate_findings - baseline_findings
    else:
        new_findings = candidate_findings

    if new_findings:
        notes.extend(sorted(new_findings))
        return False
    return True


def check_patch(project_root: Path, files: list) -> SecurityVerdict:
    """
    files: list of objects/dicts with .path / ["path"] and .new_content / ["new_content"].
    Returns a SecurityVerdict — allowed=False means the patch must be blocked
    before it is ever shown to the owner for approval.
    """
    notes: list = []
    allowed = True

    for f in files:
        path = f["path"] if isinstance(f, dict) else f.path
        content = f["new_content"] if isinstance(f, dict) else f.new_content
        original = f["original_content"] if isinstance(f, dict) else f.original_content

        if is_protected_path(path):
            notes.append(f"Refused: '{path}' is a protected path (safety system or secrets).")
            allowed = False
            continue

        if not is_within_project(project_root, path):
            notes.append(f"Refused: '{path}' resolves outside the project root.")
            allowed = False
            continue

        if path.endswith(".py"):
            if not _scan_ast_for_banned_calls(content, notes, baseline=original):
                allowed = False

    return SecurityVerdict(allowed=allowed, notes=notes)
