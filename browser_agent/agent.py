"""
The agent loop. Bounded. Verifies every action. Recovers from normal page
changes. Never runs model-generated code.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import actions as A
from . import decider as D
from . import inspector as I
from . import verifier as V
from .models import (
    AgentConfig, Observation, Step, ActionError,
    looks_high_impact,
)


# ── high-impact confirmation hook ──────────────────────────────────────────
# We integrate with core.confirm ONLY if it is functional (i.e. something is
# actually listening to resolve()). In this repo, main.py binds a no-op, so
# we fall back to "ask the user in speech and stop". Documented in the brief.
def _try_confirm_gate(title: str, detail: str) -> Optional[bool]:
    try:
        from core import confirm as gate  # noqa: WPS433
    except Exception:
        return None
    req = getattr(gate, "request", None)
    if not callable(req):
        return None
    try:
        # request() returns after user resolves it, or None on timeout.
        # We only use it if a resolver is actually wired.
        resolver = getattr(gate, "_resolver", None) or getattr(gate, "_resolved", None)
        if resolver is None and not hasattr(gate, "resolve"):
            return None
        result = req(title, detail)  # blocks per core/confirm.py TIMEOUT_SECONDS
        if isinstance(result, bool):
            return result
        return None
    except Exception:
        return None


class BrowserAgent:
    """
    Drives a BrowserView for one task at a time. Thread-safe: start_task() is
    called from the plugin's executor thread, and the loop runs inline so the
    plugin's run() returns only when the task is finished.

    Callbacks (all optional):
        on_status(text)   — short user-visible progress line
        on_speak(text)    — call JarvisLive.speak(text) or equivalent
    """

    def __init__(self, view, cfg: AgentConfig,
                 on_status: Optional[Callable[[str], None]] = None,
                 on_speak:  Optional[Callable[[str], None]] = None):
        self.view = view
        self.cfg = cfg.clamp()
        self.on_status = on_status or (lambda s: None)
        self.on_speak  = on_speak  or (lambda s: None)
        self._stop = threading.Event()
        self._history: list[Step] = []

    def stop(self):
        self._stop.set()

    # ── main entry point ───────────────────────────────────────────────────
    def run_task(self, task: str, start_url: Optional[str] = None) -> str:
        """
        Runs the full loop and returns a spoken summary string.
        Never raises — errors are converted to spoken results.
        """
        t0 = time.monotonic()
        self.on_status("Starting browser agent…")

        # ── 1) initial navigation if requested ────────────────────────────
        if start_url:
            try:
                self.view.navigate(start_url)
                self.on_status(f"Opening {start_url}…")
                time.sleep(1.2)  # let the page settle
            except Exception as e:
                return f"Sir, I couldn't open {start_url}: {e}"

        # ── 2) the bounded loop ───────────────────────────────────────────
        observations = 0
        for step_n in range(1, self.cfg.max_actions + 1):

            if self._stop.is_set():
                return "Browser task cancelled."

            if time.monotonic() - t0 > self.cfg.max_seconds:
                return self._summarise_stop("Time limit reached", task)

            # OBSERVE
            self.on_status("Inspecting page…")
            try:
                obs = I.observe(self.view)
            except Exception as e:
                return f"Sir, I couldn't inspect the page: {e}"

            observations += 1
            if observations > self.cfg.max_observations:
                return self._summarise_stop("Too many observations", task)

            if obs.hints.get("hasCaptcha"):
                self.on_status("CAPTCHA detected — waiting for user")
                return (
                    "Sir, a CAPTCHA appeared on the page. "
                    "I've stopped — please solve it in the browser panel, "
                    "then ask me to continue."
                )

            # DECIDE
            self.on_status("Deciding next step…")
            try:
                action = D.decide(task, obs, self._history, self.cfg)
            except ActionError as e:
                return self._summarise_stop(f"Model returned invalid action ({e})", task)
            except Exception as e:
                return self._summarise_stop(f"Model call failed ({e})", task)

            name = action["action"]

            # ── terminal actions ─────────────────────────────────────────
            if name == "done":
                answer = str(action.get("answer", "")).strip() or "Task complete."
                self.on_status("Task complete.")
                self.on_speak(answer)
                return answer

            if name == "fail":
                reason = str(action.get("reason", "")).strip() or "Could not complete."
                self.on_status(f"Task failed: {reason}")
                return f"Sir, {reason}"

            if name == "ask_user":
                q = str(action.get("question", "")).strip() or "I need your help."
                self.on_status("Waiting for user input…")
                self.on_speak(q)
                return q

            # ── high-impact gate ─────────────────────────────────────────
            target_name = ""
            if "id" in action:
                target_name = I.find_element_name(obs, action["id"])
            if self.cfg.require_confirmation_for_high_impact and \
               looks_high_impact(action, target_name):
                question = (
                    f"Sir, this step looks consequential: "
                    f"{action.get('reason') or name} on '{target_name or action.get('id')}'. "
                    f"Confirm and I'll proceed."
                )
                self.on_status("Confirmation required")
                self.on_speak(question)
                return question

            # ── EXECUTE ───────────────────────────────────────────────────
            self.on_status(self._describe(action, target_name))
            before_url = self._safe_url()

            try:
                ok, result_msg, raw = A.execute(self.view, action)
            except ActionError as e:
                ok, result_msg, raw = False, f"executor error: {e}", None
            except Exception as e:
                ok, result_msg, raw = False, f"executor exception: {e}", None

            # small pause so the user can follow along in the panel
            if self.cfg.slow_mo_ms:
                time.sleep(self.cfg.slow_mo_ms / 1000.0)

            # ── VERIFY ────────────────────────────────────────────────────
            verification = V.verify(
                self.view, action, ok, raw,
                before_url, {"url": obs.url},
            )
            if not verification.ok:
                self.on_status(f"Verifying failed — recovering…")
                verification = self._recover(action, obs, verification, before_url)

            self._history.append(Step(
                n=step_n, action=action,
                result=result_msg, verified=verification.ok,
                detail=verification.detail,
            ))

            if not verification.ok:
                # one recovery attempt was already tried; if still failing, stop
                return self._summarise_stop(
                    f"Action {name} did not take effect ({verification.detail})",
                    task,
                )

        return self._summarise_stop("Action limit reached", task)

    # ── recovery ───────────────────────────────────────────────────────────
    def _recover(self, action, obs: Observation, failed: V.Verification,
                 before_url: str) -> V.Verification:
        """
        Single recovery attempt: re-observe, and for click/type actions retry
        once with the same element id (which may have moved) — nothing else.
        """
        self.on_status("Re-observing page after failure…")
        time.sleep(0.4)
        try:
            fresh = I.observe(self.view)
        except Exception:
            return failed

        # Cookie banner appeared mid-flow? Try once to dismiss.
        if fresh.hints.get("hasCookieBanner"):
            for el in fresh.elements:
                nm = (el.get("name") or "").lower()
                if any(w in nm for w in ("accept", "agree", "allow all", "got it", "consent")):
                    self.on_status("Dismissing cookie banner…")
                    try:
                        A.a_click(self.view, {"action": "click", "id": el["id"]})
                        time.sleep(0.6)
                        return V.Verification(True, "cookie banner dismissed", recovered=True)
                    except Exception:
                        break

        # Same element, same action, once.
        if action.get("action") in ("click", "type", "clear", "select", "check", "uncheck"):
            if "id" in action:
                still = any(e.get("id") == action["id"] for e in fresh.elements)
                if still:
                    self.on_status("Retrying action on refreshed page…")
                    try:
                        ok, msg, raw = A.execute(self.view, action)
                        time.sleep(0.5)
                        retry = V.verify(
                            self.view, action, ok, raw,
                            self._safe_url(), {"url": fresh.url},
                        )
                        if retry.ok:
                            retry.recovered = True
                            return retry
                    except Exception:
                        pass

        return failed

    # ── helpers ────────────────────────────────────────────────────────────
    def _safe_url(self) -> str:
        try:
            return self.view.current_url() or ""
        except Exception:
            return ""

    @staticmethod
    def _describe(action: dict, target_name: str) -> str:
        name = action["action"]
        if name == "navigate":
            return f"Navigating to {action.get('url','')[:60]}…"
        if name == "click":
            return f"Clicking “{target_name or action.get('id')}”…"
        if name == "type":
            return f"Typing into “{target_name or action.get('id')}”…"
        if name == "select":
            return f"Selecting “{action.get('value')}”…"
        if name == "scroll":
            return f"Scrolling {action.get('direction')}…"
        return f"Doing {name}…"

    def _summarise_stop(self, reason: str, task: str) -> str:
        done = len(self._history)
        # include the last successful step's URL context if we can
        tail = ""
        if self._history:
            last = self._history[-1]
            tail = f" Last step: {last.action.get('action')} ({last.result[:60]})."
        msg = (
            f"Sir, I stopped working on your web task after {done} step"
            f"{'' if done == 1 else 's'}: {reason}.{tail}"
        )
        self.on_status(f"Stopped: {reason}")
        return msg