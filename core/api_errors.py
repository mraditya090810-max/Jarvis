"""Helpers for recognizing Google/Gemini connection failures."""

from __future__ import annotations


def _normalize_error(err: str) -> str:
    return (err or "").lower()


def is_invalid_api_key_error(err: str) -> bool:
    text = _normalize_error(err)
    return any(token in text for token in (
        "api key not valid",
        "api key invalid",
        "invalid api key",
        "invalid api key.",
        "authentication failed",
        "1007",
    ))


def is_project_access_blocked_error(err: str) -> bool:
    text = _normalize_error(err)
    if "1008" in text and "project" in text:
        return True

    return any(token in text for token in (
        "project has been denied access",
        "your project has been denied access",
        "denied access. please contact support",
        "project is not allowed",
        "project access is blocked",
        "policy violation",
        "project is disabled",
        "gemini api access is disabled",
        "access to this project is blocked",
    ))
