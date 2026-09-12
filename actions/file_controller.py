import os
import shutil
import platform
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

from actions._paths import get_special_folder
from memory import action_memory

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _SAFE_ROOTS = [Path(d) for d in ("C:\\", "D:\\", "E:\\", "F:\\", "G:\\") if Path(d).exists()]
else:
    _SAFE_ROOTS = [Path("/")]

def _is_safe_path(target: Path) -> bool:
    """Verilen path _SAFE_ROOTS içinde mi? Değilse işlemi reddet."""
    try:
        raw = str(target)
        if _OS == "Windows" and (raw.startswith("\\\\") or raw.startswith("//")):
            # Explicit network share the user asked for — trust it rather
            # than rejecting every UNC path outright.
            return True
        resolved = target.resolve()
        return any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        )
    except Exception:
        return False


_WIN_INVALID_CHARS = '<>:"|?*'
_WIN_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _sanitize_filename(name: str) -> str:
    """Make a user-supplied name safe to create on the current OS."""
    if not name or _OS != "Windows":
        return name
    cleaned = "".join(c for c in name if c not in _WIN_INVALID_CHARS).rstrip(" .")
    stem = Path(cleaned).stem.upper() if cleaned else ""
    if stem in _WIN_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned or "untitled"


def _long_path(target: Path) -> Path:
    """On Windows, use the \\\\?\\ prefix so paths beyond ~260 chars still work."""
    if _OS != "Windows":
        return target
    raw = str(target)
    if raw.startswith("\\\\?\\") or len(raw) <= 240:
        return target
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw.lstrip("\\"))
    return Path("\\\\?\\" + raw)


def _dedupe_path(target: Path) -> Path:
    """If target already exists, return a 'name (1).ext'-style path instead
    of silently overwriting whatever is already there."""
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1

# These now delegate to actions/_paths.py, which resolves the real,
# possibly OneDrive-redirected folder via the Windows Shell instead of
# blindly assuming Path.home() / "Desktop". Kept as thin wrappers so
# every existing call site below is unchanged.
def _get_desktop() -> Path:
    return get_special_folder("desktop")

def _get_downloads() -> Path:
    return get_special_folder("downloads")

def _get_documents() -> Path:
    return get_special_folder("documents")

def _get_pictures() -> Path:
    return get_special_folder("pictures")

def _get_music() -> Path:
    return get_special_folder("music")

def _get_videos() -> Path:
    return get_special_folder("videos")


_SEARCH_ROOTS = ("desktop", "documents", "downloads", "pictures", "music", "videos", "home")


def _smart_locate(name: str, hint_path: str) -> tuple[Path | None, list[Path]]:
    """
    Resolve a bare filename (no folder given, or the caller's default
    'desktop' guess) to the file the user actually means.

    Search order (stops at the first hit):
      1. A pronoun/reference ("it", "that file", "last file", ...) ->
         the most recently touched file in action memory.
      2. The explicitly given hint_path, if the file exists there.
      3. Recently touched files in action memory (exact filename match).
      4. Desktop, Documents, Downloads, Pictures, Music, Videos, Home
         (first level only, to stay fast).

    Returns (single_match, all_matches). If more than one folder has a
    file with this name, single_match is None and all_matches lists
    every candidate -- the caller should ask the user which one.
    """
    if not name:
        return None, []

    if action_memory.is_reference(name):
        resolved = action_memory.resolve_reference(name)
        return (resolved, [resolved]) if resolved and resolved.exists() else (None, [])

    direct = Path(name).expanduser()
    if direct.is_absolute() and direct.exists():
        return direct, [direct]

    hint_base = get_special_folder(hint_path) if hint_path in _SEARCH_ROOTS else (
        Path(hint_path).expanduser() if hint_path else None
    )
    if hint_base and (hint_base / name).exists():
        found = hint_base / name
        return found, [found]

    matches: list[Path] = []
    seen = set()

    for p in action_memory.recent_paths():
        if p.name == name and p.exists() and p not in seen:
            matches.append(p)
            seen.add(p)
    if matches:
        return matches[0], matches

    for key in _SEARCH_ROOTS:
        folder = get_special_folder(key) if key != "home" else Path.home()
        try:
            candidate = folder / name
            if candidate.exists() and candidate not in seen:
                matches.append(candidate)
                seen.add(candidate)
        except Exception:
            continue

    if len(matches) == 1:
        return matches[0], matches
    return (None, matches) if matches else (None, [])


