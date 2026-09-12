"""
browser_agent.widget — embedded Chromium browser for the JARVIS HUD.

Design notes
────────────
• Uses QtWebEngine (Blink/V8 — the same engine family as Chrome) so the
  browser is a real Chromium instance rendered inside the HUD's camera slot.
• Persistent profile at ~/.jarvis_browser — logins survive across runs,
  cookies stay, but the user's personal Chrome profile is never touched.
• run_js() is thread-safe. From a background thread it marshals the call to
  the Qt main thread via a queued signal and blocks on a threading.Event.
• No external process is ever spawned. The widget IS the browser.
"""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QSizePolicy,
)

# ── QtWebEngine availability ────────────────────────────────────────────────
# ui.py imports this module *before* QApplication is created (see ui.py's
# comment near the top) — QtWebEngineWidgets must precede QApplication.
_WEBENGINE_OK = False
_WEBENGINE_ERR = ""
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile, QWebEnginePage, QWebEngineSettings,
    )
    _WEBENGINE_OK = True
except Exception as _e:  # ImportError or any binding error
    _WEBENGINE_ERR = str(_e)


PROFILE_DIR = Path.home() / ".jarvis_browser"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


# ── shared persistent profile ───────────────────────────────────────────────
_profile: Optional["QWebEngineProfile"] = None


def _shared_profile() -> "QWebEngineProfile":
    """
    One persistent profile for the whole process. Cookies / storage survive
    restarts, so users only log in once per site. Passwords are never touched:
    we don't enable the Chromium password manager, and we never read its DB.
    """
    global _profile
    if _profile is None:
        _profile = QWebEngineProfile("jarvis", None)
        _profile.setPersistentStoragePath(str(PROFILE_DIR / "storage"))
        _profile.setCachePath(str(PROFILE_DIR / "cache"))
        _profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        _profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.DiskHttpCache
        )
        # A normal browser-like UA so sites don't serve us a broken page.
        _profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return _profile


