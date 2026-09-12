"""
JARVIS Screen Guide Plugin

Commands:
    "JARVIS, see my screen"
    "JARVIS, watch my screen and guide me"
    "JARVIS, stop seeing my screen"
    "JARVIS, stop screen guide"
    "JARVIS, screen guide status"

The plugin continuously captures the screen and sends screenshots to Gemini
for visual analysis. JARVIS then speaks concise guidance.

Mouse movement is supported through the "point" action.
The plugin does NOT click or type automatically.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import time
from io import BytesIO
from typing import Any

import requests


# ============================================================================
# PLUGIN METADATA
# ============================================================================

PLUGIN = {
    "name": "screen_guide",
    "description": (
        "Continuously watches the user's screen, understands what they are "
        "doing, gives step-by-step guidance, and can point the mouse at "
        "specified screen coordinates without clicking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action to perform: start, stop, status, or point."
                ),
                "enum": [
                    "start",
                    "stop",
                    "status",
                    "point",
                ],
            },
            "x": {
                "type": "INTEGER",
                "description": "Screen X coordinate for mouse pointing.",
            },
            "y": {
                "type": "INTEGER",
                "description": "Screen Y coordinate for mouse pointing.",
            },
        },
        "required": ["action"],
    },
    "run": None,
}


# ============================================================================
# CONFIGURATION
# ============================================================================

SCREEN_INTERVAL = float(
    os.getenv("SCREEN_GUIDE_INTERVAL", "3")
)

MAX_IMAGE_BYTES = 4 * 1024 * 1024

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or ""
).strip()

VISION_MODEL = (
    os.getenv("GEMINI_VISION_MODEL")
    or os.getenv("GEMINI_MODEL")
    or "gemini-3.5-flash"
).strip()

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{VISION_MODEL}:generateContent"
)


# ============================================================================
# RUNTIME STATE
# ============================================================================

_lock = threading.RLock()

_running = False
_worker: threading.Thread | None = None
_stop_event = threading.Event()

_last_analysis = ""
_last_analysis_time = 0.0
_last_error = ""

_player = None


# ============================================================================
# LOGGING
# ============================================================================

def _log(player, message: str) -> None:
    """Write a message to the existing JARVIS UI log."""

    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"SYS: {message}")
            return
    except Exception:
        pass

    try:
        print(f"[ScreenGuide] {message}")
    except Exception:
        pass


# ============================================================================
# FIND THE REAL JARVIS CONTROLLER
# ============================================================================

def _get_jarvis(player):
    """
    The plugin loader passes JarvisUI as `player`.

    In main.py, JARVIS assigns:

        self.ui.on_text_command = self._on_text_command

    Therefore the bound callback's __self__ points back to JarvisLive.
    """

    if player is None:
        return None

    try:
        callback = getattr(
            player,
            "on_text_command",
            None,
        )

        jarvis = getattr(
            callback,
            "__self__",
            None,
        )

        if jarvis is not None and hasattr(jarvis, "speak"):
            return jarvis

    except Exception:
        pass

    return None


# ============================================================================
# JARVIS SPEECH
# ============================================================================

def _speak(player, text: str) -> bool:
    """
    Send speech through the existing JarvisLive.speak() method.

    JarvisLive.speak() already uses run_coroutine_threadsafe(), so this can
    safely be called from our background worker.
    """

    jarvis = _get_jarvis(player)

    if jarvis is None:
        return False

    try:
        loop = getattr(
            jarvis,
            "_loop",
            None,
        )

        session = getattr(
            jarvis,
            "session",
            None,
        )

        if loop is None or session is None:
            return False

        jarvis.speak(text)

        return True

    except Exception as exc:
        _log(
            player,
            f"Screen Guide speech error: {exc}",
        )
        return False


def _speak_when_available(
    player,
    text: str,
    retries: int = 10,
) -> bool:
    """
    Wait for the Gemini Live connection if JARVIS is reconnecting.
    """

    for _ in range(retries):

        if _stop_event.is_set():
            return False

        if _speak(player, text):
            return True

        time.sleep(1)

    _log(
        player,
        "Screen Guide could not speak because JARVIS voice is unavailable.",
    )

    return False


# ============================================================================
# SCREEN CAPTURE
# ============================================================================

def _capture_screen() -> bytes:
    """
    Capture all available Windows screens.

    Pillow is intentionally imported here rather than at module import time,
    so the plugin loader can still load the plugin if Pillow is missing.
    """

    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for Screen Guide. "
            "Install it with: python -m pip install Pillow"
        ) from exc

    image = ImageGrab.grab(
        all_screens=True
    )

    max_dimension = 1920

    if max(
        image.width,
        image.height,
    ) > max_dimension:

        scale = (
            max_dimension
            / max(
                image.width,
                image.height,
            )
        )

        new_size = (
            int(image.width * scale),
            int(image.height * scale),
        )

        image = image.resize(
            new_size
        )

    buffer = BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=70,
        optimize=True,
    )

    data = buffer.getvalue()

    if len(data) > MAX_IMAGE_BYTES:

        buffer = BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=50,
            optimize=True,
        )

        data = buffer.getvalue()

    return data


# ============================================================================
# GEMINI VISION
# ============================================================================

def _analyze_screen(
    image_bytes: bytes,
) -> dict[str, Any]:

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not configured."
        )

    encoded = base64.b64encode(
        image_bytes
    ).decode("ascii")

    prompt = """
