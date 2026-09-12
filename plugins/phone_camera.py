"""
JARVIS phone_camera plugin.

Uses the user's paired phone as a wireless room camera. The phone opens a
plain web page in its own browser — nothing installs on the phone. Frames
stream over the existing JARVIS dashboard WebSocket and are held in RAM by
dashboard/phone_camera.py.

The phone screen stays black during streaming (no preview, no UI).

When 'start' is called, the JARVIS HUD switches to the live-feed widget and
mirrors the phone's stream — the same widget the PC webcam and the browser
agent use. Wiring is done through whichever UI API is available
(show_phone_camera / start_browser_stream are both supported), so this
plugin keeps working regardless of which UI methods exist on the host.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path


PLUGIN = {
    "name": "phone_camera",
    "description": (
        "Uses the user's paired phone as a wireless room camera with a LIVE "
        "feed shown inside the JARVIS HUD. "
        "USE THIS when the user says things like: 'start my phone camera', "
        "'use my phone as a camera', 'use my side phone as my camera', "
        "'show me my phone camera feed', 'live view from my phone', "
        "'look through my phone', 'what's on my phone camera', "
        "'make my phone the camera', 'watch my room with my phone'. "
        "Actions: start | stop | status | capture | link. "
        "'start' returns a link the user opens on their phone — nothing is "
        "installed on the phone, it just uses the browser — and immediately "
        "mirrors the live feed in the JARVIS HUD. "
        "The phone screen stays black while streaming. "
        "'capture' saves the current frame to a temp file and returns its "
        "path — after that, call file_processor with that file_path and "
        "action='describe' to actually see what the camera sees. "
        "IMPORTANT: the LIVE view is only shown by 'start'. 'status' only "
        "reports numbers — it does NOT open the HUD view. Never claim the "
        "feed is 'displayed in the HUD' unless 'start' was the last action "
        "that ran and it returned success."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | stop | status | capture | link",
                "enum": ["start", "stop", "status", "capture", "link"],
            },
            "expiry_secs": {
                "type": "INTEGER",
                "description": (
                    "How long the one-time link is valid, in seconds "
                    "(default 600). Only used by 'start' / 'link'."
                ),
            },
        },
        "required": ["action"],
    },
}


# ── Reach JarvisLive through the same pattern used by pomodoro.py ─────────
def _get_jarvis_live(player):
    if player is None:
        return None
    try:
        cb = getattr(player, "on_text_command", None)
        if cb is None:
            return None
        jl = getattr(cb, "__self__", None)
        return jl if jl is not None else None
    except Exception:
        return None


def _get_dashboard(player):
    jl = _get_jarvis_live(player)
    if jl is None:
        return None
    return getattr(jl, "_dashboard", None)


# ── Temp file management for capture ───────────────────────────────────────
_TMP_DIR = Path(tempfile.gettempdir()) / "jarvis_phone_camera"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
_last_capture: Path | None = None
_CAPTURE_TTL_SECONDS = 600


def _prune_old_captures() -> None:
    cutoff = time.time() - _CAPTURE_TTL_SECONDS
    try:
        for p in _TMP_DIR.glob("frame_*.jpg"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


# ── Spoken strings ────────────────────────────────────────────────────────
def _status_str(st: dict) -> str:
    if not st.get("connected"):
        if st.get("active"):
            return ("Phone camera mode is active, sir, but no phone is "
                    "connected yet. Open the link I gave you on your phone.")
        return "Phone camera is not running, sir."
    if st.get("live"):
        w, h = st.get("resolution") or (0, 0)
        res = f"{w}×{h}" if w and h else "unknown resolution"
        return (f"Phone camera is live, sir — {st.get('fps', 0)} FPS, "
                f"{res}.")
    return ("A phone is connected but the camera stream has gone quiet, "
            "sir. It may have been backgrounded.")


# ── UI live-feed wiring (tolerant of which UI API is available) ───────────

_UI_SUBSCRIBER_KEY = "ui"


def _find_callable(player, *names):
    """Return the first existing callable attribute from names, or None."""
    for n in names:
        fn = getattr(player, n, None)
        if callable(fn):
            return fn
    return None


def _wire_ui_live(player) -> bool:
    """Subscribe the JARVIS HUD to the phone stream and switch it on. Tries
    the phone-camera API first, then falls back to the browser-agent live-feed
    API (they share the same HUD widget)."""
    if player is None:
        return False
    try:
        from dashboard.phone_camera import phone_camera
    except Exception:
        return False

    def _push(jpeg_bytes: bytes):
        fn = _find_callable(
            player,
            "push_phone_camera_frame",   # preferred
            "push_browser_frame",        # fallback (same underlying signal)
        )
        if fn is None:
            return
        try:
            fn(jpeg_bytes)
        except Exception:
            pass

    try:
        phone_camera.subscribe(_UI_SUBSCRIBER_KEY, _push)
    except Exception:
        return False

    show_fn = _find_callable(
        player,
        "show_phone_camera",       # preferred
        "start_browser_stream",    # fallback
    )
    if show_fn is None:
        return False
    try:
        show_fn("PHONE CAMERA")
        return True
    except Exception:
        return False


def _unwire_ui_live(player) -> None:
    try:
        from dashboard.phone_camera import phone_camera
        phone_camera.unsubscribe(_UI_SUBSCRIBER_KEY)
    except Exception:
        pass
    if player is None:
        return
    hide_fn = _find_callable(
        player,
        "hide_phone_camera",    # preferred
        "stop_browser_stream",  # fallback
    )
    if hide_fn is not None:
        try:
            hide_fn()
        except Exception:
            pass


# ── Actions ───────────────────────────────────────────────────────────────
def _action_start(player, expiry_secs: int) -> str:
    dash = _get_dashboard(player)
    if dash is None:
        return ("Sir, the dashboard server is not running, so I can't "
                "generate a phone-camera link.")

    try:
        from dashboard.phone_camera import phone_camera
    except Exception as e:
        return f"Sir, phone_camera is not available: {e}"

    key = dash.new_key(expiry_secs=max(60, min(int(expiry_secs or 600), 3600)))
    url = dash.get_url()

    # Direct one-time link that lands on the phone-camera page and consumes
    # the key, minting a fresh session for this phone.
    link = f"{url}/phone-camera?key={key}"

    phone_camera.set_active(True)

    # Bring the HUD live-view up and mirror frames live.
    wired = _wire_ui_live(player)

    try:
        if player is not None:
            fn = getattr(player, "show_content", None)
            if callable(fn):
                fn("PHONE CAMERA — open this on your phone",
                   f"{link}\n\n"
                   f"• Open this URL in your phone's browser.\n"
                   f"• Tap once to grant camera access.\n"
                   f"• The phone screen goes black — that's normal.\n"
                   f"• The LIVE feed appears in the JARVIS HUD.\n"
                   f"• Link expires in {expiry_secs or 600} seconds.\n\n"
                   f"(If the page shows the login screen instead, make sure "
                   f"the JARVIS dashboard SSL certs are set up so HTTPS is "
                   f"used — mobile browsers block camera access on plain "
                   f"HTTP.)")
            log = getattr(player, "write_log", None)
            if callable(log):
                log(f"SYS: phone_camera link: {link}")
                log(f"SYS: phone_camera UI wiring: "
                    f"{'OK' if wired else 'FAILED — no HUD API available'}")
    except Exception:
        pass

    if wired:
        return ("Sir, the phone-camera link is on the screen, and the live "
                "view is running in the HUD. Open the link on your phone "
                "and tap once — the feed will appear here the moment the "
                "camera connects.")
    return ("Sir, the phone-camera link is on the screen. Open it on your "
            "phone and tap once to grant camera access.")


def _action_stop(player) -> str:
    _unwire_ui_live(player)
    try:
        from dashboard.phone_camera import phone_camera
        phone_camera.set_active(False)
    except Exception:
        pass
    return ("Phone camera stopped, sir. The live view is closed. "
            "Close the browser tab on your phone to fully disconnect.")


def _action_status(player) -> str:
    try:
        from dashboard.phone_camera import phone_camera
        return _status_str(phone_camera.status())
    except Exception as e:
        return f"Sir, phone_camera status is unavailable: {e}"


def _action_link(player, expiry_secs: int) -> str:
    dash = _get_dashboard(player)
    if dash is None:
        return "Sir, the dashboard server is not running."
    key = dash.new_key(expiry_secs=max(60, min(int(expiry_secs or 600), 3600)))
    url = dash.get_url()
    link = f"{url}/phone-camera?key={key}"
    try:
        if player is not None:
            fn = getattr(player, "show_content", None)
            if callable(fn):
                fn("PHONE CAMERA LINK", link)
            log = getattr(player, "write_log", None)
            if callable(log):
                log(f"SYS: phone_camera link: {link}")
    except Exception:
        pass
    return ("I've shown the phone-camera link on the screen, sir. "
            "Open it on your phone's browser.")


def _action_capture(player) -> str:
    try:
        from dashboard.phone_camera import phone_camera
    except Exception as e:
        return f"Sir, phone_camera is not available: {e}"

    frame = phone_camera.get_latest_frame()
    if frame is None:
        st = phone_camera.status()
        if not st.get("connected"):
            return ("Sir, no phone is connected to the camera yet. Say "
                    "'start my phone camera' first and open the link I show "
                    "you on your phone.")
        return ("Sir, the phone is connected but the frame is stale — it "
                "may have been backgrounded. Please bring the phone-camera "
                "page back to the foreground.")

    jpeg_bytes, _mime = frame

    _prune_old_captures()
    out = _TMP_DIR / f"frame_{int(time.time() * 1000)}.jpg"
    try:
        out.write_bytes(jpeg_bytes)
    except Exception as e:
        return f"Sir, I couldn't save the phone-camera frame: {e}"

    global _last_capture
    _last_capture = out

    return (
        f"Phone camera frame saved to: {out}. "
        f"Call file_processor with file_path='{out}' and action='describe' "
        f"to see what the camera sees."
    )


# ── Entry point ───────────────────────────────────────────────────────────
def run(parameters: dict, player=None, session_memory=None) -> str:
    del session_memory

    action = str(parameters.get("action", "status") or "status").strip().lower()
    aliases = {
        "begin":   "start",
        "open":    "start",
        "on":      "start",
        "close":   "stop",
        "off":     "stop",
        "end":     "stop",
        "grab":    "capture",
        "snap":    "capture",
        "photo":   "capture",
        "url":     "link",
        "qr":      "link",
        "info":    "status",
        "state":   "status",
    }
    action = aliases.get(action, action)

    try:
        expiry = int(parameters.get("expiry_secs", 600))
    except Exception:
        expiry = 600

    try:
        if action == "start":
            return _action_start(player, expiry)
        if action == "stop":
            return _action_stop(player)
        if action == "status":
            return _action_status(player)
        if action == "capture":
            return _action_capture(player)
        if action == "link":
            return _action_link(player, expiry)
        return (
            "Unknown phone_camera action. Use start, stop, status, capture, "
            "or link."
        )
    except Exception as e:
        return f"Sir, the phone_camera plugin encountered an error: {e}"