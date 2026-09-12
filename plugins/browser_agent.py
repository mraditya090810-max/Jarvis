"""
browser_agent — autonomous, multi-step browser tool for JARVIS.

    browser_control -> one quick action (open a URL, one click, one search).
    browser_agent   -> a whole task: INSPECT the page, decide the next move,
                       ACT, then INSPECT again to VERIFY it worked — looping
                       until the task is done or it genuinely needs the user.

The browser is real Chromium (Playwright), but it is launched 100% headless:
no OS window is ever created, on any platform. Instead, every step's
screenshot is streamed straight into the HUD area — the same panel the
camera feed uses — so the user watches the agent work without a Chrome
window ever appearing on their desktop.

Decision-making uses a local Ollama model only for this browser agent.
The rest of JARVIS keeps using its existing AI providers. No browser-agent
requests are sent to OpenRouter/Gemini/Groq.

Never raises: every failure path returns a short spoken string instead.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Same bootstrap pattern as actions/dev_agent.py — plugins/ is one level
# under the project root, so make `core` importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
import requests

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except Exception:  # Playwright not installed — plugin still loads, run() reports it
    sync_playwright = None
    PWTimeout = Exception

try:
    from actions.file_controller import extract_file
    from actions._paths import get_special_folder
except Exception:  # Keep the plugin loadable even if these move/rename
    extract_file = None
    get_special_folder = None


def _default_downloads_dir() -> Path:
    if get_special_folder is not None:
        try:
            return Path(get_special_folder("downloads"))
        except Exception:
            pass
    return Path.home() / "Downloads"


_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
_EXTRACT_INTENT_RE = re.compile(r"extract|unzip|unpack", re.IGNORECASE)


PLUGIN = {
    "name": "browser_agent",
    "description": (
        "Runs an autonomous, multi-step browser AGENT that inspects a page, decides the "
        "next action, acts, then inspects again to verify it worked — looping until the "
        "task is complete or it genuinely needs the user. Use this for tasks that take "
        "several steps to reach a goal, for example: 'open this website and fill out the "
        "form', 'go to the settings page and change this option', 'find the registration "
        "page and complete the non-sensitive fields', 'navigate through this website and "
        "find the information I asked for', 'search a site, open the relevant result, and "
        "verify the information'. "
        "The browser is real Chromium but runs fully headless — no visible Chrome window "
        "ever opens; the live page is streamed into the HUD instead, the same area used "
        "for the camera feed, so the user can watch it work. "
        "Do NOT use this for a single quick action (just opening one URL, one click, one "
        "search) — use browser_control for that instead; browser_agent is slower and only "
        "worth it for tasks that genuinely need several inspect-act-verify steps. "
        "This tool will never type into password, credit-card, CVV/CVC, PIN, OTP, SSN, or "
        "bank-account fields — it skips those automatically and reports back which ones "
        "still need the user's own input. "
        "It can also download files it finds (saved to the user's Downloads folder unless "
        "a destination folder is given in the task) and, if the task asks to extract/unzip "
        "the result, it will automatically unpack the downloaded archive there too. "
        "If this tool's result says it had trouble, was blocked, or needs the user, that IS "
        "the final answer — relay it to the user as-is. Do NOT then call browser_control or "
        "web_search to try to finish the same task instead; that would open a real, visible "
        "browser window, which is exactly what this tool exists to avoid."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {
                "type": "STRING",
                "description": (
                    "Full natural-language description of what to accomplish, including "
                    "the site/page if known (e.g. 'On example.com, go to Settings and turn "
                    "on dark mode' or 'Search openai.com for the pricing page and tell me "
                    "the cheapest plan')."
                ),
            },
            "start_url": {
                "type": "STRING",
                "description": (
                    "Optional. The URL or bare domain to start on (e.g. 'example.com'). "
                    "If omitted, the agent extracts a site from the task text, or falls "
                    "back to a Google search of the task."
                ),
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Optional safety cap on inspect-act-verify steps (default 20, max 40).",
            },
        },
        "required": ["task"],
    },
}

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
DEFAULT_MAX_STEPS   = 20
HARD_MAX_STEPS      = 40
STEP_TIMEOUT_MS     = 15_000
MAX_DECISION_FAILURES = 5   # AI-router/bad-JSON — each already retried twice internally, so allow more
MAX_ACTION_FAILURES   = 4   # click/type/navigate actually failing on the page
MAX_ELEMENTS        = 60
SCREENSHOT_QUALITY  = 55

_SENSITIVE_RE = re.compile(
    r"password|passwd|\bpin\b|\botp\b|cvv|cvc|security[\s_-]?code|"
    r"card[\s_-]?number|credit[\s_-]?card|debit[\s_-]?card|expir|"
    r"social[\s_-]?security|\bssn\b|bank[\s_-]?account|routing[\s_-]?number|"
    r"\biban\b|passport[\s_-]?number|national[\s_-]?id",
    re.IGNORECASE,
)

# One agent run at a time — the HUD only has one live-feed slot.
_run_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" in url:
        return url
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _extract_site(task: str) -> str:
    """Best-effort: pull a URL/domain out of the task text so the agent has
    somewhere to start when no explicit start_url was given."""
    m = re.search(r"https?://\S+", task)
    if m:
        return m.group(0)
    m = re.search(r"\b([a-zA-Z0-9-]+\.(?:com|org|net|io|co|gov|edu|app|dev))\b", task)
    if m:
        return m.group(1)
    return ""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_sensitive(el: dict) -> bool:
    if str(el.get("type", "")).lower() == "password":
        return True
    haystack = " ".join(
        str(el.get(k, "")) for k in ("label", "text", "name", "type")
    )
    return bool(_SENSITIVE_RE.search(haystack))


_OBSERVE_JS = """
() => {
    const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], ' +
                '[role="textbox"], [role="checkbox"], [role="radio"], [role="tab"], ' +
                '[role="menuitem"], [contenteditable="true"], [onclick]';
    const nodes = Array.from(document.querySelectorAll(sel));
    const items = [];
    let i = 0;
    for (const el of nodes) {
        if (i >= %(max)d) break;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        const tag = el.tagName.toLowerCase();
        let text = (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
        const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                      el.getAttribute('name') || el.getAttribute('title') || '';
        const type = el.getAttribute('type') || '';
        const role = el.getAttribute('role') || tag;
        el.setAttribute('data-jarvis-id', String(i));
        items.push({id: i, tag, role, type, label, text});
        i++;
    }
    return items;
}
""" % {"max": MAX_ELEMENTS}


def _observe(page) -> tuple[dict, list[dict]]:
    """Returns (page_state, elements). Never raises — best-effort on a live page."""
    try:
        url = page.url
    except Exception:
        url = "?"
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        elements = page.evaluate(_OBSERVE_JS)
    except Exception:
        elements = []
    return {"url": url, "title": title}, elements


def _screenshot_jpeg(page) -> Optional[bytes]:
    try:
        return page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY, full_page=False)
    except Exception:
        return None


def _format_elements(elements: list[dict]) -> str:
    if not elements:
        return "(no interactive elements detected)"
    lines = []
    for el in elements:
        bits = [f"[{el['id']}] <{el.get('tag', '?')}>"]
        if el.get("role") and el["role"] != el.get("tag"):
            bits.append(f"role={el['role']}")
        if el.get("type"):
            bits.append(f"type={el['type']}")
        if el.get("label"):
            bits.append(f'label="{el["label"]}"')
        if el.get("text"):
            bits.append(f'text="{el["text"]}"')
        lines.append(" ".join(bits))
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are the decision-making core of a browser automation agent. Each turn you are "
    "shown the user's task, the current page URL/title, and a numbered list of the "
    "interactive elements currently visible on the page. You must choose exactly ONE "
    "next action and reply with ONLY a single-line JSON object — no markdown, no prose, "
    "no explanation outside the JSON.\n\n"
    "JSON shape: {\"thought\": string, \"action\": string, \"id\": int, \"text\": string, "
    "\"url\": string, \"key\": string, \"direction\": string, \"amount\": int, "
    "\"seconds\": number, \"note\": string, \"message\": string, \"path\": string}\n"
    "Only include the keys the chosen action needs; omit the rest.\n\n"
    "Valid actions:\n"
    "  navigate   {url}                 - go to a URL\n"
    "  click      {id}                  - click the element with that id\n"
    "  type       {id, text}            - type text into that element (clears it first)\n"
    "  press_key  {key}                 - press a keyboard key, e.g. Enter, Escape\n"
    "  scroll     {direction, amount}   - direction is 'up' or 'down', amount in pixels\n"
    "  wait       {seconds}             - pause briefly for something to load (max 5)\n"
    "  extract    {note}                - record a piece of information you found; keep looping\n"
    "  download   {id, path}            - click a download link/button (id); optional path "
    "sets the destination folder (defaults to the user's Downloads folder); if the task asked "
    "to extract/unzip, a downloaded archive is unpacked there automatically\n"
    "  ask_user   {message}             - stop and ask the user; use this ONLY when truly "
    "blocked (e.g. a required field is sensitive, a CAPTCHA appears, or the task is "
    "genuinely ambiguous)\n"
    "  finish     {message}             - the task is complete; message summarizes the "
    "result for the user, including anything you extracted\n\n"
    "Rules:\n"
    "- Never invent an element id that isn't in the current list.\n"
    "- After every action you will see a fresh element list — use it to verify the action "
    "worked before deciding the next one; if it clearly didn't work, try a different "
    "approach instead of repeating the same action.\n"
    "- If an element you'd need to type into looks like a password, card number, CVV, PIN, "
    "OTP, SSN, or bank-account field, do NOT choose 'type' for it — it will be refused "
    "anyway. Skip it, keep going with everything else, and mention it in the final "
    "'finish' or 'ask_user' message.\n"
    "- Prefer finishing as soon as the task's goal is verifiably met. Do not keep exploring "
    "once you have the answer or the form/action is done."
)


def _build_user_prompt(task: str, step: int, max_steps: int, state: dict,
                        elements: list[dict], history: list[str],
                        extracted: list[str], last_error: str) -> str:
    parts = [
        f"TASK: {task}",
        f"STEP: {step}/{max_steps}",
        f"CURRENT PAGE: {state.get('title', '')!r} — {state.get('url', '')}",
    ]
    if last_error:
        parts.append(f"LAST ACTION RESULT: {last_error}")
    if history:
        parts.append("RECENT STEPS:\n" + "\n".join(history[-6:]))
    if extracted:
        parts.append("EXTRACTED SO FAR:\n" + "\n".join(extracted[-10:]))
    parts.append("VISIBLE ELEMENTS:\n" + _format_elements(elements))
    parts.append("Reply with the single-line JSON action now.")
    return "\n\n".join(parts)


def _extract_json(text: str) -> Optional[dict]:
    """Fast path: the whole reply is already valid JSON. Fallback: scan for
    the first balanced {...} object anywhere in the text (handles models
    that add a sentence of preamble despite instructions not to)."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:i + 1])
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def _ollama_config() -> tuple[str, str, float]:
    """Return (host, model, timeout) for the browser-agent-only Ollama backend."""
    host = (os.getenv("JARVIS_BROWSER_OLLAMA_HOST") or
            os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").strip().rstrip("/")
    model = (os.getenv("JARVIS_BROWSER_OLLAMA_MODEL") or
             "qwen2.5:3b").strip()
    try:
        timeout = float(os.getenv("JARVIS_BROWSER_OLLAMA_TIMEOUT", "60"))
    except Exception:
        timeout = 60.0
    return host, model, max(10.0, min(timeout, 180.0))


def _ollama_chat(prompt: str) -> str:
    """Ask only the local Ollama server for the browser-agent decision.

    This intentionally does NOT fall back to core.ai or any cloud provider.
    """
    host, model, timeout = _ollama_config()
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 500,
        },
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(
            f"Ollama is not reachable at {host}. Start Ollama and make sure model "
            f"'{model}' is installed. ({e})"
        ) from e

    if response.status_code != 200:
        detail = response.text[:500].replace("\n", " ")
        if response.status_code == 404 and "model" in detail.lower():
            raise RuntimeError(
                f"Ollama model '{model}' is not installed. Run: ollama pull {model}"
            )
        raise RuntimeError(f"Ollama HTTP {response.status_code}: {detail}")

    try:
        data = response.json()
    except Exception as e:
        raise RuntimeError("Ollama returned invalid JSON.") from e

    message = data.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama returned an empty browser-agent decision.")
    return content.strip()


