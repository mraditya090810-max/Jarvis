from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    import PIL.ImageGrab
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"

# Serialize screen captures — one backend at a time (live watch + one-shot).
_capture_lock = threading.Lock()

_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82

# Live watch: ~2 FPS keeps latency reasonable without flooding the Live session.
WATCH_FRAME_INTERVAL = 0.5


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_via_mss() -> Optional[bytes]:
    sct = _get_mss()
    if sct is None:
        return None
    try:
        monitors = sct.monitors
        target   = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)
        return mss.tools.to_png(shot.rgb, shot.size)
    except Exception as e:
        print(f"[Vision] mss capture failed: {e}")
        return None


def _capture_via_pil() -> Optional[bytes]:
    if not _PIL:
        return None
    try:
        img = PIL.ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"[Vision] PIL.ImageGrab capture failed: {e}")
        return None


def _capture_via_dxcam() -> Optional[bytes]:
    camera = _get_dxcam()
    if camera is None:
        return None
    try:
        frame = camera.grab()
        if frame is None:
            return None
        buf = io.BytesIO()
        PIL.Image.fromarray(frame).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"[Vision] DXCam capture failed: {e}")
        return None


# In-memory backends only — never write screenshots to disk.
_SCREENSHOT_BACKENDS = (
    _capture_via_mss,
    _capture_via_pil,
    _capture_via_dxcam,
)


# Reuse capture backends across calls — avoid creating dxcam/mss per frame.
_dxcam_instance = None
_mss_instance = None


def _get_dxcam():
    global _dxcam_instance
    if _dxcam_instance is not None:
        return _dxcam_instance
    try:
        import dxcam
        _dxcam_instance = dxcam.create()
    except Exception:
        pass
    return _dxcam_instance


def _get_mss():
    global _mss_instance
    if _mss_instance is not None:
        return _mss_instance
    if not _MSS:
        return None
    try:
        _mss_instance = mss.mss()
    except Exception:
        pass
    return _mss_instance


def _capture_screen_unlocked() -> tuple[bytes, str]:
    for backend in _SCREENSHOT_BACKENDS:
        png = backend()
        if png:
            return _compress(png, "PNG")
    raise RuntimeError(
        "Screenshot failed: no capture method available. "
        "Install one of: mss, Pillow, dxcam (pip install mss pillow dxcam)."
    )


def _capture_screen() -> tuple[bytes, str]:
    with _capture_lock:
        return _capture_screen_unlocked()


def _cv2_backend() -> int:
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:
    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:
    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    with _capture_lock:
        index   = _get_camera_index()
        backend = _cv2_backend()
        cap     = cv2.VideoCapture(index, backend)

        if not cap.isOpened():
            raise RuntimeError(f"Camera index {index} could not be opened.")

        for _ in range(10):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            raise RuntimeError("Camera returned no frame.")

        if _PIL:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = PIL.Image.fromarray(rgb)
            img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_Q)
            return buf.getvalue(), "image/jpeg"

        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
        return buf.tobytes(), "image/jpeg"


if __name__ == "__main__":
    print("[TEST] screen_processor.py — in-memory capture only")
    print("=" * 52)
    t0 = time.perf_counter()
    img, mime = _capture_screen()
    print(f"Screen: {len(img):,} bytes ({mime}) in {time.perf_counter() - t0:.2f}s")
