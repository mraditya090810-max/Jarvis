"""Per-action JS executors. Each returns (ok, message, raw_js_value)."""
from __future__ import annotations

import json
from typing import Any, Callable

from .models import ActionError


def _json(s: str) -> str:
    """Safely embed a Python string as a JS string literal."""
    return json.dumps(s, ensure_ascii=False)


def _element_js(element_id: str, body: str) -> str:
    return f"""
    (function(){{
        try {{
            var el = document.querySelector('[data-jarvis-id={_json(element_id)}]');
            if (!el) return JSON.stringify({{ok:false, error:'element_not_found'}});
            {body}
        }} catch (e) {{
            return JSON.stringify({{ok:false, error: String(e && e.message || e)}});
        }}
    }})()
    """


# ── individual actions ─────────────────────────────────────────────────────

def a_navigate(view, action: dict) -> tuple[bool, str, Any]:
    url = action["url"]
    if "://" not in url and not url.startswith("about:"):
        url = "https://" + url if "." in url else "https://" + url + ".com"
    view.navigate(url)
    return True, f"Navigating to {url}", url


def a_click(view, action: dict) -> tuple[bool, str, Any]:
    eid = action["id"]
    js = _element_js(eid, """
        el.scrollIntoView({block:'center'});
        try { el.focus({preventScroll:true}); } catch(e){}
        try {
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
            el.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true, view:window}));
        } catch(e){}
        el.click();
        return JSON.stringify({ok:true});
    """)
    res = _run_and_parse(view, js, 4.0)
    if res.get("ok"):
        return True, "Clicked.", res
    return False, f"Click failed: {res.get('error','unknown')}", res


def a_type(view, action: dict) -> tuple[bool, str, Any]:
    eid  = action["id"]
    text = action["text"]
    clear_first = bool(action.get("clear_first", True))
    js = f"""
    (function(){{
        try {{
            var el = document.querySelector('[data-jarvis-id={_json(eid)}]');
            if (!el) return JSON.stringify({{ok:false, error:'element_not_found'}});
            el.scrollIntoView({{block:'center'}});
            el.focus();
            var text = {_json(text)};
            var clearFirst = {str(clear_first).lower()};

            if (el.isContentEditable) {{
                if (clearFirst) el.textContent = '';
                el.textContent = el.textContent + text;
            }} else {{
                var proto = el.tagName === 'TEXTAREA'
                    ? window.HTMLTextAreaElement.prototype
                    : window.HTMLInputElement.prototype;
                var desc = Object.getOwnPropertyDescriptor(proto, 'value');
                var setter = desc && desc.set;
                if (clearFirst && setter) setter.call(el, '');
                if (setter) setter.call(el, clearFirst ? text : (el.value || '') + text);
                else el.value = clearFirst ? text : (el.value || '') + text;
            }}
            el.dispatchEvent(new Event('input',  {{bubbles:true}}));
            el.dispatchEvent(new Event('change', {{bubbles:true}}));
            return JSON.stringify({{ok:true, value: String(el.value !== undefined ? el.value : el.textContent)}});
        }} catch (e) {{
            return JSON.stringify({{ok:false, error: String(e && e.message || e)}});
        }}
    }})()
    """
    res = _run_and_parse(view, js, 4.0)
    if res.get("ok"):
        return True, "Text entered.", res
    return False, f"Type failed: {res.get('error','unknown')}", res


def a_clear(view, action: dict) -> tuple[bool, str, Any]:
    eid = action["id"]
    js = _element_js(eid, """
        el.focus();
        if (el.isContentEditable) {
            el.textContent = '';
        } else {
            var proto = el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
            var d = Object.getOwnPropertyDescriptor(proto, 'value');
            if (d && d.set) d.set.call(el, ''); else el.value = '';
        }
        el.dispatchEvent(new Event('input',  {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        return JSON.stringify({ok:true});
    """)
    res = _run_and_parse(view, js, 3.0)
    return bool(res.get("ok")), "Cleared." if res.get("ok") else res.get("error", "clear failed"), res