def _decide_next_action(prompt: str, player=None) -> Optional[dict]:
    """Get the next browser action from local Ollama only.

    Two attempts are allowed for malformed JSON, but there is deliberately no
    cloud-model fallback. This keeps browser-agent usage isolated from the rest
    of JARVIS and avoids OpenRouter free-model rate-limit cascades.
    """
    last_err = ""
    current_prompt = prompt
    for attempt in range(2):
        try:
            raw = _ollama_chat(current_prompt)
        except Exception as e:
            last_err = str(e)
            break

        cleaned = _strip_fences(raw)
        data = _extract_json(cleaned)
        if isinstance(data, dict) and "action" in data:
            return data

        last_err = f"Ollama reply wasn't valid JSON: {cleaned[:180]!r}"
        current_prompt = prompt + (
            "\n\nYour previous reply was invalid. Reply with ONLY one JSON object "
            "with a valid 'action' field. Do not include markdown or prose."
        )

    print(f"[BrowserAgent] Ollama decision failed: {last_err}")
    if player:
        try:
            player.write_log(f"[browser_agent] Ollama decision failed: {last_err[:140]}")
        except Exception:
            pass
    return None


def _log(player, text: str) -> None:
    short = str(text)[:160]
    print(f"[BrowserAgent] {short}")
    if player:
        try:
            player.write_log(f"[browser_agent] {short[:80]}")
        except Exception:
            pass