class BrowserView(QWidget):
    """
    Embedded Chromium browser + thin header (title / URL / status) + nav row.

    Public API (all callable from any thread):
        view.run_js(script, timeout=)   → Any        (blocks)
        view.navigate(url)              → None       (queued)
        view.grab_png()                 → bytes|None (blocks)
        view.current_url()              → str        (blocks)
        view.set_status(text)           → None       (queued)
    """

    _js_request = pyqtSignal(str, str)     # (call_id, script)
    _nav_request = pyqtSignal(str)         # url
    _status_request = pyqtSignal(str)      # text
    _title_request = pyqtSignal(str)       # text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._pending_lock = threading.Lock()
        self._last_title = "BROWSER"
        self._build_ui()
        self._wire_signals()

        if not _WEBENGINE_OK:
            self._show_unavailable()

    # ── UI construction ────────────────────────────────────────────────────
    def _build_ui(self):
        self.setStyleSheet("background: #000308;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── header row ─────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(28)
        hdr.setStyleSheet(
            "background: #010d14; border-bottom: 1px solid #0d3347;"
        )
        hb = QHBoxLayout(hdr)
        hb.setContentsMargins(10, 0, 8, 0)
        hb.setSpacing(8)

        self._title_lbl = QLabel("◈  BROWSER")
        self._title_lbl.setFont(self._mono(8, bold=True))
        self._title_lbl.setStyleSheet("color: #00d4ff; background: transparent;")
        hb.addWidget(self._title_lbl)

        self._url_lbl = QLabel("about:blank")
        self._url_lbl.setFont(self._mono(7))
        self._url_lbl.setStyleSheet("color: #3a8a9a; background: transparent;")
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hb.addWidget(self._url_lbl, stretch=1)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(self._mono(7, bold=True))
        self._status_lbl.setStyleSheet("color: #00ff88; background: transparent;")
        hb.addWidget(self._status_lbl)

        self._close_btn = QPushButton("✕  CLOSE")
        self._close_btn.setFont(self._mono(8, bold=True))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(
            "QPushButton{color:#3a8a9a;background:transparent;border:none;padding:2px 6px;}"
            "QPushButton:hover{color:#00d4ff;}"
        )
        self._close_btn.clicked.connect(self._on_close_clicked)
        hb.addWidget(self._close_btn)

        root.addWidget(hdr)

        # ── progress bar (thin) ────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFixedHeight(2)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar{background:#010d14;border:none;}"
            "QProgressBar::chunk{background:#00d4ff;}"
        )
        root.addWidget(self._progress)

        # ── web view ───────────────────────────────────────────────────────
        if _WEBENGINE_OK:
            self._view = QWebEngineView(self)
            page = QWebEnginePage(_shared_profile(), self._view)
            self._view.setPage(page)
            s = self._view.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
            self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            root.addWidget(self._view, stretch=1)
            self._view.loadFinished.connect(self._on_load_finished)
            self._view.titleChanged.connect(self._on_title)
            self._view.urlChanged.connect(self._on_url)
            self._view.loadProgress.connect(self._progress.setValue)
        else:
            self._view = None
            placeholder = QLabel(
                "QtWebEngine is not installed.\n\n"
                "Install it with:\n"
                "    pip install PyQt6-WebEngine\n\n"
                "Then restart JARVIS."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setFont(self._mono(9))
            placeholder.setStyleSheet("color:#ff6b00;background:transparent;padding:40px;")
            root.addWidget(placeholder, stretch=1)

        # ── footer with quick nav (back / forward / reload / home) ─────────
        nav = QWidget()
        nav.setFixedHeight(26)
        nav.setStyleSheet("background:#010d14;border-top:1px solid #0d3347;")
        nb = QHBoxLayout(nav)
        nb.setContentsMargins(8, 0, 8, 0)
        nb.setSpacing(4)

        for label, slot in (
            ("◀", self._go_back),
            ("▶", self._go_forward),
            ("⟳", self._reload),
            ("⌂", self._go_home),
        ):
            b = QPushButton(label)
            b.setFixedSize(24, 22)
            b.setFont(self._mono(9, bold=True))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{color:#5ab8cc;background:transparent;"
                "border:1px solid #0d3347;border-radius:3px;}"
                "QPushButton:hover{color:#00d4ff;border-color:#1a5c7a;}"
            )
            b.clicked.connect(slot)
            nb.addWidget(b)

        nb.addStretch()
        self._spinner_lbl = QLabel("")
        self._spinner_lbl.setFont(self._mono(7))
        self._spinner_lbl.setStyleSheet("color:#3a8a9a;background:transparent;")
        nb.addWidget(self._spinner_lbl)

        root.addWidget(nav)

    @staticmethod
    def _mono(size: int, bold: bool = False):
        from PyQt6.QtGui import QFont
        return QFont("Courier New", size,
                     QFont.Weight.Bold if bold else QFont.Weight.Normal)

    def _show_unavailable(self):
        self._title_lbl.setText("◈  BROWSER — UNAVAILABLE")
        self._title_lbl.setStyleSheet("color:#ff3355;background:transparent;")
        self._status_lbl.setText(f"missing dependency")
        self._status_lbl.setStyleSheet("color:#ff3355;background:transparent;")

    # ── signal wiring ──────────────────────────────────────────────────────
    def _wire_signals(self):
        self._js_request.connect(self._handle_js_request, Qt.ConnectionType.QueuedConnection)
        self._nav_request.connect(self._handle_nav_request, Qt.ConnectionType.QueuedConnection)
        self._status_request.connect(self._handle_status_request, Qt.ConnectionType.QueuedConnection)
        self._title_request.connect(self._handle_title_request, Qt.ConnectionType.QueuedConnection)

    # ── thread-safe public API ─────────────────────────────────────────────
    def run_js(self, script: str, timeout: float = 6.0) -> Any:
        """
        Run JavaScript in the live page and return its (JSON-stringified) value.
        Safe to call from any thread; blocks the caller until the main thread
        has executed the script and the browser delivered the result.

        If the value returned by the script is a string, it is returned as-is.
        Otherwise None (the model should call scripts that JSON.stringify).
        """
        if self._view is None:
            raise RuntimeError("Browser unavailable: QtWebEngine not installed.")
        if threading.current_thread() is threading.main_thread():
            return self._run_js_on_main(script, timeout)

        call_id = secrets.token_urlsafe(8)
        event = threading.Event()
        holder: dict = {}
        with self._pending_lock:
            self._pending[call_id] = (event, holder)

        self._js_request.emit(call_id, script)

        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(call_id, None)
            raise TimeoutError(f"JS call timed out after {timeout:.1f}s")
        return holder.get("value")

    def _handle_js_request(self, call_id: str, script: str):
        """Runs on the Qt main thread."""
        if self._view is None:
            self._resolve_pending(call_id, None)
            return

        def _cb(value):
            self._resolve_pending(call_id, value)

        try:
            self._view.page().runJavaScript(script, _cb)
        except Exception:
            self._resolve_pending(call_id, None)

    def _resolve_pending(self, call_id: str, value: Any):
        with self._pending_lock:
            entry = self._pending.pop(call_id, None)
        if entry:
            event, holder = entry
            holder["value"] = value
            event.set()

    def _run_js_on_main(self, script: str, timeout: float) -> Any:
        """Rare — only used if something calls run_js from the main thread."""
        done = threading.Event()
        holder: dict = {}

        def _cb(v):
            holder["value"] = v
            done.set()

        self._view.page().runJavaScript(script, _cb)
        from PyQt6.QtWidgets import QApplication
        deadline = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        return holder.get("value")

    # ── navigation ─────────────────────────────────────────────────────────
    def navigate(self, url: str) -> None:
        """Queue a navigation. Never blocks the caller."""
        self._nav_request.emit(url)

    def _handle_nav_request(self, url: str):
        if self._view is None:
            return
        if "://" not in url and not url.startswith("about:"):
            url = "https://" + url if "." in url else "https://" + url + ".com"
        self._view.setUrl(QUrl(url))

    # ── status / title ─────────────────────────────────────────────────────
    def set_status(self, text: str) -> None:
        self._status_request.emit(text)

    def _handle_status_request(self, text: str):
        self._status_lbl.setText(text[:120])

    def set_panel_title(self, text: str) -> None:
        self._title_request.emit(text)

    def _handle_title_request(self, text: str):
        self._last_title = text
        self._title_lbl.setText(f"◈  {text.upper()[:48]}")

    # ── page hooks (main thread) ───────────────────────────────────────────
    def _on_load_finished(self, ok: bool):
        self._spinner_lbl.setText("ready" if ok else "load failed")

    def _on_title(self, title: str):
        self._spinner_lbl.setText(title[:60] if title else "")

    def _on_url(self, url: QUrl):
        u = url.toString()
        self._url_lbl.setText(u[:120])

    # ── nav buttons ────────────────────────────────────────────────────────
    def _go_back(self):
        if self._view: self._view.back()

    def _go_forward(self):
        if self._view: self._view.forward()

    def _reload(self):
        if self._view: self._view.reload()

    def _go_home(self):
        if self._view: self._view.setUrl(QUrl("about:blank"))

    # ── close hook ─────────────────────────────────────────────────────────
    _close_cb = None

    def set_close_callback(self, fn):
        """Called by MainWindow to hide the panel."""
        self._close_cb = fn

    def _on_close_clicked(self):
        if callable(self._close_cb):
            try:
                self._close_cb()
            except Exception:
                pass

    # ── screenshots (used by the agent's vision fallback) ─────────────────
    def grab_png(self) -> Optional[bytes]:
        """
        Return a PNG-encoded snapshot of the visible viewport, or None.
        Safe from any thread (marshals to main thread).
        """
        if self._view is None:
            return None

        result: dict = {}
        done = threading.Event()

        def _do():
            try:
                pix: QPixmap = self._view.grab()
                from PyQt6.QtCore import QBuffer, QIODevice
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                pix.save(buf, "PNG")
                result["png"] = bytes(buf.data())
            except Exception:
                result["png"] = None
            finally:
                done.set()

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            QTimer.singleShot(0, _do)
            if not done.wait(3.0):
                return None
        return result.get("png")

    def current_url(self) -> str:
        if self._view is None:
            return ""
        result: dict = {}
        done = threading.Event()

        def _do():
            try:
                result["url"] = self._view.url().toString()
            except Exception:
                result["url"] = ""
            finally:
                done.set()

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            QTimer.singleShot(0, _do)
            if not done.wait(2.0):
                return ""
        return result.get("url", "")


def webengine_available() -> bool:
    return _WEBENGINE_OK


def webengine_error() -> str:
    return _WEBENGINE_ERR