
"""
JARVIS Pomodoro Plugin
======================

Background Pomodoro timer with spoken phase-change announcements.

Commands:
    start
    pause
    resume
    stop
    reset
    status

Examples:
    "Start a Pomodoro"
    "Start a 25 minute focus session"
    "Start a Pomodoro with 30 minutes focus and 10 minutes break"
    "Pause my Pomodoro"
    "Resume my Pomodoro"
    "How much time is left?"
    "Stop the Pomodoro"
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_STOP_EVENT = threading.Event()

_TIMER_THREAD: Optional[threading.Thread] = None

_STATE = {
    "phase": "idle",              # idle / work / break / paused
    "previous_phase": None,
    "end_monotonic": None,
    "remaining": 0.0,

    "work_minutes": 25.0,
    "break_minutes": 5.0,

    "completed": 0,

    # Always keep the most recent JARVIS player.
    "player": None,
}


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------

PLUGIN = {
    "name": "pomodoro",
    "description": (
        "Pomodoro focus timer. Start, pause, resume, stop, reset and check "
        "the timer. Supports custom focus and break durations. The timer "
        "runs in the background and JARVIS announces when a focus session "
        "or break finishes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "start | pause | resume | stop | reset | status"
                ),
            },
            "work_minutes": {
                "type": "NUMBER",
                "description": "Focus duration in minutes. Default: 25.",
            },
            "break_minutes": {
                "type": "NUMBER",
                "description": "Break duration in minutes. Default: 5.",
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _number(value, default, minimum=1.0, maximum=240.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, value))


def _format_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))

    minutes, secs = divmod(seconds, 60)

    if minutes:
        return (
            f"{minutes} minute"
            f"{'' if minutes == 1 else 's'} "
            f"{secs:02d} seconds"
        )

    return f"{secs} seconds"


def _remaining_locked() -> float:
    phase = _STATE["phase"]
    end = _STATE["end_monotonic"]

    if phase in ("work", "break") and end is not None:
        return max(0.0, end - time.monotonic())

    return max(0.0, float(_STATE["remaining"] or 0.0))


def _write_log(message: str):
    """
    Write to JARVIS activity log without ever crashing the timer.
    """
    with _LOCK:
        player = _STATE.get("player")

    if player is None:
        return

    try:
        player.write_log(f"SYS: {message}")
        return
    except Exception:
        pass

    try:
        ui = getattr(player, "ui", None)

        if ui is not None:
            ui.write_log(f"SYS: {message}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Reliable JARVIS speech
# ---------------------------------------------------------------------------

def _speak(message: str):
    """
    Ask the currently connected JARVIS Live session to speak.

    This is deliberately independent from the timer thread.

    We first try JARVIS's public speak() method. If that isn't available,
    we directly schedule send_client_content() on JARVIS's Live event loop.

    Nothing here is allowed to kill the Pomodoro worker.
    """

    with _LOCK:
        player = _STATE.get("player")

    if player is None:
        return False

    # ---------------------------------------------------------------
    # Preferred path: JARVIS's own public speak() method.
    # ---------------------------------------------------------------

    try:
        speak_method = getattr(player, "speak", None)

        if callable(speak_method):
            speak_method(message)

            # JarvisLive.speak() normally schedules the coroutine itself
            # and returns immediately.
            return True

    except Exception as exc:
        try:
            _write_log(
                f"Pomodoro speech method failed: {exc}"
            )
        except Exception:
            pass

    # ---------------------------------------------------------------
    # Fallback path: directly schedule Gemini Live speech.
    # ---------------------------------------------------------------

    try:
        loop = getattr(player, "_loop", None)
        session = getattr(player, "session", None)

        if loop is None or session is None:
            _write_log(
                "Pomodoro could not speak because the Gemini Live session "
                "is not currently connected."
            )
            return False

        if loop.is_closed():
            return False

        async def send_speech():
            try:
                await session.send_client_content(
                    turns={
                        "parts": [
                            {
                                "text": (
                                    "Speak this notification to the user "
                                    "naturally and briefly:\n"
                                    f"{message}"
                                )
                            }
                        ]
                    },
                    turn_complete=True,
                )
            except Exception as exc:
                _write_log(
                    f"Pomodoro Live speech failed: {exc}"
                )

        asyncio.run_coroutine_threadsafe(
            send_speech(),
            loop,
        )

        return True

    except Exception as exc:
        _write_log(
            f"Pomodoro speech scheduling failed: {exc}"
        )
        return False


def _announce(message: str):
    """
    Log AND speak a Pomodoro notification.
    """

    _write_log(f"🍅 {message}")

    # Speech happens separately so logging never depends on audio.
    threading.Thread(
        target=_speak_with_retry,
        args=(message,),
        name="pomodoro-speech",
        daemon=True,
    ).start()


def _speak_with_retry(message: str):
    """
    Give the Live session a couple of chances to accept the announcement.

    This is useful when Gemini has just finished a response or is reconnecting.
    """

    for attempt in range(3):

        if _speak(message):
            return

        if attempt < 2:
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# Timer worker
# ---------------------------------------------------------------------------

def _timer_worker():
    global _TIMER_THREAD

    try:
        while not _STOP_EVENT.is_set():

            with _LOCK:
                phase = _STATE["phase"]
                end = _STATE["end_monotonic"]

            # No active timer.
            if phase not in ("work", "break") or end is None:
                _STOP_EVENT.wait(0.25)
                continue

            remaining = end - time.monotonic()

            # Not finished yet.
            if remaining > 0:
                _STOP_EVENT.wait(
                    min(remaining, 0.25)
                )
                continue

            # -------------------------------------------------------
            # Phase completed.
            # -------------------------------------------------------

            with _LOCK:

                # Re-check because pause/stop may have happened.
                if _STATE["phase"] not in ("work", "break"):
                    continue

                completed_phase = _STATE["phase"]

                if completed_phase == "work":

                    _STATE["completed"] += 1

                    _STATE["phase"] = "break"

                    duration = (
                        _STATE["break_minutes"] * 60.0
                    )

                    _STATE["end_monotonic"] = (
                        time.monotonic() + duration
                    )

                    completed_count = _STATE["completed"]
                    break_minutes = _STATE["break_minutes"]

                    message = (
                        "Your focus session is complete. "
                        f"Time for your {break_minutes:g} minute break. "
                        f"You have completed {completed_count} "
                        "focus session"
                        f"{'' if completed_count == 1 else 's'}."
                    )

                else:

                    _STATE["phase"] = "work"

                    duration = (
                        _STATE["work_minutes"] * 60.0
                    )

                    _STATE["end_monotonic"] = (
                        time.monotonic() + duration
                    )

                    work_minutes = _STATE["work_minutes"]

                    message = (
                        "Your break is over. "
                        f"Starting the next {work_minutes:g} minute "
                        "focus session."
                    )

            # Speak outside the lock.
            _announce(message)

    except Exception as exc:

        _write_log(
            f"Pomodoro timer worker stopped unexpectedly: {exc}"
        )

    finally:

        _TIMER_THREAD = None


def _ensure_worker():
    global _TIMER_THREAD

    with _LOCK:

        if (
            _TIMER_THREAD is not None
            and _TIMER_THREAD.is_alive()
        ):
            return

        _STOP_EVENT.clear()

        _TIMER_THREAD = threading.Thread(
            target=_timer_worker,
            name="jarvis-pomodoro",
            daemon=True,
        )

        _TIMER_THREAD.start()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def run(parameters: dict, player=None, session_memory=None) -> str:

    try:

        action = str(
            parameters.get("action", "status") or "status"
        ).strip().lower()

        aliases = {
            "start_pomodoro": "start",
            "start_focus": "start",
            "begin": "start",

            "pause_timer": "pause",

            "resume_timer": "resume",

            "stop_timer": "stop",
            "reset_timer": "reset",

            "check": "status",
            "time_left": "status",
        }

        action = aliases.get(action, action)

        # Always keep the newest JARVIS instance.
        if player is not None:
            with _LOCK:
                _STATE["player"] = player

        # ---------------------------------------------------------------
        # START
        # ---------------------------------------------------------------

        if action == "start":

            work = _number(
                parameters.get("work_minutes"),
                25.0,
            )

            break_minutes = _number(
                parameters.get("break_minutes"),
                5.0,
            )

            with _LOCK:

                _STATE["phase"] = "work"
                _STATE["previous_phase"] = None

                _STATE["work_minutes"] = work
                _STATE["break_minutes"] = break_minutes

                _STATE["remaining"] = work * 60.0

                _STATE["end_monotonic"] = (
                    time.monotonic() + work * 60.0
                )

                # Keep previous completed-session count.
                # Starting a new timer does not erase the count.

            _ensure_worker()

            return (
                f"Pomodoro started. "
                f"{work:g} minutes of focus followed by "
                f"a {break_minutes:g} minute break."
            )

        # ---------------------------------------------------------------
        # PAUSE
        # ---------------------------------------------------------------

        if action == "pause":

            with _LOCK:

                if _STATE["phase"] not in ("work", "break"):
                    return (
                        "There is no active Pomodoro to pause."
                    )

                remaining = _remaining_locked()

                _STATE["remaining"] = remaining
                _STATE["previous_phase"] = _STATE["phase"]

                _STATE["phase"] = "paused"
                _STATE["end_monotonic"] = None

            return (
                "Pomodoro paused with "
                f"{_format_time(remaining)} remaining."
            )

        # ---------------------------------------------------------------
        # RESUME
        # ---------------------------------------------------------------

        if action == "resume":

            with _LOCK:

                if _STATE["phase"] != "paused":
                    return (
                        "There is no paused Pomodoro to resume."
                    )

                remaining = max(
                    1.0,
                    float(_STATE["remaining"]),
                )

                phase = (
                    _STATE["previous_phase"]
                    or "work"
                )

                _STATE["phase"] = phase

                _STATE["end_monotonic"] = (
                    time.monotonic() + remaining
                )

            _ensure_worker()

            return (
                "Pomodoro resumed with "
                f"{_format_time(remaining)} remaining."
            )

        # ---------------------------------------------------------------
        # STOP / RESET
        # ---------------------------------------------------------------

        if action in ("stop", "reset"):

            with _LOCK:

                was_running = (
                    _STATE["phase"] != "idle"
                )

                completed = _STATE["completed"]

                _STATE["phase"] = "idle"
                _STATE["previous_phase"] = None
                _STATE["end_monotonic"] = None
                _STATE["remaining"] = 0.0

            _STOP_EVENT.set()

            if not was_running:
                return (
                    "No Pomodoro is currently running."
                )

            return (
                "Pomodoro stopped. "
                f"Completed focus sessions: {completed}."
            )

        # ---------------------------------------------------------------
        # STATUS
        # ---------------------------------------------------------------

        if action == "status":

            with _LOCK:

                phase = _STATE["phase"]
                remaining = _remaining_locked()

                completed = _STATE["completed"]

                work = _STATE["work_minutes"]
                break_minutes = _STATE["break_minutes"]

            if phase == "idle":

                return (
                    "No Pomodoro is currently running. "
                    f"Completed focus sessions: {completed}."
                )

            if phase == "paused":

                return (
                    "Pomodoro is paused with "
                    f"{_format_time(remaining)} remaining. "
                    f"Completed focus sessions: {completed}."
                )

            phase_name = (
                "focus"
                if phase == "work"
                else "break"
            )

            return (
                f"Pomodoro is currently in the {phase_name} phase. "
                f"{_format_time(remaining)} remaining. "
                f"Schedule: {work:g} minute focus sessions and "
                f"{break_minutes:g} minute breaks. "
                f"Completed focus sessions: {completed}."
            )

        return (
            "Unknown Pomodoro action. "
            "Use start, pause, resume, stop, reset, or status."
        )

    except Exception as exc:

        _write_log(
            f"Pomodoro plugin error: {exc}"
        )

        return (
            f"Sir, the Pomodoro plugin encountered an error: {exc}"
        )