def _resolve_path(raw: str) -> Path:
    shortcuts: dict[str, Path] = {
        "desktop":   _get_desktop(),
        "downloads": _get_downloads(),
        "documents": _get_documents(),
        "pictures":  _get_pictures(),
        "music":     _get_music(),
        "videos":    _get_videos(),
        "home":      Path.home(),
    }
    raw = raw.strip()
    lower = raw.lower()
    if lower in shortcuts:
        return shortcuts[lower]

    # Handle a shortcut combined with a sub-path in one string, e.g.
    # "desktop/Shortcuts" or "desktop\Shortcuts" — callers (including the
    # AI) sometimes fold path+name into a single argument instead of
    # passing them separately. Without this, such a string falls straight
    # through to Path(raw), which resolves relative to the process's
    # working directory and will almost never exist, even when the
    # target is clearly visible to the user.
    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if len(parts) > 1 and parts[0].lower() in shortcuts:
        return shortcuts[parts[0].lower()].joinpath(*parts[1:])

    return Path(raw).expanduser()

def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target.name}/"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    target = None
    try:
        base       = _resolve_path(path)
        safe_name  = _sanitize_filename(name) if name else name
        target     = (base / safe_name) if safe_name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target = _dedupe_path(target)
        write_target = _long_path(target)
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(content, encoding="utf-8")
        return f"File created: {target.name}"
    except PermissionError:
        loc = target.parent if target else path
        return f"Permission denied writing to {loc}. Try a different folder or run as administrator."
    except FileNotFoundError:
        return f"Could not create file: the path is invalid or too long ({target})."
    except OSError as e:
        return f"Could not create file: {e}"
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    target = None
    try:
        base      = _resolve_path(path)
        safe_name = _sanitize_filename(name) if name else name
        target    = (base / safe_name) if safe_name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        _long_path(target).mkdir(parents=True, exist_ok=True)
        return f"Folder created: {target.name}"
    except PermissionError:
        loc = target.parent if target else path
        return f"Permission denied creating folder in {loc}. Try a different location or run as administrator."
    except FileExistsError:
        return f"Folder already exists: {target.name if target else name}"
    except OSError as e:
        return f"Could not create folder: {e}"
    except Exception as e:
        return f"Could not create folder: {e}"


