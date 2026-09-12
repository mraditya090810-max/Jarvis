#permission_utils.py
"""
Cross-platform helpers to detect *why* a system-control action silently did
nothing, and to run a single up-front diagnostic ("check permissions") that
tells the user exactly what to grant/elevate — instead of a vague apology
with no next step.

Root problem this fixes: most of computer_settings.py shells out to
osascript / PowerShell / brightnessctl with capture_output=True and never
looks at the return code or stderr — so even when the OS refuses the action
(missing macOS Accessibility/Automation permission, missing Windows admin
rights, missing Linux backlight group membership), the function returns
normally and Jarvis reports "Done." This module makes failures visible and
translates the common cases into a plain-language fix.
"""
import os
import platform
import subprocess

_OS = platform.system()


def run_checked(cmd, timeout: int = 8, shell: bool = False, **kwargs) -> tuple:
    """
    Run a command and return (ok, message).
    ok=False for a non-zero exit code OR a raised exception; message
    translates the most common permission-related failures into plain
    language instead of a raw OS error string.
    """
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, timeout=timeout, **kwargs
        )
    except Exception as e:
        return False, f"Could not run command: {e}"

    if result.returncode == 0:
        return True, ""

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    combined = (stderr + " " + stdout).lower()

    if "not authorized to send apple events" in combined or "-1743" in combined:
        return False, (
            "macOS is blocking this — Jarvis doesn't have Automation permission. "
            "Go to System Settings -> Privacy & Security -> Automation, find the app you "
            "run Jarvis from (Terminal / your packaged app), and enable 'System Events'."
        )
    if "not allowed assistive access" in combined or "-1719" in combined:
        return False, (
            "macOS is blocking this — Jarvis doesn't have Accessibility permission. "
            "Go to System Settings -> Privacy & Security -> Accessibility, enable the app "
            "you run Jarvis from (Terminal / your packaged app), then restart Jarvis."
        )
    if "access is denied" in combined or "accessdenied" in combined.replace(" ", ""):
        return False, (
            "Windows denied this — it needs administrator rights. "
            "Close Jarvis and re-launch it with 'Run as administrator'."
        )
    if "permission denied" in combined:
        return False, f"Permission denied by the OS: {stderr[:200] or stdout[:200]}"

    return False, f"Command failed ({result.returncode}): {stderr[:200] or stdout[:200] or 'no error output'}"


def is_windows_admin() -> bool:
    if _OS != "Windows":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def macos_automation_ok() -> tuple:
    """Can Jarvis control 'System Events'? (needed for volume/dark-mode/restart via osascript)"""
    if _OS != "Darwin":
        return True, ""
    return run_checked(["osascript", "-e", 'tell application "System Events" to name of first process'])


def macos_accessibility_ok() -> tuple:
    """Can Jarvis send keystrokes? (needed for pyautogui: minimize/maximize/volume hotkeys)"""
    if _OS != "Darwin":
        return True, ""
    return run_checked(["osascript", "-e", 'tell application "System Events" to keystroke ""'])


def linux_display_server() -> str:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def full_permission_report() -> str:
    lines = [f"Permission check ({_OS}):"]

    if _OS == "Windows":
        admin = is_windows_admin()
        lines.append(
            f"  - Administrator rights: {'YES' if admin else 'NO (required for WiFi toggle, some brightness controls)'}"
        )
        if not admin:
            lines.append("    Fix: right-click Jarvis's shortcut/terminal -> 'Run as administrator'.")

    elif _OS == "Darwin":
        auto_ok, auto_msg = macos_automation_ok()
        lines.append(f"  - Automation (System Events): {'YES' if auto_ok else 'NO'}")
        if not auto_ok:
            lines.append(f"    Fix: {auto_msg}")

        acc_ok, acc_msg = macos_accessibility_ok()
        lines.append(f"  - Accessibility (keystrokes/hotkeys): {'YES' if acc_ok else 'NO'}")
        if not acc_ok:
            lines.append(f"    Fix: {acc_msg}")

    else:  # Linux
        display = linux_display_server()
        lines.append(f"  - Display server: {display}")
        if display == "wayland":
            lines.append(
                "    Warning: keyboard/mouse automation (minimize, maximize, volume hotkeys) "
                "does NOT work on a native Wayland session — this is an OS-level restriction, "
                "not a bug. Log in to an 'Ubuntu on Xorg' / X11 session instead."
            )
        elif display == "none":
            lines.append("    Warning: no DISPLAY/WAYLAND_DISPLAY detected — is Jarvis running headless?")

        if subprocess.run(["which", "brightnessctl"], capture_output=True).returncode == 0:
            ok, msg = run_checked(["brightnessctl", "set", "+0%"])
            lines.append(f"  - brightnessctl write access: {'YES' if ok else 'NO'}")
            if not ok:
                lines.append("    Fix: sudo usermod -aG video $USER   (then log out and back in)")
        else:
            lines.append("  - brightnessctl: not installed (sudo apt install brightnessctl)")

        nmcli_present = subprocess.run(["which", "nmcli"], capture_output=True).returncode == 0
        lines.append(f"  - nmcli (WiFi toggle): {'installed' if nmcli_present else 'NOT installed'}")

    return "\n".join(lines)
