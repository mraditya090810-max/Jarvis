"""Action schema, observation shape, config, validation. No I/O here."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ── allowed actions & required fields ────────────────────────────────────────
# Every action the model is allowed to emit. Anything else is rejected.
ACTION_SCHEMA: dict[str, list[str]] = {
    "navigate":   ["url"],
    "click":      ["id"],
    "type":       ["id", "text"],
    "clear":      ["id"],
    "select":     ["id", "value"],
    "check":      ["id"],
    "uncheck":    ["id"],
    "press":      [],                     # key defaults to Enter
    "scroll":     [],                     # direction/amount optional
    "back":       [],
    "forward":    [],
    "reload":     [],
    "new_tab":    [],                     # url optional
    "switch_tab": [],
    "close_tab":  [],
    "wait":       [],                     # seconds optional
    "read_text":  [],
    "screenshot": [],
    "done":       ["answer"],
    "fail":       ["reason"],
    "ask_user":   ["question"],
}
ALLOWED_ACTIONS = set(ACTION_SCHEMA.keys())

# Actions that require user confirmation before execution (unless the user
# explicitly authorised them in the task). Matched against the target
# element's accessible name and the model's stated reason.
HIGH_IMPACT_KEYWORDS = {
    "buy", "purchase", "checkout", "pay", "payment", "order",
    "delete", "remove account", "cancel subscription",
    "transfer", "confirm order", "confirm purchase", "place order",
    "subscribe now", "unsubscribe", "change password", "reset password",
    "send money", "sign contract", "accept terms", "agree to terms",
    "install", "uninstall",
}

MAX_TEXT_CHARS = 4500
MAX_ELEMENTS = 140
MAX_STEPS_IN_CONTEXT = 4


@dataclass
class AgentConfig:
    """Tunable, per-task caps. All bounded — no infinite loops."""
    max_actions: int = 25
    max_retries: int = 3
    max_observations: int = 40
    max_seconds: float = 180.0
    default_url: str = "about:blank"
    slow_mo_ms: int = 250            # delay between actions so the user can watch
    js_timeout: float = 6.0          # per-JS call timeout
    model: str = "gemini-2.5-flash"
    require_confirmation_for_high_impact: bool = True

    def clamp(self) -> "AgentConfig":
        self.max_actions = max(1, min(int(self.max_actions), 100))
        self.max_retries = max(0, min(int(self.max_retries), 10))
        self.max_observations = max(self.max_actions, min(int(self.max_observations), 200))
        self.max_seconds = max(10.0, min(float(self.max_seconds), 900.0))
        self.slow_mo_ms = max(0, min(int(self.slow_mo_ms), 3000))
        self.js_timeout = max(1.0, min(float(self.js_timeout), 30.0))
        return self


@dataclass
class Observation:
    url: str = ""
    title: str = ""
    text: str = ""
    ready_state: str = ""
    elements: list[dict] = field(default_factory=list)
    hints: dict = field(default_factory=dict)
    scroll_y: int = 0
    scroll_height: int = 0
    viewport_h: int = 0
    error: str = ""

    def compact(self) -> dict:
        """Token-efficient view for the model."""
        return {
            "url": self.url,
            "title": self.title,
            "readyState": self.ready_state,
            "scroll": f"{self.scroll_y}/{self.scroll_height} (vp {self.viewport_h})",
            "hints": self.hints,
            "elements": self.elements,
            "text": self.text[:MAX_TEXT_CHARS],
            "error": self.error,
        }


@dataclass
class Step:
    n: int
    action: dict
    result: str
    verified: bool
    detail: str = ""


class ActionError(ValueError):
    """Raised for malformed model output."""


def validate_action(raw: Any) -> dict:
    """
    Strictly validate a model-generated action. Never executes anything here.
    Returns the action dict on success; raises ActionError on any problem.
    """
    if not isinstance(raw, dict):
        raise ActionError(f"action must be a JSON object, got {type(raw).__name__}")

    name = str(raw.get("action", "")).strip().lower()
    if name not in ALLOWED_ACTIONS:
        raise ActionError(f"unknown action: {name!r}")

    required = ACTION_SCHEMA[name]
    missing = [f for f in required if f not in raw or raw[f] in (None, "")]
    if missing:
        raise ActionError(f"action {name!r} missing required field(s): {missing}")

    # Light type checks on the fields the executor actually uses.
    if name == "navigate" and not isinstance(raw["url"], str):
        raise ActionError("navigate.url must be a string")
    if name == "type" and not isinstance(raw.get("text", ""), str):
        raise ActionError("type.text must be a string")
    if name == "wait":
        try:
            raw["seconds"] = float(raw.get("seconds", 1.0))
        except (TypeError, ValueError):
            raise ActionError("wait.seconds must be numeric")
        raw["seconds"] = max(0.1, min(raw["seconds"], 20.0))
    if name == "scroll":
        raw["direction"] = str(raw.get("direction", "down")).lower()
        if raw["direction"] not in ("up", "down"):
            raise ActionError("scroll.direction must be 'up' or 'down'")
        try:
            raw["amount"] = int(raw.get("amount", 600))
        except (TypeError, ValueError):
            raise ActionError("scroll.amount must be an integer")
        raw["amount"] = max(50, min(raw["amount"], 5000))

    return raw


def looks_high_impact(action: dict, target_name: str = "") -> bool:
    """Conservative heuristic — used only to gate, never to auto-approve."""
    if action.get("action") not in ("click", "type", "press"):
        return False
    haystack = " ".join([
        str(action.get("reason", "")),
        str(target_name),
    ]).lower()
    return any(kw in haystack for kw in HIGH_IMPACT_KEYWORDS)


def safe_json_loads(text: str) -> Any:
    """Parse JSON that may be wrapped in ```json fences by the model."""
    if not isinstance(text, str):
        raise ActionError("expected string from model")
    t = text.strip()
    if t.startswith("```"):
        # strip one fenced block
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return json.loads(t)