#process_utils.py
"""
Shared helper for closing a running application by name.

Both computer_settings.close_app() and browser_control's close/close_all used
to just fire a hotkey (Alt+F4 / Cmd+Q) at whatever window happened to have OS
focus, or look inside an internal registry that only knows about automation
sessions it started itself. Neither approach can reliably close "Chrome" (or
any app) that the user actually has open, because focus may have moved
elsewhere, or the app was opened natively rather than via automation.

This module finds the real OS process(es) for a spoken app name and closes
them properly: terminate() first (clean shutdown), then kill() anything still
alive after a short grace period. It reports back exactly what happened so
Jarvis can give an honest answer instead of a silent no-op.
"""

import os

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


def _protected_pids() -> set:
    """
    PIDs that must never be matched, no matter what token is searched for:
    Jarvis's own process and every one of its ancestors (the terminal/IDE/
    service that launched it). A broad token like 'python' or 'code' should
    never be able to take Jarvis down with the app it was asked to close.
    """
    if not _PSUTIL:
        return {os.getpid()}
    protected = {os.getpid()}
    try:
        proc = psutil.Process(os.getpid())
        for ancestor in proc.parents():
            protected.add(ancestor.pid)
    except Exception:
        pass
    return protected

# Search tokens used to match a running process to a spoken app name.
# Checked (case-insensitive) against the process name, exe path, and full
# command line, so this works across Windows/macOS/Linux without needing an
# exact executable name for every platform.
_CLOSE_TOKENS: dict[str, list[str]] = {
    "chrome":             ["chrome"],
    "google chrome":      ["chrome"],
    "firefox":            ["firefox"],
    "edge":               ["msedge", "microsoft edge"],
    "brave":              ["brave"],
    "opera":              ["opera"],
    "safari":             ["safari"],
    "whatsapp":           ["whatsapp"],
    "telegram":           ["telegram"],
    "discord":            ["discord"],
    "slack":              ["slack"],
    "zoom":               ["zoom"],
    "teams":              ["teams"],
    "skype":              ["skype"],
    "signal":             ["signal"],
    "spotify":            ["spotify"],
    "vlc":                ["vlc"],
    "vscode":             ["code.exe", "code helper", "visual studio code"],
    "visual studio code": ["code.exe", "code helper", "visual studio code"],
    "code":               ["code.exe", "code helper", "visual studio code"],
    "word":               ["winword", "microsoft word"],
    "excel":              ["excel.exe", "microsoft excel"],
    "powerpoint":         ["powerpnt", "microsoft powerpoint"],
    "libreoffice":        ["soffice", "libreoffice"],
    "notepad":            ["notepad", "textedit"],
    "explorer":           ["explorer.exe"],
    "file explorer":      ["explorer.exe"],
    "finder":             ["finder"],
    "calculator":         ["calculator", "calc.exe", "gnome-calculator"],
    "paint":              ["mspaint", "preview", "gimp"],
    "steam":              ["steam"],
    "epic":               ["epicgameslauncher", "epic games launcher"],
    "epic games":         ["epicgameslauncher", "epic games launcher"],
    "notion":             ["notion"],
    "obsidian":           ["obsidian"],
    "postman":            ["postman"],
    "figma":              ["figma"],
    "blender":            ["blender"],
    "instagram":          ["instagram"],
    "tiktok":             ["tiktok"],
    "capcut":             ["capcut"],
}

# Never match Jarvis's own process (or the Python interpreter running it)
# even if a broad token like "code" is used — that would let "close code"
# accidentally kill Jarvis itself.
_NEVER_KILL_HINTS = ["jarvis", "python.exe", "pythonw.exe"]


def _tokens_for(app_name: str) -> list[str]:
    key = app_name.lower().strip()
    if key in _CLOSE_TOKENS:
        return _CLOSE_TOKENS[key]
    for hint_key, tokens in _CLOSE_TOKENS.items():
        if hint_key in key or key in hint_key:
            return tokens
    return [key] if key else []


def _matches(proc_info: dict, tokens: list[str]) -> bool:
    name = (proc_info.get("name") or "").lower()
    exe = (proc_info.get("exe") or "").lower()
    cmd = " ".join(proc_info.get("cmdline") or []).lower()
    haystack = f"{name} {exe} {cmd}"
    if any(bad in haystack for bad in _NEVER_KILL_HINTS):
        return False
    return any(tok in haystack for tok in tokens if tok)


def find_processes(app_name: str) -> list:
    """Return running psutil.Process objects that match the spoken app name."""
    if not _PSUTIL or not app_name:
        return []
    tokens = _tokens_for(app_name)
    protected = _protected_pids()
    matches = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            if proc.info.get("pid") in protected:
                continue
            if _matches(proc.info, tokens):
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


def _prefer_main_processes(procs: list) -> list:
    """
    Chromium-based apps (Chrome, Edge, Brave, Discord, Slack…) run many child
    renderer/GPU/utility processes that all match the same name. Terminating
    only the ones NOT tagged '--type=' lets the app shut itself (and its
    children) down cleanly instead of killing every child process directly.
    Falls back to all matches if that filter would leave nothing.
    """
    filtered = [
        p for p in procs
        if "--type=" not in " ".join(p.info.get("cmdline") or [])
    ]
    return filtered or procs


def close_app_by_name(app_name: str, graceful_timeout: float = 3.0) -> dict:
    """
    Find every running process matching app_name and close it properly:
    terminate() first, then kill() anything still alive after the grace
    period.

    Returns:
        {
            "found": bool,          # was anything matching even running?
            "closed": int,          # processes confirmed gone
            "forced": int,          # of those, how many needed a force-kill
            "still_running": int,   # processes that would not die at all
        }
    """
    if not _PSUTIL:
        return {"found": False, "closed": 0, "forced": 0,
                "still_running": 0, "error": "psutil not installed"}

    all_matches = find_processes(app_name)
    if not all_matches:
        return {"found": False, "closed": 0, "forced": 0, "still_running": 0}

    targets = _prefer_main_processes(all_matches)

    terminated = []
    for proc in targets:
        try:
            proc.terminate()
            terminated.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    gone, alive = psutil.wait_procs(terminated, timeout=graceful_timeout)

    forced = 0
    if alive:
        for proc in alive:
            try:
                proc.kill()
                forced += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive2 = psutil.wait_procs(alive, timeout=2.0)
        still_running = len(alive2)
    else:
        still_running = 0

    return {
        "found": True,
        "closed": len(terminated) - still_running,
        "forced": forced,
        "still_running": still_running,
    }


def is_running(app_name: str) -> bool:
    """Quick check used for verification commands: is this app running right now?"""
    return len(find_processes(app_name)) > 0
