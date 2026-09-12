"""
dashboard/server.py — JARVIS Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import socket
import string
import time
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

from dashboard.camera_service import camera_manager
from dashboard.phone_camera import phone_camera
from dashboard.wifi_pair import WifiPairManager
from dashboard.discovery import DiscoveryService
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "static"
PORT = 8000
MAX_UPLOAD_MB = 500

# Persistent device-session lifetime (Feature 2 — "Persistent Remote Connection").
# A device stays paired without rescanning the QR until either limit is hit, or until
# a manual Disconnect / Stop Remote Access happens.
DEVICE_SESSION_TTL_SECS = 30 * 24 * 3600          # absolute max lifetime since pairing
DEVICE_INACTIVITY_TIMEOUT_SECS = 7 * 24 * 3600    # expires if unused for this long


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()


def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None


_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw = base64.b64decode(enc_b64)
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN = ("https://cdnjs.cloudflare.com/ajax/libs/"
                 "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule = "JARVIS Dashboard Python"
        py_exe = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port = not _netsh_rule_exists(port_rule)
        need_prog = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp "
                      f"--permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip = _local_ip()
        self._tokens: set[str] = set()
        self._token_keys: dict[str, str] = {}        # auth_token → session_key
        self._aes_cache: dict[str, bytes] = {}        # session_key → AES bytes
        self._clients: set[WebSocket] = set()
        self._history: list[dict] = []
        self._command_queue = asyncio.Queue()
        self._wake_callback = None
        self._connect_callback = None
        self._disconnect_callback = None              # fired when a device disconnects / access is stopped
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}   # device_token → {session_key, created, last_used}
        self._token_device: dict[str, str] = {}       # auth_token → device_token (for targeted disconnect)
        self._ws_tokens: dict[WebSocket, str] = {}     # live /ws connections → their auth token
        self._camera_ws_tokens: dict[WebSocket, str] = {}  # live /ws/camera connections → their auth token
        self._phone_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._uploads_dir = UPLOADS_DIR
        self._login_html = _read("login.html")
        self._app_html = _read("app.html")
        self._camera_html = _read("camera.html")
        self._phone_camera_html = _read("phone_camera.html")
        self._remote_pin = "123456"
        self._pair = WifiPairManager()
        self._discovery = DiscoveryService(self._ip, PORT, socket.gethostname())
        self.app = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def generate_pair_code(self):
        return self._pair.new_pair_code()

    def set_remote_pin(self, pin: str):
        self._remote_pin = str(pin)

    def get_remote_pin(self):
        return self._remote_pin

    def get_pair_code(self):
        return self._pair.current_code

    def get_pair_ip(self):
        return self._pair.ip

    def verify_pair_code(self, code):
        return self._pair.verify(code)

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_disconnect_callback(self, fn) -> None:
        self._disconnect_callback = fn

    # ── status (for desktop UI — Feature 5 / Feature 9) ─────────────────────

    def get_status(self) -> dict:
        """Cheap, synchronous snapshot — safe to call directly from the Qt thread."""
        return {
            "connected": len(self._tokens) > 0,
            "device_count": len(self._tokens),
            "camera_active": camera_manager.active,
            "camera_fps": camera_manager.fps,
        }

    # ── device-session expiry (Feature 2 / Feature 6) ───────────────────────

    def _device_session_valid(self, dev_tok: str) -> dict | None:
        """Returns the session dict if dev_tok is still valid, else purges it and returns None."""
        sess = self._device_sessions.get(dev_tok)
        if not sess:
            return None
        now = time.time()
        if now - sess["created"] > DEVICE_SESSION_TTL_SECS:
            self._device_sessions.pop(dev_tok, None)
            return None
        if now - sess["last_used"] > DEVICE_INACTIVITY_TIMEOUT_SECS:
            self._device_sessions.pop(dev_tok, None)
            return None
        sess["last_used"] = now
        return sess

    # ── Stop Remote Access (Feature 3 — desktop-only, called via main.py) ──

    async def async_stop_remote_access(self) -> int:
        """Invalidate every session/token, disconnect every phone, stop the camera stream,
        and require a fresh QR scan for any future connection. Must run on the dashboard's
        own event loop — call via asyncio.run_coroutine_threadsafe from the Qt thread."""
        count = len(self._tokens)

        self._tokens.clear()
        self._token_keys.clear()
        self._token_device.clear()
        self._device_sessions.clear()
        self._pending_keys.clear()       # any unscanned/old QR code becomes invalid immediately
        self._aes_cache.clear()
        self._history.clear()

        camera_manager.force_stop_all()

        for ws in list(self._clients):
            try:
                await ws.send_json({"type": "force_disconnect", "reason": "stopped_by_desktop"})
                await ws.close(code=4002)
            except Exception:
                pass
        self._clients.clear()
        self._ws_tokens.clear()

        for ws in list(self._camera_ws_tokens):
            try:
                await ws.close(code=4002)
            except Exception:
                pass
        self._camera_ws_tokens.clear()

        if self._disconnect_callback:
            self._disconnect_callback()
        return count

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── login/session helper (shared by /login and /auto-login) ────────────

    def _create_login(self, session_key: str, device_token: str | None = None) -> str:
        tok = secrets.token_urlsafe(32)

        self._tokens.add(tok)
        self._token_keys[tok] = session_key

        if device_token:
            self._token_device[tok] = device_token

        self._aes_key(session_key)

        if self._connect_callback:
            self._connect_callback()

        asyncio.create_task(
            self.broadcast({
                "type": "sys",
                "text": "Remote connection established."
            })
        )

        return tok

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                     media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            print("====== LOGIN REQUEST RECEIVED ======")

            body = await req.json()
            print(body)

            entered_pin = str(body.get("pin", "")).strip()
            device_token = str(body.get("device_token", "")).strip()

            # PIN check
            if entered_pin != self._remote_pin:
                return JSONResponse(
                    {"ok": False, "error": "Wrong PIN"},
                    status_code=401
                )

            # -------------------------------------------------
            # Already paired device
            # -------------------------------------------------
            if device_token:
                sess = self._device_session_valid(device_token)

                if sess:
                    tok = self._create_login(
                        sess["session_key"],
                        device_token
                    )

                    return JSONResponse({
                        "ok": True,
                        "token": tok
                    })

            # -------------------------------------------------
            # First time (QR required)
            # -------------------------------------------------
            if len(self._pending_keys) == 0:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "Please scan the QR code first."
                    },
                    status_code=401
                )

            # Get latest QR session
            session_key = next(iter(self._pending_keys))
            del self._pending_keys[session_key]

            dev_tok = secrets.token_urlsafe(32)

            self._device_sessions[dev_tok] = {
                "session_key": session_key,
                "created": time.time(),
                "last_used": time.time(),
            }

            tok = self._create_login(
                session_key,
                dev_tok
            )

            return JSONResponse({
                "ok": True,
                "token": tok,
                "device_token": dev_tok
            })

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in JARVIS to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            now = time.time()
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._token_device[tok] = dev_tok
            self._aes_key(key)
            self._device_sessions[dev_tok] = {
                "session_key": key, "created": now, "last_used": now,
            }

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
</head>
<body>

<script>
localStorage.setItem("jarvis_device_token", "{dev_tok}");
window.location.replace("/login");
</script>

</body>
</html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            sess = self._device_session_valid(dev_tok) if dev_tok else None
            if not sess:
                return JSONResponse({"ok": False, "error": "Session expired"}, status_code=401)
            session_key = sess["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._token_device[tok] = dev_tok
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.post("/api/disconnect")
        async def disconnect_ep(req: Request):
            """Phone's own 'Disconnect' button (Feature 4) — ends only this device's session."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            try:
                body = await req.json()
            except Exception:
                body = {}
            dev_tok = (body.get("device_token") or self._token_device.get(tok) or "").strip()

            self._device_sessions.pop(dev_tok, None)
            self._token_device.pop(tok, None)
            self._tokens.discard(tok)
            self._token_keys.pop(tok, None)

            for ws, wtok in list(self._ws_tokens.items()):
                if wtok == tok:
                    try:
                        await ws.close(code=4000)
                    except Exception:
                        pass
                    self._clients.discard(ws)
                    self._ws_tokens.pop(ws, None)

            for ws, wtok in list(self._camera_ws_tokens.items()):
                if wtok == tok:
                    try:
                        await ws.close(code=4000)
                    except Exception:
                        pass
                    self._camera_ws_tokens.pop(ws, None)

            asyncio.create_task(self.broadcast({"type": "sys", "text": "Phone disconnected."}))
            if self._disconnect_callback:
                self._disconnect_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Autonomous Self-Engineering Status ─────────────────────────────────────

        @app.get("/api/engineering/status")
        async def engineering_status(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            # Return current engineering status from the orchestrator
            # This will be populated by the main.py integration
            return JSONResponse({
                "ok": True,
                "status": self._engineering_status if hasattr(self, "_engineering_status") else None
            })

        @app.get("/api/engineering/history")
        async def engineering_history(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            # Return engineering history
            return JSONResponse({
                "ok": True,
                "history": self._engineering_history if hasattr(self, "_engineering_history") else []
            })

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            self._ws_tokens[websocket] = tok
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
                    elif data.get("type") == "ping":
                        await websocket.send_json({"type": "pong", "t": data.get("t")})
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)
                self._ws_tokens.pop(websocket, None)

        # ── Live Camera page + stream (Feature 1) ─────────────────────────────

        @app.get("/camera", response_class=HTMLResponse)
        async def camera_page():
            return HTMLResponse(self._camera_html)

        @app.websocket("/ws/camera")
        async def camera_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            if not camera_manager.available:
                await websocket.accept()
                await websocket.send_json({"type": "camera_error", "error": "opencv-python not installed"})
                await websocket.close(code=4003)
                return

            await websocket.accept()
            self._camera_ws_tokens[websocket] = tok
            sub = await camera_manager.subscribe()
            asyncio.create_task(self.broadcast({"type": "sys", "text": "Phone viewing live camera."}))

            async def _send_frames():
                while True:
                    get_task = asyncio.create_task(sub.queue.get())
                    close_task = asyncio.create_task(sub.closed_event.wait())
                    done, pending = await asyncio.wait(
                        {get_task, close_task}, timeout=5.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for p in pending:
                        p.cancel()
                    if close_task in done:
                        await websocket.send_json({"type": "camera_stopped", "reason": "stopped_by_desktop"})
                        return
                    if get_task in done:
                        await websocket.send_bytes(get_task.result())
                    elif not camera_manager.active and camera_manager.last_error:
                        # 5s with no frame and the capture thread died — report once.
                        await websocket.send_json(
                            {"type": "camera_error", "error": camera_manager.last_error}
                        )

            async def _recv_pings():
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "ping":
                        await websocket.send_json({"type": "pong", "t": data.get("t")})

            try:
                tasks = [asyncio.create_task(_send_frames()), asyncio.create_task(_recv_pings())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
            except WebSocketDisconnect:
                pass
            finally:
                camera_manager.unsubscribe(sub)
                self._camera_ws_tokens.pop(websocket, None)
                asyncio.create_task(self.broadcast({"type": "sys", "text": "Phone stopped viewing camera."}))
                # ── Phone as camera (Feature: phone_camera) ─────────────────────
        # The phone opens this page in its browser. If a valid one-time key
        # is in the URL (?key=XXXXXX), we mint a fresh session for that phone
        # and embed the token directly. Otherwise the page tries auto-login
        # with the device token already stored from a previous pairing.

        @app.get("/phone-camera", response_class=HTMLResponse)
        async def phone_camera_page(key: str = ""):
            html = self._phone_camera_html

            now = time.time()
            if key and key in self._pending_keys and self._pending_keys[key] > now:
                del self._pending_keys[key]
                dev_tok = secrets.token_urlsafe(32)
                self._device_sessions[dev_tok] = {
                    "session_key": key,
                    "created":     now,
                    "last_used":   now,
                }
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = key
                self._token_device[tok] = dev_tok
                self._aes_key(key)
                if self._connect_callback:
                    self._connect_callback()
                html = (html
                        .replace("__TOKEN__", tok)
                        .replace("__DEVICE_TOKEN__", dev_tok))
            else:
                html = (html
                        .replace("__TOKEN__", "")
                        .replace("__DEVICE_TOKEN__", ""))
            return HTMLResponse(html)

        @app.websocket("/ws/phone-camera")
        async def phone_camera_ws(websocket: WebSocket, token: str = ""):
            tok = (token or "").strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return

            await websocket.accept()
            phone_camera.on_connect()
            asyncio.create_task(self.broadcast({
                "type": "sys",
                "text": "Phone camera connected.",
            }))

            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break

                    data = msg.get("bytes")
                    if data:
                        phone_camera.on_frame(data)

                    text = msg.get("text")
                    if text:
                        try:
                            info = json.loads(text)
                            if info.get("type") == "hello":
                                phone_camera.on_hello(
                                    int(info.get("width", 0)),
                                    int(info.get("height", 0)),
                                )
                        except Exception:
                            pass
            except WebSocketDisconnect:
                pass
            finally:
                phone_camera.on_disconnect()
                asyncio.create_task(self.broadcast({
                    "type": "sys",
                    "text": "Phone camera disconnected.",
                }))
        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        print("=" * 60)
        print("Starting Uvicorn Server...")
        print(f"IP   : {self._ip}")
        print(f"PORT : {PORT}")
        print("=" * 60)
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        print("=" * 60)
        print("Dashboard serve() called")
        print("=" * 60)
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        use_ssl = self._ssl_enabled()
        ssl_key = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        self._discovery.start()
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")
        await uvicorn.Server(cfg).serve()