def a_select(view, action: dict) -> tuple[bool, str, Any]:
    eid = action["id"]
    val = action["value"]
    js = f"""
    (function(){{
        try {{
            var el = document.querySelector('[data-jarvis-id={_json(eid)}]');
            if (!el) return JSON.stringify({{ok:false, error:'element_not_found'}});
            el.focus();
            var want = {_json(val)};
            var matched = null;
            for (var i=0; i<el.options.length; i++) {{
                var o = el.options[i];
                if (o.value === want || (o.text||'').trim().toLowerCase() === String(want).toLowerCase()) {{
                    matched = o.value; break;
                }}
            }}
            if (matched === null) {{
                return JSON.stringify({{ok:false, error:'option_not_found', value:String(want)}});
            }}
            el.value = matched;
            el.dispatchEvent(new Event('input',  {{bubbles:true}}));
            el.dispatchEvent(new Event('change', {{bubbles:true}}));
            return JSON.stringify({{ok:true, value: el.value}});
        }} catch (e) {{
            return JSON.stringify({{ok:false, error: String(e && e.message || e)}});
        }}
    }})()
    """
    res = _run_and_parse(view, js, 3.0)
    if res.get("ok"):
        return True, f"Selected {res.get('value')}.", res
    return False, f"Select failed: {res.get('error','unknown')}", res


def _toggle_check(view, action: dict, want: bool) -> tuple[bool, str, Any]:
    eid = action["id"]
    js = _element_js(eid, f"""
        el.focus();
        if (!!el.checked !== {str(want).lower()}) el.click();
        return JSON.stringify({{ok:true, checked: !!el.checked}});
    """)
    res = _run_and_parse(view, js, 3.0)
    if res.get("ok"):
        state = "checked" if res.get("checked") else "unchecked"
        return True, state, res
    return False, f"Toggle failed: {res.get('error','unknown')}", res


def a_check(view, action: dict):   return _toggle_check(view, action, True)
def a_uncheck(view, action: dict): return _toggle_check(view, action, False)


def a_press(view, action: dict) -> tuple[bool, str, Any]:
    key = str(action.get("key", "Enter"))
    # Dispatch a synthetic keydown/keyup on the active element / body.
    js = f"""
    (function(){{
        try {{
            var el = document.activeElement || document.body;
            var k = {_json(key)};
            var codeMap = {{
                'Enter':'Enter','Escape':'Escape','Tab':'Tab',' ':'Space',
                'ArrowUp':'ArrowUp','ArrowDown':'ArrowDown',
                'ArrowLeft':'ArrowLeft','ArrowRight':'ArrowRight'
            }};
            var keyCode = k === 'Enter' ? 13 : k === 'Escape' ? 27 : 0;
            var code = codeMap[k] || k;
            var down = new KeyboardEvent('keydown', {{key:k, code:code, keyCode:keyCode, which:keyCode, bubbles:true, cancelable:true}});
            var up   = new KeyboardEvent('keyup',   {{key:k, code:code, keyCode:keyCode, which:keyCode, bubbles:true, cancelable:true}});
            el.dispatchEvent(down);
            if (k === 'Enter' && el.form && typeof el.form.requestSubmit === 'function') {{
                try {{ el.form.requestSubmit(); }} catch(e) {{}}
            }}
            el.dispatchEvent(up);
            return JSON.stringify({{ok:true}});
        }} catch (e) {{
            return JSON.stringify({{ok:false, error: String(e && e.message || e)}});
        }}
    }})()
    """
    res = _run_and_parse(view, js, 3.0)
    return bool(res.get("ok")), f"Pressed {key}." if res.get("ok") else res.get("error", "press failed"), res