def delete_file(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        # Güvenli dizin kontrolü — kritik kullanıcı klasörlerini koru
        protected = {
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home()
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return f"Protected directory, cannot delete: {target.name}"

        return _safe_trash(target)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def move_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base   = _resolve_path(path)
        src    = (base / name) if name else base
        dst    = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        dst  = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        return f"Copied: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"
        if not new_name:
            return "No new name provided."

        new_path = target.parent / new_name
        if new_path.exists():
            return f"A file named '{new_name}' already exists here."

        target.rename(new_path)
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        return f"{action}: {target.name}"
    except Exception as e:
        return f"Could not write file: {e}"


def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Search path not found: {path}"

        results    = []
        dir_count  = 0
        max_dirs   = 500  # performans + güvenlik limiti

        for item in search_path.rglob("*"):
            if item.is_dir():
                dir_count += 1
                if dir_count > max_dirs:
                    break
                # Folders can be searched by name (but not by extension —
                # extension only makes sense for files). Without this,
                # "find/search" could never locate a folder, even one
                # sitting in plain sight, which is confusing when the
                # user says "find my Shortcuts folder".
                if extension:
                    continue
                if name and name.lower() not in item.name.lower():
                    continue
                results.append(f"📁 {item.name}/ — {item.parent}")
                if len(results) >= max_results:
                    break
                continue
            if not item.is_file():
                continue
            if extension and item.suffix.lower() != extension.lower():
                continue
            if name and name.lower() not in item.name.lower():
                continue
            size = _format_size(item.stat().st_size)
            results.append(f"📄 {item.name} ({size}) — {item.parent}")
            if len(results) >= max_results:
                break

        if not results:
            query = name or extension or "files"
            return f"No {query} found in {search_path.name}/"

        return f"Found {len(results)} item(s):\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {e}"


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []

    try:
        for item in desktop.iterdir():
            # Klasörlere, gizli dosyalara ve organize klasörlerine dokunma
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

def open_folder(path: str, name: str = "") -> str:
    try:
        base = _resolve_path(path)
        target = (base / name) if name else base

        if not target.exists():
            return f"Folder not found: {target}"

        if not target.is_dir():
            return f"Not a folder: {target}"

        if _OS == "Windows":
            os.startfile(str(target))
        elif _OS == "Darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

        return f"Opened folder: {target}"

    except Exception as e:
        return f"Could not open folder: {e}"

def compress_file(path: str, name: str = "", destination: str = "") -> str:
    """Zip a file or folder."""
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        if not src.exists():
            return f"Source not found: {src.name}"
        if not _is_safe_path(src):
            return f"Access denied: {src}"

        dst_base = _resolve_path(destination) if destination else src.parent
        archive_name = dst_base / (src.stem if src.is_dir() else src.name)
        result_path = shutil.make_archive(str(archive_name), "zip", root_dir=src if src.is_dir() else src.parent,
                                           base_dir=None if src.is_dir() else src.name)
        return f"Compressed: {Path(result_path).name}"
    except Exception as e:
        return f"Could not compress: {e}"


def extract_file(path: str, name: str = "", destination: str = "") -> str:
    """Unzip an archive."""
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        if not src.exists():
            return f"Archive not found: {src.name}"
        if not _is_safe_path(src):
            return f"Access denied: {src}"

        if destination:
            dst = _resolve_path(destination)
        else:
            dst = src.parent / src.stem
            if dst.exists() and not dst.is_dir():
                dst = src.parent / f"{src.stem}_extracted"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(src), str(dst))
        return f"Extracted: {src.name} -> {dst.name}/"
    except Exception as e:
        return f"Could not extract: {e}"


def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        player.write_log(f"[file] {action} {name or path}")

    # Actions that operate on a file that must already exist. For these,
    # a bare filename (or a reference like "it" / "that file" / "last
    # file") is resolved via action memory and, failing that, by
    # searching the common folders instead of assuming the caller's
    # 'path' guess (usually "desktop") is right.
    _NEEDS_EXISTING_FILE = {"delete", "move", "copy", "rename", "read", "info", "compress", "open"}
    resolved_note = None
    if action in _NEEDS_EXISTING_FILE and name:
        explicit_path = "path" in params
        if action_memory.is_reference(name) or not explicit_path:
            found, matches = _smart_locate(name, path)
            if found is not None:
                path, name = str(found.parent), found.name
                resolved_note = found
            elif len(matches) > 1:
                choices = "\n".join(f"- {m}" for m in matches[:5])
                return (
                    f"Multiple files named '{name}' found — which one did you mean?\n{choices}"
                )
            # else: no match anywhere; fall through and let the normal
            # per-op function report "not found" against the given path.

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            result = create_file(path, name=name, content=params.get("content", ""))
            if result.startswith("File created"):
                action_memory.record("create_file", _resolve_path(path) / name if name else _resolve_path(path))
            return result

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "delete":
            target = resolved_note or (_resolve_path(path) / name if name else _resolve_path(path))
            result = delete_file(path, name=name)
            if result.startswith("Moved to Trash"):
                action_memory.record("delete", target)
            return result

        elif action == "move":
            dest_arg = params.get("destination", "")
            src_for_memory = resolved_note or (_resolve_path(path) / name if name else _resolve_path(path))
            result = move_file(path, name=name, destination=dest_arg)
            if result.startswith("Moved:"):
                dst_base = _resolve_path(dest_arg) if dest_arg else src_for_memory.parent
                action_memory.record("move", (dst_base / src_for_memory.name) if dst_base.is_dir() else dst_base)
            return result

        elif action == "copy":
            dest_arg = params.get("destination", "")
            src_for_memory = resolved_note or (_resolve_path(path) / name if name else _resolve_path(path))
            result = copy_file(path, name=name, destination=dest_arg)
            if result.startswith("Copied:"):
                dst_base = _resolve_path(dest_arg) if dest_arg else src_for_memory.parent
                action_memory.record("copy", (dst_base / src_for_memory.name) if dst_base.is_dir() else dst_base)
            return result

        elif action == "rename":
            new_name = params.get("new_name", "")
            result = rename_file(path, name=name, new_name=new_name)
            if result.startswith("Renamed:"):
                action_memory.record("rename", _resolve_path(path) / new_name)
            return result

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            result = write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )
            if result.startswith("Written to") or result.startswith("Appended to"):
                action_memory.record("write", _resolve_path(path) / name if name else _resolve_path(path))
            return result

        elif action in ("find", "search"):
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 20)), 50),
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        elif action == "open":
            return open_folder(path, name=name)

        elif action == "compress":
            result = compress_file(path, name=name, destination=params.get("destination", ""))
            if result.startswith("Compressed:"):
                dst_base = _resolve_path(params.get("destination", "")) if params.get("destination") else (
                    resolved_note.parent if resolved_note else _resolve_path(path)
                )
                action_memory.record("compress", dst_base)
            return result

        elif action == "extract":
            return extract_file(path, name=name, destination=params.get("destination", ""))

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        return f"File controller error ({action}): {e}"