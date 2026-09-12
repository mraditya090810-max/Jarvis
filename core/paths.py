"""
core/paths.py — single canonical path resolver for JARVIS.

Every module that needs project root, temp, or OS special folders
(Desktop, Documents, Downloads, …) should import from here instead of
re-implementing get_base_dir() or Path.home() / "Desktop".
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path

_OS = platform.system()

_SHELL_FOLDER_NAMES = {
    "desktop":   "Desktop",
    "documents": "Personal",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures":  "My Pictures",
    "music":     "My Music",
    "videos":    "My Video",
}

_XDG_ENV = {
    "desktop":   "XDG_DESKTOP_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "downloads": "XDG_DOWNLOAD_DIR",
    "pictures":  "XDG_PICTURES_DIR",
    "music":     "XDG_MUSIC_DIR",
    "videos":    "XDG_VIDEOS_DIR",
}

_FALLBACK_NAME = {
    "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
    "pictures": "Pictures", "music": "Music", "videos": "Videos",
}

_special_cache: dict[str, Path] = {}


def get_base_dir() -> Path:
    """Project root — works in dev and PyInstaller frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_temp_dir() -> Path:
    return Path(tempfile.gettempdir())


def _windows_shell_folder(key: str) -> Path | None:
    try:
        import winreg
    except ImportError:
        return None

    value_name = _SHELL_FOLDER_NAMES[key]
    for hive_path in (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, hive_path) as hkey:
                raw, _ = winreg.QueryValueEx(hkey, value_name)
                expanded = os.path.expandvars(raw)
                p = Path(expanded)
                if p.exists():
                    return p
        except OSError:
            continue
    return None


def _windows_known_folder(key: str) -> Path | None:
    import ctypes
    from ctypes import wintypes

    guids = {
        "desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
        "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "pictures":  "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
        "music":     "{4BD8D571-6D19-48D3-BE97-422220080E43}",
        "videos":    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    }
    if key not in guids:
        return None
    try:
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8),
            ]

        buf = ctypes.c_wchar_p()
        guid = GUID()
        ctypes.windll.ole32.CLSIDFromString(guids[key], ctypes.byref(guid))
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, 0, ctypes.byref(buf)
        )
        if result == 0 and buf.value:
            p = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)
            if p.exists():
                return p
    except Exception:
        return None
    return None


def get_special_folder(key: str) -> Path:
    """Resolve desktop | documents | downloads | pictures | music | videos."""
    key = key.lower()
    if key in _special_cache:
        return _special_cache[key]

    resolved: Path | None = None

    if _OS == "Windows":
        resolved = _windows_shell_folder(key) or _windows_known_folder(key)
    elif _OS == "Linux":
        xdg = os.environ.get(_XDG_ENV.get(key, ""), "")
        if xdg and Path(xdg).exists():
            resolved = Path(xdg)

    if resolved is None:
        resolved = Path.home() / _FALLBACK_NAME.get(key, key.capitalize())

    _special_cache[key] = resolved
    return resolved


def clear_cache() -> None:
    _special_cache.clear()
