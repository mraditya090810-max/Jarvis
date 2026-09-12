"""
Post-action verification. OBSERVE → ACT → VERIFY, never ACT → ASSUME.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Verification:
    ok: bool
    detail: str = ""
    recovered: bool = False


def verify(view, action: dict, result_ok: bool, raw: Any,
           before_url: str, before_obs: dict) -> Verification:
    """
    Verify that a browser action actually had the effect we expected.
    `before_url` / `before_obs` are the URL and a dict view of the previous
    observation so we can compare structural changes.
    """
    if not result_ok:
        return Verification(False, "executor reported failure")

    name = action["action"]

    # ── navigate / reload / back / forward / new_tab / close_tab ──────────
    if name in ("navigate", "reload", "back", "forward", "new_tab", "close_tab"):
        after_url = _safe_current_url(view)
        if not after_url:
            return Verification(False, "no URL after navigation")
        if name == "navigate":
            target = str(action.get("url", "")).lower()
            if target and target not in after_url.lower() and \
               after_url.rstrip("/").lower() != target.rstrip("/"):
                # allow redirects: only flag when we ended on about:blank
                if after_url in ("about:blank", ""):
                    return Verification(False, f"navigation did not change URL (still {after_url})")
        if name == "reload" and after_url == before_url:
            return Verification(True, "reload (URL unchanged, expected)")
        return Verification(True, f"now at {after_url[:80]}")

    # ── type / clear: read the value back ─────────────────────────────────
    if name == "type":
        want = action["text"]
        got = _read_value(view, action["id"])
        if got is None:
            return Verification(False, "could not read field value back")
        if want and want not in got:
            return Verification(False, f"field contains {got[:40]!r}, expected to contain {want[:40]!r}")
        return Verification(True, "field value matches")

    if name == "clear":
        got = _read_value(view, action["id"])
        if got:
            return Verification(False, f"field still contains {got[:40]!r}")
        return Verification(True, "field cleared")

    # ── select: verify option chosen ──────────────────────────────────────
    if name == "select":
        got = _read_value(view, action["id"])
        want = action["value"]
        if got is None or str(got) != str(want):
            # some sites normalise value; accept either direction
            if got is None or (want not in str(got) and str(got) not in str(want)):
                return Verification(False, f"select landed on {got!r}, wanted {want!r}")
        return Verification(True, "option selected")

    # ── check / uncheck ───────────────────────────────────────────────────
    if name in ("check", "uncheck"):
        want = name == "check"
        got = _read_checked(view, action["id"])
        if got is None:
            return Verification(False, "could not read checked state")
        if bool(got) != want:
            return Verification(False, f"checked={got}, wanted {want}")
        return Verification(True, "checkbox state matches")

    # ── click: heuristic — URL change, text change, or element-set change ─
    if name == "click":
        after_url = _safe_current_url(view)
        if after_url and after_url != before_url:
            return Verification(True, f"URL changed → {after_url[:80]}")
        # If URL did not change, we can't reliably verify a DOM change without
        # a full re-observation. Treat the click as "assumed OK" — the next
        # observation will reveal whether the page moved. Mark as soft OK.
        return Verification(True, "click dispatched (no navigation expected)")

    # ── press / scroll / wait / read_text / screenshot: no hard contract ──
    if name in ("press", "scroll", "wait", "read_text", "screenshot"):
        after_url = _safe_current_url(view)
        if after_url and after_url != before_url and name == "press":
            return Verification(True, f"key triggered navigation → {after_url[:80]}")
        return Verification(True, "dispatched")

    return Verification(True, "no verification rule")


# ── helpers ────────────────────────────────────────────────────────────────

def _safe_current_url(view) -> str:
    try:
        return view.current_url() or ""
    except Exception:
        return ""


def _read_value(view, element_id: str):
    js = f"""
    (function(){{
        try {{
            var el = document.querySelector('[data-jarvis-id="{element_id}"]');
            if (!el) return JSON.stringify({{ok:false, error:'missing'}});
            return JSON.stringify({{ok:true, value: String(el.value !== undefined ? el.value : (el.textContent || ''))}});
        }} catch (e) {{ return JSON.stringify({{ok:false, error:String(e)}}); }}
    }})()
    """
    try:
        raw = view.run_js(js, timeout=3.0)
        import json
        data = json.loads(raw) if isinstance(raw, str) else {}
        return data.get("value")
    except Exception:
        return None


def _read_checked(view, element_id: str):
    js = f"""
    (function(){{
        try {{
            var el = document.querySelector('[data-jarvis-id="{element_id}"]');
            if (!el) return JSON.stringify({{ok:false}});
            return JSON.stringify({{ok:true, checked: !!el.checked}});
        }} catch (e) {{ return JSON.stringify({{ok:false}}); }}
    }})()
    """
    try:
        raw = view.run_js(js, timeout=3.0)
        import json
        data = json.loads(raw) if isinstance(raw, str) else {}
        return data.get("checked")
    except Exception:
        return None