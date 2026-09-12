"""DOM → compact structured observation. One JS round-trip per observation."""
from __future__ import annotations

import json

from .models import Observation, MAX_TEXT_CHARS, MAX_ELEMENTS


# This is injected into every observation. It assigns a *stable per-session*
# data-jarvis-id to every visible interactive element so the model can refer
# to elements by short IDs across turns without re-identifying them.
_INSPECT_JS = r"""
(function(){
    try {
        function isVisible(el){
            if (!el) return false;
            var s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden' ||
                parseFloat(s.opacity || '1') < 0.05) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return false;
            return true;
        }
        function textOf(el){
            if (!el) return '';
            return (el.innerText || el.textContent || '').trim();
        }
        function accName(el){
            var a = el.getAttribute && el.getAttribute('aria-label');
            if (a && a.trim()) return a.trim().slice(0,120);
            var lb = el.getAttribute && el.getAttribute('aria-labelledby');
            if (lb){
                var lbl = document.getElementById(lb);
                if (lbl){ var t = textOf(lbl); if (t) return t.slice(0,120); }
            }
            var ti = el.getAttribute && el.getAttribute('title');
            if (ti && ti.trim()) return ti.trim().slice(0,120);
            var ph = el.getAttribute && el.getAttribute('placeholder');
            if (ph && ph.trim()) return ph.trim().slice(0,120);
            var al = el.getAttribute && el.getAttribute('alt');
            if (al && al.trim()) return al.trim().slice(0,120);
            var id = el.id;
            if (id){
                try {
                    var l = document.querySelector('label[for="' + CSS.escape(id) + '"]');
                    if (l){ var lt = textOf(l); if (lt) return lt.slice(0,120); }
                } catch(e){}
            }
            if (el.closest){
                var pl = el.closest('label');
                if (pl){ var pt = textOf(pl); if (pt) return pt.slice(0,120); }
            }
            if (el.value && el.tagName === 'INPUT' &&
                (el.type === 'submit' || el.type === 'button')){
                return String(el.value).slice(0,120);
            }
            var txt = textOf(el);
            if (txt) return txt.slice(0,120);
            var nm = el.getAttribute && el.getAttribute('name');
            if (nm) return String(nm).slice(0,120);
            return '';
        }

        var SEL = 'a,button,input,textarea,select,' +
                  '[role="button"],[role="link"],[role="textbox"],' +
                  '[role="searchbox"],[role="combobox"],[role="checkbox"],' +
                  '[role="radio"],[role="menuitem"],[role="tab"],' +
                  '[role="option"],[contenteditable="true"]';
        var nodes = document.querySelectorAll(SEL);
        var elements = [];
        var counter = 0;
        for (var i = 0; i < nodes.length; i++){
            if (elements.length >= __MAX_ELEMENTS__) break;
            var el = nodes[i];
            if (!isVisible(el)) continue;
            if (el.disabled) continue;
            var jid = el.getAttribute('data-jarvis-id');
            if (!jid){
                jid = 'j' + (counter++) + '_' + Math.random().toString(36).slice(2,6);
                try { el.setAttribute('data-jarvis-id', jid); } catch(e){}
            }
            var r = el.getBoundingClientRect();
            var item = {
                id: jid,
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                role: el.getAttribute('role') || '',
                name: accName(el),
                disabled: !!el.disabled,
                x: Math.round(r.left + r.width / 2),
                y: Math.round(r.top + r.height / 2),
            };
            if (el.value !== undefined && el.tagName !== 'SELECT'){
                item.value = String(el.value).slice(0,200);
            }
            if (el.tagName === 'SELECT'){
                var opts = [];
                var lim = Math.min(el.options.length, 30);
                for (var k = 0; k < lim; k++){
                    opts.push({
                        value: el.options[k].value,
                        text: (el.options[k].text || '').slice(0,80),
                    });
                }
                item.options = opts;
                item.value = String(el.value);
            }
            if (el.checked !== undefined) item.checked = !!el.checked;
            if (el.tagName === 'A' && el.href) item.href = String(el.href).slice(0,250);
            elements.push(item);
        }

        var bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';

        var hasCookieBanner = false;
        try {
            hasCookieBanner =
                !!(document.querySelector(
                    '[id*="cookie" i],[class*="cookie" i],' +
                    '[id*="consent" i],[class*="consent" i],' +
                    '[id*="gdpr" i],[class*="gdpr" i]'
                )) ||
                /(accept|agree|allow) (all )?(cookies|consent)/i.test(bodyText);
        } catch(e){}

        var hasCaptcha = false;
        try {
            hasCaptcha = !!(document.querySelector(
                'iframe[src*="recaptcha" i],iframe[src*="hcaptcha" i],' +
                'iframe[src*="turnstile" i],' +
                '[class*="captcha" i],[id*="captcha" i]'
            ));
        } catch(e){}

        return JSON.stringify({
            url: String(location.href),
            title: String(document.title || ''),
            text: bodyText.slice(0, __MAX_TEXT__),
            readyState: document.readyState,
            scrollY: Math.round(window.scrollY || 0),
            scrollHeight: document.documentElement ? document.documentElement.scrollHeight : 0,
            viewportH: window.innerHeight,
            elements: elements,
            hints: {
                hasCookieBanner: hasCookieBanner,
                hasCaptcha: hasCaptcha,
            },
        });
    } catch (err) {
        return JSON.stringify({
            url: String(location.href),
            title: String(document.title || ''),
            text: '',
            elements: [],
            hints: { hasError: String(err && err.message || err) },
            error: 'inspect failed',
        });
    }
})();
"""


def _build_js() -> str:
    return (_INSPECT_JS
            .replace("__MAX_ELEMENTS__", str(MAX_ELEMENTS))
            .replace("__MAX_TEXT__", str(MAX_TEXT_CHARS)))


_INSPECT_JS_FINAL = _build_js()


def observe(view) -> Observation:
    """Run the inspector against the live BrowserView. Never raises."""
    obs = Observation()
    try:
        raw = view.run_js(_INSPECT_JS_FINAL, timeout=6.0)
    except Exception as e:
        obs.error = f"inspect error: {e}"
        return obs

    if not isinstance(raw, str):
        obs.error = "inspect returned non-string"
        return obs

    try:
        data = json.loads(raw)
    except Exception as e:
        obs.error = f"inspect json parse: {e}"
        return obs

    obs.url           = str(data.get("url", ""))
    obs.title         = str(data.get("title", ""))
    obs.text          = str(data.get("text", ""))[:MAX_TEXT_CHARS]
    obs.ready_state   = str(data.get("readyState", ""))
    obs.elements      = list(data.get("elements", []) or [])
    obs.hints         = dict(data.get("hints", {}) or {})
    obs.scroll_y      = int(data.get("scrollY", 0) or 0)
    obs.scroll_height = int(data.get("scrollHeight", 0) or 0)
    obs.viewport_h    = int(data.get("viewportH", 0) or 0)
    obs.error         = str(data.get("error", "") or "")
    return obs


def find_element_name(obs: Observation, element_id: str) -> str:
    for el in obs.elements:
        if el.get("id") == element_id:
            return str(el.get("name") or el.get("tag") or "")
    return ""