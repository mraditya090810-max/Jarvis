"""
actions/whatsapp_watcher.py — reads and replies inside WhatsApp Desktop via
UI Automation (pywinauto, uia backend), the same approach already used
elsewhere in this project (see actions/game_updater.py).

This is intentionally separate from actions/send_message.py:
send_message.py only ever *sends* a one-off message you dictated. This
module also *reads* the chat list and a conversation's recent messages, so
plugins/whatsapp_auto_reply.py can decide what, if anything, to say back.

IMPORTANT — calibration notice:
WhatsApp Desktop is an Electron app; its accessibility tree isn't as
consistent across versions/OS themes as a native app's. The heuristics
below (which element is "the chat list", which bubble is incoming vs
outgoing, where the header name sits) are best-effort and were written to
match the current WhatsApp Desktop layout, but you should sanity-check them
once against your own installed version — see the CALIBRATION section at
the bottom of this file for how to dump the live element tree and adjust
the selectors quickly if something doesn't match.

Windows-only for now (pywinauto's uia backend), matching this project's
other desktop-automation modules.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from pywinauto import Application
    import win32gui
    _PYWINAUTO = True
except Exception:
    _PYWINAUTO = False

from actions.send_message import _paste_text  # reuse the same clipboard-paste helper

_UNREAD_RE = re.compile(r"^\s*(\d+)\s+unread messages?\s+(.*)$", re.IGNORECASE)
_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:am|pm)?", re.IGNORECASE)
_GROUP_HINT_RE = re.compile(r"\bgroup\b", re.IGNORECASE)


@dataclass
class UnreadChat:
    name: str
    unread_count: int
    is_group: bool = False


@dataclass
class ChatContext:
    contact_name: str
    is_group: bool
    # Each entry: ("them" | "you", text)
    transcript: list[tuple[str, str]] = field(default_factory=list)


class WhatsAppNotOpen(RuntimeError):
    pass


def _all_visible_windows() -> list[tuple[int, str]]:
    """Every visible top-level window's (hwnd, title), via win32gui directly —
    avoids pywinauto's find_windows(title_re=...), which anchors the regex at
    the START of the title (re.match, not re.search). WhatsApp Desktop's
    title becomes '(N) WhatsApp' whenever there are unread chats, which a
    startswith-anchored 'whatsapp' pattern would silently fail to match —
    exactly when we most need to find it."""
    out = []

    def _cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    out.append((hwnd, title))
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return out


def _connect_window():
    """Finds and returns the live WhatsApp Desktop top-level window (pywinauto
    WindowSpecification). Raises WhatsAppNotOpen if it isn't running/visible."""
    if not _PYWINAUTO:
        raise RuntimeError(
            "pywinauto/pywin32 aren't installed, so I can't read WhatsApp Desktop. "
            "Run: pip install pywinauto pywin32"
        )
    candidates = [(hwnd, title) for hwnd, title in _all_visible_windows()
                  if "whatsapp" in title.lower()]
    for hwnd, _title in candidates:
        try:
            app = Application(backend="uia").connect(handle=hwnd)
            win = app.window(handle=hwnd)
            rect = win.rectangle()
            if win.is_visible() and rect.width() > 300 and rect.height() > 200:
                return win
        except Exception:
            continue
    raise WhatsAppNotOpen(
        "WhatsApp Desktop doesn't seem to be open. Open and log into it first."
    )


def _chat_list_items(win) -> list:
    """Best-effort: the chat list is a scrollable list of rows on the left.
    Confirmed via calibration dump: WhatsApp Desktop exposes these rows as
    control_type 'DataItem' (not 'ListItem' as first guessed), so we scan
    all descendants and filter by control_type in Python instead of asking
    UIA to filter for us — cheaper to get right and easier to widen later
    if another row type shows up."""
    items = []
    try:
        for c in win.descendants():
            try:
                ctype = c.element_info.control_type
                if ctype in ("DataItem", "ListItem") and c.window_text().strip():
                    items.append(c)
            except Exception:
                continue
    except Exception:
        pass
    return items


def _parse_unread_row(text: str) -> Optional[tuple[int, str]]:
    """Parses a chat-row accessible name confirmed to look like:
    '1 unread message Monu Babu 1:09 pm hello' -> (1, 'Monu Babu').
    Returns None if the row isn't an unread row at all."""
    m = _UNREAD_RE.match(text.strip())
    if not m:
        return None
    count = int(m.group(1))
    rest = m.group(2)  # "Monu Babu 1:09 pm hello"
    tm = _TIME_RE.search(rest)
    name = rest[:tm.start()].strip() if tm else rest.strip()
    return count, (name or rest.strip())


