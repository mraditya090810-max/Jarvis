"""
plugins/whatsapp_auto_reply.py — transparent WhatsApp auto-reply.

While you're away, this watches WhatsApp Desktop for new 1:1 messages and
sends a short, context-aware reply in Hinglish on your behalf — but it
ALWAYS makes clear the reply is coming from your assistant, not from you.
It never impersonates you, never pretends to be a person, and never hides
what it is. That disclosure rule is not configurable.

It also, by design:
  - only handles 1:1 chats by default (skip_groups=True) — group auto-reply
    is riskier (more people misled at once) and off unless you turn it on.
  - never invents commitments, plans, or promises on your behalf — it can
    acknowledge, relay, and answer simple factual/logistic questions it's
    actually confident about; anything else it just notes "I'll pass this
    along."
  - re-sends the disclosure once per contact per day, not on every message,
    so a back-and-forth doesn't read like a broken record — but a new
    contact, or the same contact the next day, gets it again.
  - keeps a small on-disk log of what it sent, so you can review it later.

Actions: start, stop, status.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import whatsapp_watcher as ww
from core.ai import call_llm_text
from core.paths import get_base_dir


PLUGIN = {
    "name": "whatsapp_auto_reply",
    "description": (
        "Starts/stops/checks a background watcher that replies to your WhatsApp messages "
        "while you're away. It ALWAYS discloses it's your assistant replying, not you — it "
        "never pretends to be you or hides what it is. It reads each conversation's recent "
        "context first, then replies briefly in natural Hinglish, handling only 1:1 chats "
        "unless group replies are explicitly enabled. Use action='start' when the user says "
        "something like 'reply for me on WhatsApp while I'm out', action='stop' when they're "
        "back or want it off, action='status' to check whether it's running."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: start, stop, status.",
            },
            "reason": {
                "type": "STRING",
                "description": (
                    "Optional short context for WHY the user is away, e.g. 'in a meeting "
                    "till 5pm', 'driving', 'asleep'. Shapes the reply tone/wording."
                ),
            },
            "duration_minutes": {
                "type": "INTEGER",
                "description": "Optional. Auto-stop after this many minutes (default: no limit).",
            },
            "allow_groups": {
                "type": "BOOLEAN",
                "description": "Optional. Also auto-reply in group chats. Default false.",
            },
        },
        "required": ["action"],
    },
}

POLL_SECONDS = 20
STATE_PATH = get_base_dir() / "memory" / "whatsapp_auto_reply_state.json"

_SYSTEM_PROMPT = (
    "You are drafting a WhatsApp reply on behalf of a JARVIS-style personal assistant. "
    "The assistant is standing in for its user, who is currently away, and is replying to "
    "someone who messaged the user directly.\n\n"
    "Absolute rules:\n"
    "- Never claim to be the user. Never use the user's name as if you ARE them. You are "
    "their assistant, speaking as their assistant.\n"
    "- Do not invent plans, promises, prices, times, or commitments the user hasn't actually "
    "made. If the message needs a real decision from the user, say you'll pass it along.\n"
    "- You may answer simple factual or logistic questions ONLY if the provided context "
    "already makes the answer obvious; otherwise just acknowledge and say you'll relay it.\n"
    "- Keep it short — 1 to 3 sentences, like a real WhatsApp message, not an essay.\n"
    "- Write naturally in Hinglish (Hindi-English mix, Latin script), the way a young Indian "
    "assistant would actually text — not textbook Hindi, not stiff English.\n"
    "- If disclose=true, the very first line must clearly and warmly state you're the "
    "user's assistant, before anything else. If disclose=false, skip the disclosure line "
    "(already given earlier today) and just reply naturally.\n"
    "- Never sound robotic or like a canned auto-reply template — vary your phrasing."
)


# ---------------------------------------------------------------------------
# State: per-contact "last disclosed" date + "last replied-to message" so we
# don't re-disclose every message or double-reply to the same message.
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[WhatsAppAutoReply] Could not save state: {e}")


def _needs_disclosure(state: dict, contact: str) -> bool:
    today = date.today().isoformat()
    return state.get(contact, {}).get("last_disclosed") != today


def _mark_disclosed(state: dict, contact: str) -> None:
    state.setdefault(contact, {})["last_disclosed"] = date.today().isoformat()


def _already_handled(state: dict, contact: str, last_line: str) -> bool:
    return state.get(contact, {}).get("last_seen_message") == last_line


def _mark_handled(state: dict, contact: str, last_line: str, reply: str) -> None:
    entry = state.setdefault(contact, {})
    entry["last_seen_message"] = last_line
    log = entry.setdefault("log", [])
    log.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "sent": reply})
    entry["log"] = log[-20:]  # keep it small


# ---------------------------------------------------------------------------
# Reply generation
# ---------------------------------------------------------------------------
def _build_reply(ctx: "ww.ChatContext", reason: str, disclose: bool) -> str:
    transcript_lines = [f"{'You' if side == 'you' else 'Them'}: {txt}" for side, txt in ctx.transcript]
    transcript_str = "\n".join(transcript_lines) if transcript_lines else "(no prior context visible)"

    away_reason = reason or "not specified, just say you're unavailable right now"
    prompt = (
        f"Contact name: {ctx.contact_name}\n"
        f"Why the user is away: {away_reason}\n"
        f"disclose: {'true' if disclose else 'false'}\n\n"
        f"Recent conversation:\n{transcript_str}\n\n"
        "Write the WhatsApp reply now. Reply with ONLY the message text — no quotes, "
        "no labels, no explanation."
    )
    try:
        reply = call_llm_text(prompt, system=_SYSTEM_PROMPT, timeout=45,
                               task="whatsapp_auto_reply").strip()
    except Exception as e:
        reply = None
        print(f"[WhatsAppAutoReply] LLM call failed: {e}")

    if not reply:
        # Safe, honest fallback if the model call fails — still discloses.
        base = "Ye JARVIS bol raha hai, [user] ka assistant — abhi thoda unavailable hain."
        return base if disclose else "Note kar liya, thodi der mein reply milega."
    return reply


# ---------------------------------------------------------------------------
# Background watcher
# ---------------------------------------------------------------------------
class _Watcher:
    def __init__(self):
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.reason = ""
        self.allow_groups = False
        self.deadline: Optional[float] = None
        self.replies_sent = 0
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, reason: str, duration_minutes: Optional[int], allow_groups: bool, player):
        if self.running:
            return False
        self.stop_event.clear()
        self.reason = reason
        self.allow_groups = allow_groups
        self.deadline = (time.time() + duration_minutes * 60) if duration_minutes else None
        self.replies_sent = 0
        self.last_error = ""
        self.thread = threading.Thread(target=self._loop, args=(player,), daemon=True)
        self.thread.start()
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self.stop_event.set()
        self.thread.join(timeout=5)
        return True

    def _loop(self, player):
        state = _load_state()
        while not self.stop_event.is_set():
            if self.deadline and time.time() >= self.deadline:
                _log(player, "Auto-stopping — time limit reached.")
                break
            try:
                self._poll_once(state, player)
            except ww.WhatsAppNotOpen as e:
                self.last_error = str(e)
                _log(player, str(e))
            except Exception as e:
                self.last_error = str(e)
                _log(player, f"Watcher error: {e}")
            self.stop_event.wait(POLL_SECONDS)

    def _poll_once(self, state: dict, player):
        unread = ww.list_unread_chats(skip_groups=not self.allow_groups)
        for chat in unread:
            if not ww.open_chat(chat.name):
                continue
            time.sleep(0.4)
            ctx = ww.read_active_conversation()
            if ctx.is_group and not self.allow_groups:
                continue
            if not ctx.transcript:
                continue

            last_line = ctx.transcript[-1][1]
            if _already_handled(state, ctx.contact_name, last_line):
                continue
            if ctx.transcript[-1][0] == "you":
                # Last message in the thread is already ours — nothing new to answer.
                continue

            disclose = _needs_disclosure(state, ctx.contact_name)
            reply = _build_reply(ctx, self.reason, disclose)
            if ww.send_reply(reply):
                if disclose:
                    _mark_disclosed(state, ctx.contact_name)
                _mark_handled(state, ctx.contact_name, last_line, reply)
                _save_state(state)
                self.replies_sent += 1
                _log(player, f"Replied to {ctx.contact_name}: {reply[:80]}")
            else:
                _log(player, f"Could not send reply to {ctx.contact_name}.")


_watcher = _Watcher()


def _log(player, text: str) -> None:
    print(f"[WhatsAppAutoReply] {text}")
    if player:
        try:
            player.write_log(f"[whatsapp_auto_reply] {text[:120]}")
        except Exception:
            pass


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "").strip().lower()

    if not ww._PYWINAUTO:
        return ("Sir, this needs pywinauto, which isn't installed. Run: pip install pywinauto "
                "(Windows only, since it drives WhatsApp Desktop's UI).")

    if action == "start":
        reason = (params.get("reason") or "").strip()
        allow_groups = bool(params.get("allow_groups", False))
        duration = params.get("duration_minutes")
        try:
            duration = int(duration) if duration else None
        except Exception:
            duration = None

        started = _watcher.start(reason, duration, allow_groups, player)
        if not started:
            return "Sir, WhatsApp auto-reply is already running."
        scope = "1:1 chats and groups" if allow_groups else "1:1 chats only"
        limit = f" for the next {duration} minutes" if duration else ""
        return (f"Sir, I'll watch WhatsApp and reply for you{limit} ({scope}). "
                "Every reply makes clear it's me, your assistant, not you. Say 'stop' anytime.")

    if action == "stop":
        stopped = _watcher.stop()
        if not stopped:
            return "Sir, WhatsApp auto-reply wasn't running."
        return f"Sir, I've stopped. I sent {_watcher.replies_sent} reply(ies) while it was on."

    if action == "status":
        if not _watcher.running:
            return "Sir, WhatsApp auto-reply is currently off."
        left = ""
        if _watcher.deadline:
            mins = max(0, int((_watcher.deadline - time.time()) // 60))
            left = f", about {mins} more minute(s)"
        err = f" Last issue: {_watcher.last_error}" if _watcher.last_error else ""
        return f"Sir, it's running{left}. {_watcher.replies_sent} reply(ies) sent so far.{err}"

    return "Please specify action: start, stop, or status."
