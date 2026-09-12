"""
dashboard/camera_service.py — Shared webcam capture manager for phone Live Camera streaming.

Kept independent from the existing HUD "live camera" feature in ui.py (MainWindow._cam_loop),
which opens its own cv2.VideoCapture when triggered by voice/UI. Most webcams only expose one
open handle at a time — if that HUD feature and a phone's Live Camera tab are both started at
once, whichever asked second may fail to acquire the device. This module does not attempt to
arbitrate that (doing so would mean rewriting the existing HUD capture code, which the brief
asked not to touch); it only owns the camera while a phone is actively viewing it.

Design:
- The camera opens lazily — only when the first phone viewer subscribes — and is released the
  moment the last viewer disconnects. No idle camera handle, no idle thread.
- One background thread performs the blocking cv2 reads; frames are resized, JPEG-encoded, and
  fanned out to every subscriber's asyncio.Queue via loop.call_soon_threadsafe (the standard
  thread-safe bridge into an asyncio event loop from a plain thread).
- If reads start failing (device unplugged, temporary glitch), the thread reopens the capture
  automatically instead of dying — this is the "reconnect on interruption" behavior.
- force_stop_all() is used by "Stop Remote Access" to immediately kill every viewer and release
  the device, regardless of per-connection cleanup timing.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

BASE_DIR       = Path(__file__).resolve().parent.parent
_API_KEYS_FILE = BASE_DIR / "config" / "api_keys.json"

TARGET_FPS      = 20            # requested capture/encode rate (within the 15-30 FPS target)
JPEG_QUALITY    = 65
FRAME_MAX_W     = 960            # downscale cap — keeps frames small for <300ms LAN latency
READ_FAIL_LIMIT = 15             # consecutive failed reads before the device is reopened


def _camera_index() -> int:
    """Reuse the camera index already detected/cached by the desktop app (ui.py / screen_processor.py)."""
    try:
        cfg = json.loads(_API_KEYS_FILE.read_text(encoding="utf-8"))
        return int(cfg.get("camera_index", 0))
    except Exception:
        return 0


class _Subscriber:
    __slots__ = ("id", "queue", "loop", "closed_event")

    def __init__(self, queue: "asyncio.Queue", loop: "asyncio.AbstractEventLoop"):
        self.id           = -1
        self.queue        = queue
        self.loop         = loop
        self.closed_event = asyncio.Event()


class CameraManager:
    """One instance is created in dashboard/server.py and shared by every phone connection."""

    def __init__(self):
        self._lock         = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_evt     = threading.Event()
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_id      = 0
        self._active       = False
        self._last_error   = ""
        self._fps          = 0.0

    # ── status (cheap reads, safe from any thread) ──────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def viewer_count(self) -> int:
        return len(self._subscribers)

    @property
    def fps(self) -> float:
        return round(self._fps, 1)

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def available(self) -> bool:
        return _CV2_OK

    # ── subscription (called from async websocket handlers) ────────────────

    async def subscribe(self) -> _Subscriber:
        loop = asyncio.get_event_loop()
        sub = _Subscriber(queue=asyncio.Queue(maxsize=2), loop=loop)
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            sub.id = sid
            self._subscribers[sid] = sub
            need_start = not self._active
        if need_start:
            self._start()
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.pop(sub.id, None)
            empty = not self._subscribers
        if empty:
            # Signal the capture thread to stop; it releases the device and exits on its own.
            # No join() here — this can be called from an asyncio handler and must not block.
            self._stop_evt.set()

    def force_stop_all(self) -> None:
        """Used by 'Stop Remote Access' — immediately kick every viewer and release the camera."""
        with self._lock:
            subs = list(self._subscribers.values())
            self._subscribers.clear()
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.closed_event.set)
            except Exception:
                pass
        self._stop_evt.set()

    # ── internal capture thread ─────────────────────────────────────────────

    def _start(self) -> None:
        with self._lock:
            if self._active:
                return
            if not _CV2_OK:
                self._last_error = "opencv-python not installed"
                return
            self._active = True
            self._last_error = ""
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="phone-camera-stream"
            )
            self._thread.start()

    def _open_capture(self):
        import sys
        idx = _camera_index()
        try:
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        except AttributeError:
            backend = 0
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(0)
        return cap

    def _broadcast(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            subs = list(self._subscribers.values())
        for sub in subs:
            def _put(q=sub.queue, data=jpeg_bytes):
                if q.full():
                    try:
                        q.get_nowait()          # drop the oldest frame, never block on a slow phone
                    except Exception:
                        pass
                try:
                    q.put_nowait(data)
                except Exception:
                    pass
            try:
                sub.loop.call_soon_threadsafe(_put)
            except Exception:
                pass

    def _run(self) -> None:
        cap = self._open_capture()
        fail_count       = 0
        frame_interval   = 1.0 / TARGET_FPS
        last_tick        = time.monotonic()
        frame_count      = 0
        fps_window_start = time.monotonic()

        try:
            if not cap.isOpened():
                self._last_error = "Camera unavailable"
                return

            for _ in range(3):
                cap.read()   # warm-up — first frames from some webcams are dark/stale

            while not self._stop_evt.is_set():
                now = time.monotonic()
                elapsed = now - last_tick
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
                last_tick = time.monotonic()

                ret, frame = cap.read()
                if not ret or frame is None:
                    fail_count += 1
                    if fail_count >= READ_FAIL_LIMIT:
                        try:
                            cap.release()
                        except Exception:
                            pass
                        cap = self._open_capture()
                        fail_count = 0
                        if not cap.isOpened():
                            time.sleep(1.0)
                    continue
                fail_count = 0

                h, w = frame.shape[:2]
                if w > FRAME_MAX_W:
                    scale = FRAME_MAX_W / float(w)
                    frame = cv2.resize(frame, (FRAME_MAX_W, int(h * scale)),
                                        interpolation=cv2.INTER_AREA)

                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if not ok:
                    continue
                self._broadcast(buf.tobytes())

                frame_count += 1
                if now - fps_window_start >= 1.0:
                    self._fps = frame_count / (now - fps_window_start)
                    frame_count = 0
                    fps_window_start = now

                with self._lock:
                    if not self._subscribers:
                        break     # last viewer left mid-frame — don't linger with the device open
        except Exception as e:
            self._last_error = str(e)
        finally:
            try:
                cap.release()
            except Exception:
                pass
            with self._lock:
                self._active = False
                self._thread = None
                self._fps = 0.0


# Module-level singleton — imported once and shared by dashboard/server.py
camera_manager = CameraManager()