def a_scroll(view, action: dict) -> tuple[bool, str, Any]:
    d = action.get("direction", "down")
    amt = action.get("amount", 600)
    delta = amt if d == "down" else -amt
    js = f"window.scrollBy({{top:{delta}, left:0, behavior:'smooth'}}); JSON.stringify({{ok:true, scrollY: Math.round(window.scrollY)}});"
    res = _run_and_parse(view, js, 3.0)
    return True, f"Scrolled {d} {amt}px.", res


def a_back(view, action: dict) -> tuple[bool, str, Any]:
    js = "window.history.back(); JSON.stringify({ok:true});"
    _run_and_parse(view, js, 2.0)
    return True, "Going back.", None


def a_forward(view, action: dict) -> tuple[bool, str, Any]:
    js = "window.history.forward(); JSON.stringify({ok:true});"
    _run_and_parse(view, js, 2.0)
    return True, "Going forward.", None


def a_reload(view, action: dict) -> tuple[bool, str, Any]:
    js = "location.reload(); JSON.stringify({ok:true});"
    _run_and_parse(view, js, 2.0)
    return True, "Reloading.", None


def a_new_tab(view, action: dict) -> tuple[bool, str, Any]:
    # QtWebEngine's page doesn't have multi-tab; we open a new page-less tab by
    # navigating a fresh popup if supported, otherwise we just navigate.
    url = action.get("url") or "about:blank"
    view.navigate(url)
    return True, f"Opened new tab: {url}", url


def a_switch_tab(view, action: dict) -> tuple[bool, str, Any]:
    return True, "Tab switching is not supported in embedded mode.", None


def a_close_tab(view, action: dict) -> tuple[bool, str, Any]:
    view.navigate("about:blank")
    return True, "Closed tab.", None


def a_wait(view, action: dict) -> tuple[bool, str, Any]:
    secs = float(action.get("seconds", 1.0))
    import time
    time.sleep(min(secs, 20.0))
    return True, f"Waited {secs:.1f}s.", None


def a_read_text(view, action: dict) -> tuple[bool, str, Any]:
    res = _run_and_parse(
        view,
        "JSON.stringify({ok:true, text: (document.body && document.body.innerText || '').slice(0,4500)})",
        3.0,
    )
    return True, "Page text captured.", res


def a_screenshot(view, action: dict) -> tuple[bool, str, Any]:
    png = view.grab_png()
    if not png:
        return False, "Screenshot failed.", None
    return True, f"Screenshot taken ({len(png):,} bytes).", {"bytes": len(png)}


# ── dispatch table ─────────────────────────────────────────────────────────
EXECUTORS: dict[str, Callable] = {
    "navigate":   a_navigate,
    "click":      a_click,
    "type":       a_type,
    "clear":      a_clear,
    "select":     a_select,
    "check":      a_check,
    "uncheck":    a_uncheck,
    "press":      a_press,
    "scroll":     a_scroll,
    "back":       a_back,
    "forward":    a_forward,
    "reload":     a_reload,
    "new_tab":    a_new_tab,
    "switch_tab": a_switch_tab,
    "close_tab":  a_close_tab,
    "wait":       a_wait,
    "read_text":  a_read_text,
    "screenshot": a_screenshot,
}
# done/fail/ask_user are handled by the agent loop itself.


def execute(view, action: dict) -> tuple[bool, str, Any]:
    """Execute a validated action. Returns (ok, spoken_result, raw_value)."""
    name = action["action"]
    fn = EXECUTORS.get(name)
    if fn is None:
        raise ActionError(f"no executor for action {name!r}")
    return fn(view, action)


# ── helpers ────────────────────────────────────────────────────────────────

def _run_and_parse(view, js: str, timeout: float) -> dict:
    try:
        raw = view.run_js(js, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"js_error: {e}"}
    if not isinstance(raw, str):
        return {"ok": False, "error": "js_non_string"}
    try:
        return json.loads(raw)
    except Exception as e:
        return {"ok": False, "error": f"js_json_error: {e}"}