You are JARVIS, a real-time computer-use guide.

Analyze the user's current computer screen carefully.

Determine:

1. Which application or website is visible.
2. What the user appears to be doing.
3. Whether something is loading, processing, compiling, downloading,
   or waiting.
4. What the most useful next step is.
5. Whether there is a clearly visible UI element that could be pointed at.

Important rules:

- Only describe things actually visible on the screen.
- Never invent buttons, menus, windows, or messages.
- Keep spoken guidance concise.
- Normally provide one or two sentences.
- If the user appears to be doing something correctly, say so and give
  the next useful step.
- If the screen is idle, explain what is visible and ask what they want
  to accomplish.
- Do not tell the user to click something that is not clearly visible.

Return ONLY valid JSON in this exact structure:

{
    "summary": "Short description of what the user is doing",
    "guidance": "Short spoken guidance for the next step",
    "point": {
        "available": false,
        "x": 0,
        "y": 0,
        "description": ""
    }
}

The point coordinates must correspond to the screenshot coordinates.
Only set point.available to true if a useful target is clearly visible.
"""

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt,
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": encoded,
                        },
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    response = requests.post(
        GEMINI_URL,
        params={
            "key": GEMINI_API_KEY,
        },
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    candidates = data.get(
        "candidates",
        [],
    )

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates."
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    if not parts:
        raise RuntimeError(
            "Gemini returned no response parts."
        )

    text = parts[0].get(
        "text",
        "",
    ).strip()

    if not text:
        raise RuntimeError(
            "Gemini returned an empty screen analysis."
        )

    # Remove accidental Markdown JSON fences.
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    result = json.loads(text)

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "Gemini returned invalid screen-analysis JSON."
        )

    return result


# ============================================================================
# MOUSE CONTROL
# ============================================================================

def _get_screen_size() -> tuple[int, int]:
    """Return the primary Windows screen dimensions."""

    try:
        user32 = ctypes.windll.user32

        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)

        return width, height

    except Exception:
        return 1920, 1080


def _move_cursor(
    x: int,
    y: int,
) -> bool:
    """
    Move the Windows cursor.

    This function ONLY moves the cursor.
    It does not click, type, or press keys.
    """

    try:

        width, height = _get_screen_size()

        x = max(
            0,
            min(
                int(x),
                width - 1,
            ),
        )

        y = max(
            0,
            min(
                int(y),
                height - 1,
            ),
        )

        ctypes.windll.user32.SetCursorPos(
            x,
            y,
        )

        return True

    except Exception:
        return False


# ============================================================================
# CONTINUOUS MONITOR
# ============================================================================

def _monitor_loop(
    player,
) -> None:

    global _last_analysis
    global _last_analysis_time
    global _last_error

    _log(
        player,
        "👁️ Screen Guide is now watching your screen.",
    )

    first_frame = True

    while not _stop_event.is_set():

        try:

            image = _capture_screen()

            analysis = _analyze_screen(
                image
            )

            summary = str(
                analysis.get(
                    "summary",
                    "",
                )
            ).strip()

            guidance = str(
                analysis.get(
                    "guidance",
                    "",
                )
            ).strip()

            _last_analysis = (
                guidance
                or summary
            )

            _last_analysis_time = time.time()

            _last_error = ""

            if guidance:

                if first_frame:

                    message = (
                        "I'm looking at your screen now. "
                        + guidance
                    )

                else:

                    message = guidance

                _speak_when_available(
                    player,
                    message,
                )

            first_frame = False

        except requests.HTTPError as exc:

            _last_error = (
                f"Gemini HTTP error: {exc}"
            )

            _log(
                player,
                f"Screen Guide API error: {exc}",
            )

            _stop_event.wait(8)

        except Exception as exc:

            _last_error = str(exc)

            _log(
                player,
                f"Screen Guide error: {exc}",
            )

            _stop_event.wait(5)

        _stop_event.wait(
            SCREEN_INTERVAL
        )

    _log(
        player,
        "👁️ Screen Guide stopped.",
    )


# ============================================================================
# START
# ============================================================================

def _start(
    player,
) -> str:

    global _running
    global _worker
    global _player

    with _lock:

        if _running:
            return (
                "Screen Guide is already watching "
                "your screen, sir."
            )

        if not GEMINI_API_KEY:
            return (
                "I can't start Screen Guide because "
                "no Gemini API key is configured."
            )

        _player = player

        _stop_event.clear()

        _running = True

        _worker = threading.Thread(
            target=_monitor_loop,
            args=(player,),
            name="JARVIS-ScreenGuide",
            daemon=True,
        )

        _worker.start()

    return (
        "Screen Guide started, sir. "
        "I'm watching your screen and will guide "
        "you as you work. Say stop screen guide "
        "when you want me to stop."
    )


# ============================================================================
# STOP
# ============================================================================

def _stop(
    player,
) -> str:

    global _running
    global _worker

    with _lock:

        if not _running:
            return (
                "Screen Guide is not currently running, sir."
            )

        _stop_event.set()

        _running = False

        worker = _worker

        _worker = None

    if (
        worker is not None
        and worker.is_alive()
    ):
        worker.join(
            timeout=2
        )

    return (
        "Screen Guide stopped, sir. "
        "I'm no longer watching your screen."
    )


# ============================================================================
# STATUS
# ============================================================================

def _status(
    player,
) -> str:

    with _lock:
        running = _running

    if not running:
        return (
            "Screen Guide is currently stopped, sir."
        )

    if _last_analysis:

        age = int(
            max(
                0,
                time.time()
                - _last_analysis_time,
            )
        )

        return (
            "Screen Guide is active, sir. "
            f"My latest guidance was "
            f"{age} seconds ago: "
            f"{_last_analysis}"
        )

    if _last_error:

        return (
            "Screen Guide is active, but the "
            "latest screen analysis failed: "
            f"{_last_error}"
        )

    return (
        "Screen Guide is active and waiting "
        "for its first screen analysis."
    )


# ============================================================================
# POINT CURSOR
# ============================================================================

def _point(
    player,
    parameters: dict[str, Any],
) -> str:

    x = parameters.get("x")
    y = parameters.get("y")

    if x is None or y is None:

        return (
            "I need the screen X and Y coordinates "
            "for the point command, sir."
        )

    try:

        x = int(x)
        y = int(y)

    except (TypeError, ValueError):

        return (
            "The cursor coordinates must be numbers, sir."
        )

    if _move_cursor(
        x,
        y,
    ):

        return (
            f"Pointing the cursor at "
            f"screen position {x}, {y}, sir."
        )

    return (
        "I couldn't move the cursor, sir."
    )


# ============================================================================
# PLUGIN ENTRY POINT
# ============================================================================

def run(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:

    del session_memory

    action = str(
        parameters.get(
            "action",
            "status",
        )
    ).strip().lower()

    if action == "start":
        return _start(player)

    if action == "stop":
        return _stop(player)

    if action == "status":
        return _status(player)

    if action == "point":
        return _point(
            player,
            parameters,
        )

    return (
        "Unknown Screen Guide action. "
        "Use start, stop, status, or point."
    )


# IMPORTANT:
# Plugin loader expects PLUGIN["run"] to be callable.
PLUGIN["run"] = run