def _hud_start(player, label: str) -> None:
    if player and hasattr(player, "start_browser_stream"):
        try:
            player.start_browser_stream(label)
        except Exception:
            pass


def _hud_frame(player, frame: Optional[bytes]) -> None:
    if player and frame and hasattr(player, "push_browser_frame"):
        try:
            player.push_browser_frame(frame)
        except Exception:
            pass


def _hud_stop(player) -> None:
    if player and hasattr(player, "stop_browser_stream"):
        try:
            player.stop_browser_stream()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    task = (params.get("task") or "").strip()
    if not task:
        return "Sir, I need a task description to run the browser agent."

    if sync_playwright is None:
        return "Sir, Playwright isn't installed, so I can't run the browser agent. Run: pip install playwright && playwright install chromium"

    try:
        max_steps = int(params.get("max_steps") or DEFAULT_MAX_STEPS)
    except Exception:
        max_steps = DEFAULT_MAX_STEPS
    max_steps = max(1, min(max_steps, HARD_MAX_STEPS))

    if not _run_lock.acquire(blocking=False):
        return "Sir, a browser agent task is already running — please wait for it to finish first."

    hud_label = f"◈  BROWSER AGENT — {task[:34]}"
    _hud_start(player, hud_label)
    _log(player, f"Starting: {task}")

    history: list[str] = []
    extracted: list[str] = []
    last_error = ""
    decision_failures = 0   # AI-router / bad-JSON failures — usually transient, tolerate more
    action_failures = 0     # click/type/navigate actually failing on the page — a real stuck agent
    final_message = None

    browser = context = page = pw = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)

        start_url = _normalize_url(params.get("start_url") or "") or _normalize_url(_extract_site(task))
        if not start_url:
            start_url = "https://www.google.com/search?q=" + task.replace(" ", "+")
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=25_000)
        except Exception as e:
            history.append(f"navigate({start_url}) -> could not fully load: {e}")

        step = 0
        while step < max_steps:
            step += 1
            time.sleep(0.2)  # let any pending JS/render settle before observing
            state, elements = _observe(page)
            _hud_frame(player, _screenshot_jpeg(page))

            prompt = _build_user_prompt(task, step, max_steps, state, elements,
                                         history, extracted, last_error)
            decision = _decide_next_action(prompt, player=player)
            last_error = ""

            if decision is None:
                decision_failures += 1
                history.append("decision -> could not get a valid next action from the model "
                                "(see log for the exact reason)")
                if decision_failures >= MAX_DECISION_FAILURES:
                    final_message = (
                        "Sir, the local Ollama browser-agent model could not make a decision. "
                        "Make sure Ollama is running and the configured model is installed. "
                        "Check the browser-agent log for the exact error."
                    )
                    break
                continue

            action = str(decision.get("action", "")).strip().lower()
            thought = str(decision.get("thought", ""))[:140]

            try:
                if action == "navigate":
                    url = _normalize_url(decision.get("url", ""))
                    if not url:
                        last_error = "navigate had no url"
                    else:
                        page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                        history.append(f"navigate({url})")

                elif action == "click":
                    eid = decision.get("id")
                    loc = page.locator(f'[data-jarvis-id="{eid}"]').first
                    loc.scroll_into_view_if_needed(timeout=5_000)
                    loc.click(timeout=8_000)
                    page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    history.append(f"click(id={eid}) — {thought}")

                elif action == "type":
                    eid = decision.get("id")
                    text = str(decision.get("text", ""))
                    target_el = next((e for e in elements if e.get("id") == eid), {})
                    if _is_sensitive(target_el):
                        history.append(f"type(id={eid}) SKIPPED — looks like a sensitive field")
                        last_error = (f"Refused to type into element [{eid}] — it looks like a "
                                       f"password/card/PIN/SSN-type field. Skip it and continue; "
                                       f"the user will fill that one in themselves.")
                    else:
                        loc = page.locator(f'[data-jarvis-id="{eid}"]').first
                        loc.scroll_into_view_if_needed(timeout=5_000)
                        loc.click(timeout=5_000)
                        loc.fill("")
                        loc.type(text, delay=20)
                        history.append(f"type(id={eid}, text={text[:24]!r}) — {thought}")

                elif action == "press_key":
                    key = str(decision.get("key", "Enter"))
                    page.keyboard.press(key)
                    page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    history.append(f"press_key({key})")

                elif action == "scroll":
                    direction = str(decision.get("direction", "down"))
                    amount = int(decision.get("amount", 600) or 600)
                    y = amount if direction == "down" else -amount
                    page.mouse.wheel(0, y)
                    history.append(f"scroll({direction}, {amount})")

                elif action == "wait":
                    seconds = min(float(decision.get("seconds", 1) or 1), 5.0)
                    time.sleep(seconds)
                    history.append(f"wait({seconds}s)")

                elif action == "extract":
                    note = str(decision.get("note", "")).strip()
                    if note:
                        extracted.append(note)
                    history.append(f"extract: {note[:80]}")

                elif action == "download":
                    eid = decision.get("id")
                    dest_raw = str(decision.get("path") or "").strip()
                    save_dir = Path(dest_raw) if dest_raw else _default_downloads_dir()
                    try:
                        save_dir.mkdir(parents=True, exist_ok=True)
                        with page.expect_download(timeout=20_000) as dl_info:
                            if eid is not None:
                                loc = page.locator(f'[data-jarvis-id="{eid}"]').first
                                loc.scroll_into_view_if_needed(timeout=5_000)
                                loc.click(timeout=8_000)
                            else:
                                last_error = "download had no target id"
                        download = dl_info.value
                        suggested = download.suggested_filename or "download"
                        target = save_dir / suggested
                        download.save_as(str(target))
                        note = f"Downloaded '{suggested}' to {target}"
                        extracted.append(note)
                        history.append(f"download(id={eid}) -> {target}")

                        if (extract_file is not None
                                and target.suffix.lower() in _ARCHIVE_EXTS
                                and _EXTRACT_INTENT_RE.search(task)):
                            try:
                                ex_result = extract_file(str(save_dir), name=target.name,
                                                          destination=str(save_dir))
                                extracted.append(ex_result)
                                history.append(f"auto-extract: {ex_result}")
                            except Exception as e:
                                history.append(f"auto-extract failed: {e}")
                    except PWTimeout:
                        last_error = ("download timed out — clicking that element did not start "
                                      "a file download within 20s")
                        history.append(last_error)
                    except Exception as e:
                        last_error = f"download failed: {e}"
                        history.append(last_error)

                elif action == "ask_user":
                    final_message = str(decision.get("message", "")).strip() or \
                        "Sir, I need your input to continue with this one."
                    break

                elif action == "finish":
                    final_message = str(decision.get("message", "")).strip() or "Task completed."
                    break

                else:
                    last_error = f"unknown action '{action}'"
                    history.append(last_error)

                action_failures = 0
                decision_failures = 0

            except PWTimeout as e:
                action_failures += 1
                last_error = f"{action} timed out: {e}"
                history.append(last_error)
            except Exception as e:
                action_failures += 1
                last_error = f"{action} failed: {e}"
                history.append(last_error)

            if action_failures >= MAX_ACTION_FAILURES:
                final_message = ("Sir, I kept running into errors trying to act on this page "
                                  "and couldn't finish it safely. You may want to take over "
                                  "from here.")
                break

        if final_message is None:
            final_message = (f"Sir, I reached the {max_steps}-step limit before finishing. "
                              f"Here's what I found so far: " + ("; ".join(extracted) if extracted
                              else "nothing conclusive yet."))

    except Exception as e:
        final_message = f"Sir, the browser agent hit an unexpected error: {e}"
    finally:
        for obj in (context, browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        _hud_stop(player)
        _run_lock.release()

    if extracted and "extracted" not in final_message.lower() and \
            not any(note in final_message for note in extracted):
        final_message = final_message.rstrip(".") + ". " + " ".join(extracted[-3:])

    _log(player, f"Done: {final_message}")
    return final_message