def list_unread_chats(skip_groups: bool = True) -> list[UnreadChat]:
    """Scans the chat list for rows whose accessible name indicates unread
    messages. Returns the parsed list, optionally filtering out anything
    that looks like a group chat."""
    win = _connect_window()
    out: list[UnreadChat] = []
    for item in _chat_list_items(win):
        parsed = _parse_unread_row(item.window_text())
        if parsed is None:
            continue
        count, clean_name = parsed
        is_group = bool(_GROUP_HINT_RE.search(clean_name))
        if skip_groups and is_group:
            continue
        out.append(UnreadChat(name=clean_name, unread_count=count, is_group=is_group))
    return out


def open_chat(chat_name: str) -> bool:
    """Clicks the given chat row so it becomes the active conversation.
    Matches by substring rather than a startswith/prefix check, since the
    row's accessible text leads with the unread-count phrase, not the name."""
    win = _connect_window()
    for item in _chat_list_items(win):
        if chat_name.lower() in item.window_text().lower():
            try:
                item.click_input()
                time.sleep(0.6)
                return True
            except Exception:
                return False
    return False


def read_active_conversation(max_messages: int = 12) -> ChatContext:
    """Reads the currently-open conversation: header name + the last
    max_messages bubbles, tagged as 'them' or 'you' by which side of the
    panel they're rendered on (WhatsApp right-aligns your own messages)."""
    win = _connect_window()
    rect = win.rectangle()
    midpoint = rect.left + int(rect.width() * 0.55)  # chat panel starts ~35% in

    # Header: the contact/group name sits near the top of the right-hand
    # panel. Take the first sizeable Text element in that top strip that
    # isn't a status word.
    contact_name = "them"
    is_group = False
    header_candidates = []
    try:
        for c in win.descendants(control_type="Text"):
            try:
                r = c.rectangle()
                if r.top < rect.top + 90 and r.left > midpoint - 200:
                    txt = c.window_text().strip()
                    if txt and txt.lower() not in ("online", "typing…", "typing..."):
                        header_candidates.append(txt)
            except Exception:
                continue
    except Exception:
        pass
    if header_candidates:
        contact_name = header_candidates[0]
        is_group = bool(_GROUP_HINT_RE.search(contact_name))

    # Messages: Text elements inside the conversation area, ordered top to
    # bottom, tagged by horizontal position relative to the panel midpoint.
    bubbles = []
    try:
        for c in win.descendants(control_type="Text"):
            try:
                r = c.rectangle()
                if r.top < rect.top + 100:
                    continue  # header area, already handled above
                txt = c.window_text().strip()
                if not txt:
                    continue
                side = "you" if r.left > midpoint else "them"
                bubbles.append((r.top, side, txt))
            except Exception:
                continue
    except Exception:
        pass

    bubbles.sort(key=lambda b: b[0])
    transcript = [(side, txt) for _, side, txt in bubbles[-max_messages:]]

    return ChatContext(contact_name=contact_name, is_group=is_group, transcript=transcript)


def send_reply(text: str) -> bool:
    """Types the reply into the currently-open conversation's message box and
    sends it. Assumes a chat is already open (call open_chat() first)."""
    win = _connect_window()
    try:
        win.set_focus()
    except Exception:
        pass
    time.sleep(0.2)
    try:
        _paste_text(text)
        time.sleep(0.15)
        win.type_keys("{ENTER}")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CALIBRATION
# ---------------------------------------------------------------------------
# If unread detection or message reading doesn't line up with your installed
# WhatsApp Desktop version, run this file directly (`python -m
# actions.whatsapp_watcher`) with WhatsApp open and focused. It dumps every
# descendant's control_type + accessible name to the console so you can see
# exactly what text/attributes are actually available and adjust the
# regexes/heuristics above (e.g. _UNREAD_RE, the header-strip cutoff, the
# left/right midpoint ratio) to match what you see.
if __name__ == "__main__":
    w = _connect_window()
    for d in w.descendants():
        try:
            name = d.window_text().strip()
            if name:
                print(f"{d.element_info.control_type:<14} {name[:100]}")
        except Exception:
            pass
