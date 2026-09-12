"""
JARVIS File Manager Plugin

Examples:
    "JARVIS, organize my Downloads"
    "JARVIS, organize my Desktop"
    "JARVIS, organize my Documents"
    "JARVIS, preview my Downloads organization"
    "JARVIS, undo the last file organization"

The plugin safely organizes files inside a selected user folder.

It does NOT:
- touch Windows/system directories
- delete files
- overwrite existing files
- recursively reorganize meaningful existing folders
- modify files
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path


# ============================================================================
# PLUGIN METADATA
# ============================================================================

PLUGIN = {
    "name": "file_manager",
    "description": (
        "Organizes files in user folders such as Downloads, Desktop and "
        "Documents into sensible categories. Supports preview, organizing "
        "and undoing the last organization."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action to perform: organize, preview, undo, or status."
                ),
                "enum": [
                    "organize",
                    "preview",
                    "undo",
                    "status",
                ],
            },
            "location": {
                "type": "STRING",
                "description": (
                    "Folder to organize. Examples: Downloads, Desktop, "
                    "Documents, or a full folder path."
                ),
            },
        },
        "required": ["action"],
    },
    "run": None,
}


# ============================================================================
# CONFIGURATION
# ============================================================================

LOCK = threading.RLock()

# Undo information is stored in the user's home directory.
STATE_DIR = Path.home() / ".jarvis"

UNDO_FILE = STATE_DIR / "file_manager_undo.json"

# Maximum number of files processed in one operation.
# This prevents an accidental command from moving an enormous amount of data.
MAX_FILES_PER_OPERATION = 2000


# ============================================================================
# FILE CATEGORIES
# ============================================================================

EXTENSION_CATEGORIES: dict[str, str] = {

    # Documents
    ".doc": "Documents",
    ".docx": "Documents",
    ".odt": "Documents",
    ".rtf": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".tex": "Documents",

    # PDFs
    ".pdf": "PDFs",

    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".webp": "Images",
    ".svg": "Images",
    ".ico": "Images",
    ".tif": "Images",
    ".tiff": "Images",
    ".heic": "Images",

    # Videos
    ".mp4": "Videos",
    ".mkv": "Videos",
    ".avi": "Videos",
    ".mov": "Videos",
    ".wmv": "Videos",
    ".webm": "Videos",
    ".flv": "Videos",
    ".m4v": "Videos",

    # Audio
    ".mp3": "Audio",
    ".wav": "Audio",
    ".flac": "Audio",
    ".aac": "Audio",
    ".ogg": "Audio",
    ".m4a": "Audio",
    ".wma": "Audio",

    # Spreadsheets
    ".xls": "Spreadsheets",
    ".xlsx": "Spreadsheets",
    ".xlsm": "Spreadsheets",
    ".csv": "Spreadsheets",
    ".ods": "Spreadsheets",

    # Presentations
    ".ppt": "Presentations",
    ".pptx": "Presentations",
    ".odp": "Presentations",

    # Archives
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",

    # Installers
    ".exe": "Installers",
    ".msi": "Installers",
    ".msix": "Installers",
    ".appx": "Installers",

    # Code
    ".py": "Code",
    ".pyw": "Code",
    ".js": "Code",
    ".jsx": "Code",
    ".ts": "Code",
    ".tsx": "Code",
    ".java": "Code",
    ".c": "Code",
    ".h": "Code",
    ".cpp": "Code",
    ".hpp": "Code",
    ".cs": "Code",
    ".go": "Code",
    ".rs": "Code",
    ".php": "Code",
    ".rb": "Code",
    ".swift": "Code",
    ".kt": "Code",
    ".kts": "Code",
    ".html": "Code",
    ".htm": "Code",
    ".css": "Code",
    ".scss": "Code",
    ".json": "Code",
    ".xml": "Code",
    ".yaml": "Code",
    ".yml": "Code",
    ".toml": "Code",
    ".sql": "Code",
    ".sh": "Code",
    ".bat": "Code",
    ".cmd": "Code",
    ".ps1": "Code",

    # Fonts
    ".ttf": "Fonts",
    ".otf": "Fonts",
    ".woff": "Fonts",
    ".woff2": "Fonts",

    # Disk images
    ".iso": "Disk Images",
    ".img": "Disk Images",

    # Shortcuts
    ".lnk": "Shortcuts",
    ".url": "Shortcuts",
}


# ============================================================================
# WINDOWS USER LOCATIONS
# ============================================================================

def _user_locations() -> dict[str, Path]:
    """Return common user folders."""

    home = Path.home()

    return {
        "downloads": home / "Downloads",
        "download": home / "Downloads",

        "desktop": home / "Desktop",

        "documents": home / "Documents",
        "document": home / "Documents",

        "pictures": home / "Pictures",
        "picture": home / "Pictures",
        "photos": home / "Pictures",

        "videos": home / "Videos",
        "video": home / "Videos",

        "music": home / "Music",
        "audio": home / "Music",
    }


# ============================================================================
# LOGGING
# ============================================================================

def _log(
    player,
    message: str,
) -> None:

    try:

        if (
            player is not None
            and hasattr(player, "write_log")
        ):
            player.write_log(
                f"SYS: {message}"
            )
            return

    except Exception:
        pass

    try:
        print(
            f"[FileManager] {message}"
        )
    except Exception:
        pass


# ============================================================================
# LOCATION RESOLUTION
# ============================================================================

def _resolve_location(
    location: str | None,
) -> Path:

    locations = _user_locations()

    if not location:
        return locations["downloads"]

    cleaned = str(
        location
    ).strip().strip('"').strip("'")

    key = cleaned.lower()

    if key in locations:
        return locations[key]

    # Handle common natural-language phrases.
    aliases = {
        "my downloads": "downloads",
        "my download": "downloads",
        "my desktop": "desktop",
        "my documents": "documents",
        "my document": "documents",
        "my pictures": "pictures",
        "my photos": "pictures",
        "my videos": "videos",
        "my music": "music",
    }

    if key in aliases:
        return locations[
            aliases[key]
        ]

    # Otherwise treat it as a path.
    try:
        path = Path(
            os.path.expandvars(
                os.path.expanduser(
                    cleaned
                )
            )
        ).resolve()

        return path

    except Exception:
        return locations["downloads"]


# ============================================================================
# SAFETY
# ============================================================================

def _is_safe_location(
    path: Path,
) -> tuple[bool, str]:

    try:
        path = path.resolve()
    except Exception:
        return False, "The selected location is invalid."

    home = Path.home().resolve()

    # Only allow locations inside the user's home directory.
    try:
        path.relative_to(home)
    except ValueError:
        return (
            False,
            "For safety, I can only organize folders inside your "
            "Windows user folder.",
        )

    # Never operate directly on the user profile root.
    if path == home:
        return (
            False,
            "Please specify a folder such as Downloads or Desktop "
            "instead of the entire user profile.",
        )

    # Block obvious system/application locations.
    blocked_names = {
        "appdata",
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
    }

    for part in path.parts:

        if part.lower() in blocked_names:
            return (
                False,
                "That location is protected and cannot be organized.",
            )

    if not path.exists():
        return (
            False,
            f"The folder does not exist: {path}",
        )

    if not path.is_dir():
        return (
            False,
            "The selected location is not a folder.",
        )

    return True, ""


# ============================================================================
# CATEGORY
# ============================================================================

def _category_for_file(
    path: Path,
) -> str:

    extension = path.suffix.lower()

    return EXTENSION_CATEGORIES.get(
        extension,
        "Other",
    )


# ============================================================================
# SAFE DESTINATION
# ============================================================================

def _unique_destination(
    destination: Path,
) -> Path:

    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix

    counter = 2

    while True:

        candidate = destination.with_name(
            f"{stem} ({counter}){suffix}"
        )

        if not candidate.exists():
            return candidate

        counter += 1


# ============================================================================
# COLLECT FILES
# ============================================================================

def _collect_files(
    root: Path,
) -> list[Path]:

    files: list[Path] = []

    # Only inspect files directly inside the selected folder.
    #
    # This is intentional. Existing subfolders are treated as meaningful
    # organization and are left untouched.
    try:

        for item in root.iterdir():

            if not item.is_file():
                continue

            # Ignore hidden/system-like files.
            if item.name.startswith("."):
                continue

            files.append(item)

            if len(files) >= MAX_FILES_PER_OPERATION:
                break

    except PermissionError:
        return []

    return files


# ============================================================================
# BUILD PLAN
# ============================================================================

def _build_plan(
    root: Path,
) -> list[dict[str, str]]:

    files = _collect_files(root)

    plan: list[dict[str, str]] = []

    for source in files:

        category = _category_for_file(
            source
        )

        destination_dir = (
            root / category
        )

        destination = _unique_destination(
            destination_dir / source.name
        )

        plan.append(
            {
                "source": str(source),
                "destination": str(destination),
                "category": category,
            }
        )

    return plan


# ============================================================================
# PREVIEW
# ============================================================================

def _preview(
    player,
    location: str | None,
) -> str:

    root = _resolve_location(
        location
    )

    safe, reason = _is_safe_location(
        root
    )

    if not safe:
        return reason

    plan = _build_plan(
        root
    )

    if not plan:
        return (
            f"I found no loose files to organize in "
            f"{root.name}, sir. Existing folders were left untouched."
        )

    categories: dict[str, int] = {}

    for item in plan:

        category = item["category"]

        categories[category] = (
            categories.get(category, 0) + 1
        )

    parts = [
        f"I found {len(plan)} files to organize in {root}.",
    ]

    parts.append(
        "I would organize them as follows:"
    )

    for category, count in sorted(
        categories.items()
    ):

        parts.append(
            f"{category}: {count}"
        )

    parts.append(
        "No files will be deleted or overwritten."
    )

    return " ".join(parts)


# ============================================================================
# SAVE UNDO INFORMATION
# ============================================================================

def _save_undo(
    operations: list[dict[str, str]],
) -> None:

    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "created_at": datetime.now().isoformat(),
        "operations": operations,
    }

    UNDO_FILE.write_text(
        json.dumps(
            data,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================================
# ORGANIZE
# ============================================================================

def _organize(
    player,
    location: str | None,
) -> str:

    root = _resolve_location(
        location
    )

    safe, reason = _is_safe_location(
        root
    )

    if not safe:
        return reason

    with LOCK:

        plan = _build_plan(
            root
        )

        if not plan:
            return (
                f"There are no loose files to organize "
                f"in {root.name}, sir."
            )

        operations: list[
            dict[str, str]
        ] = []

        errors = 0

        for item in plan:

            source = Path(
                item["source"]
            )

            destination = Path(
                item["destination"]
            )

            try:

                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                # Re-check before moving.
                destination = _unique_destination(
                    destination
                )

                shutil.move(
                    str(source),
                    str(destination),
                )

                operations.append(
                    {
                        "source": str(source),
                        "destination": str(destination),
                    }
                )

            except Exception as exc:

                errors += 1

                _log(
                    player,
                    f"Could not move {source.name}: {exc}",
                )

        if operations:
            _save_undo(
                operations
            )

    if errors:

        return (
            f"I organized {len(operations)} files, sir, "
            f"but {errors} files could not be moved. "
            "The successful moves can be undone."
        )

    return (
        f"Done, sir. I organized {len(operations)} files "
        f"in {root.name}. I didn't delete or overwrite anything. "
        "You can say 'undo the last file organization' if you "
        "want me to reverse it."
    )


# ============================================================================
# UNDO
# ============================================================================

def _undo(
    player,
) -> str:

    if not UNDO_FILE.exists():

        return (
            "I don't have a previous file-organization operation "
            "that I can undo, sir."
        )

    with LOCK:

        try:

            data = json.loads(
                UNDO_FILE.read_text(
                    encoding="utf-8"
                )
            )

            operations = data.get(
                "operations",
                [],
            )

        except Exception:

            return (
                "The previous file-organization record is damaged, sir."
            )

        if not operations:

            return (
                "There are no file moves available to undo, sir."
            )

        restored = 0
        skipped = 0

        for operation in reversed(
            operations
        ):

            source = Path(
                operation["source"]
            )

            destination = Path(
                operation["destination"]
            )

            try:

                if not destination.exists():
                    skipped += 1
                    continue

                # Never overwrite something that appeared at the original
                # location after the organization.
                if source.exists():
                    skipped += 1
                    continue

                source.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                shutil.move(
                    str(destination),
                    str(source),
                )

                restored += 1

            except Exception as exc:

                skipped += 1

                _log(
                    player,
                    f"Could not restore {destination.name}: {exc}",
                )

        # Remove undo record after attempting the operation.
        try:
            UNDO_FILE.unlink()
        except Exception:
            pass

    if skipped:

        return (
            f"I restored {restored} files, sir. "
            f"{skipped} could not be restored because their "
            "original location was no longer safe or available."
        )

    return (
        f"Done, sir. I restored all {restored} files "
        "from the last organization."
    )


# ============================================================================
# STATUS
# ============================================================================

def _status(
    player,
) -> str:

    if not UNDO_FILE.exists():

        return (
            "File Manager is ready, sir. "
            "There is no previous organization waiting to be undone."
        )

    try:

        data = json.loads(
            UNDO_FILE.read_text(
                encoding="utf-8"
            )
        )

        operations = data.get(
            "operations",
            [],
        )

        created = data.get(
            "created_at",
            "unknown time",
        )

        return (
            f"File Manager is ready, sir. "
            f"The last organization contains "
            f"{len(operations)} file moves and can be undone. "
            f"It was created at {created}."
        )

    except Exception:

        return (
            "File Manager is ready, but the previous "
            "undo record could not be read."
        )


# ============================================================================
# PLUGIN ENTRY POINT
# ============================================================================

def run(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:

    del session_memory

    action = str(
        parameters.get(
            "action",
            "status",
        )
    ).strip().lower()

    location = parameters.get(
        "location"
    )

    if action == "preview":

        return _preview(
            player,
            location,
        )

    if action == "organize":

        return _organize(
            player,
            location,
        )

    if action == "undo":

        return _undo(
            player,
        )

    if action == "status":

        return _status(
            player,
        )

    return (
        "Unknown File Manager action. "
        "Use organize, preview, undo, or status."
    )


# ============================================================================
# REGISTER PLUGIN
# ============================================================================

PLUGIN["run"] = run