"""
dashboard/phone_camera.py — Phone-as-camera manager.

A phone opens a page in its browser (no app install needed), grants camera
access via getUserMedia, and streams JPEG frames to the JARVIS dashboard
over a WebSocket. Frames are held in memory here and are retrievable by
actions/screen_processor.py and by the phone_camera plugin.

Supports live subscribers: any component (e.g. the JARVIS HUD) can register
a callback and receive every incoming frame in real time.

Nothing is written to disk unless the plugin's capture action is used.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


# A frame older than this means we consider the phone offline.
FRAME_STALE_SECONDS = 3.0


class PhoneCameraManager:
    """
    Holds the most recent phone frame. Single instance shared process-wide.
    Thread-safe (dashboard WS handler, executor threads, and the plugin can
    all touch it).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest: Optional[tuple[bytes, str, float]] = None   # (jpeg, mime, ts)
        self._connected = False
        self._active = False            # user has "started" phone-camera mode
        self._resolution = (0, 0)
        self._fps_window: list[float] = []
        self._fps = 0.0
        # keyed subscribers: key -> callback(jpeg_bytes)
        self._subscribers: dict[str, Callable[[bytes], None]] = {}

    # ── pub/sub (live feed) ────────────────────────────────────────────

    def subscribe(self, key: str, callback: Callable[[bytes], None]) -> None:
        """Register a callback to receive every incoming frame.
        Re-using a key replaces the previous callback."""
        with self._lock:
            self._subscribers[key] = callback

    def unsubscribe(self, key: str) -> None:
        with self._lock:
            self._subscribers.pop(key, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ── called by the WebSocket handler ────────────────────────────────

    def on_connect(self) -> None:
        with self._lock:
            self._connected = True

    def on_disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._latest = None
            self._fps_window.clear()
            self._fps = 0.0

    def on_hello(self, width: int, height: int) -> None:
        with self._lock:
            self._resolution = (int(width), int(height))

    def on_frame(self, jpeg_bytes: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self._latest = (jpeg_bytes, "image/jpeg", now)
            self._fps_window.append(now)
            if len(self._fps_window) > 20:
                self._fps_window = self._fps_window[-20:]
            if len(self._fps_window) >= 2:
                span = self._fps_window[-1] - self._fps_window[0]
                if span > 0:
                    self._fps = (len(self._fps_window) - 1) / span
            subs = list(self._subscribers.values())

        # Fan out OUTSIDE the lock so a slow subscriber can never stall the WS.
        for cb in subs:
            try:
                cb(jpeg_bytes)
            except Exception:
                pass

    # ── consumer API ───────────────────────────────────────────────────

    def is_live(self) -> bool:
        with self._lock:
            if not self._connected or self._latest is None:
                return False
            return (time.monotonic() - self._latest[2]) < FRAME_STALE_SECONDS

    def get_latest_frame(self) -> Optional[tuple[bytes, str]]:
        """Return (jpeg_bytes, mime_type) or None if nothing fresh."""
        with self._lock:
            if self._latest is None:
                return None
            if (time.monotonic() - self._latest[2]) >= FRAME_STALE_SECONDS:
                return None
            return self._latest[0], self._latest[1]

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = bool(active)

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def status(self) -> dict:
        with self._lock:
            live = (
                self._connected
                and self._latest is not None
                and (time.monotonic() - self._latest[2]) < FRAME_STALE_SECONDS
            )
            return {
                "connected":  self._connected,
                "live":       live,
                "active":     self._active,
                "fps":        round(self._fps, 1),
                "resolution": self._resolution,
                "subscribers": len(self._subscribers),
            }


# Module-level singleton
phone_camera = PhoneCameraManager()