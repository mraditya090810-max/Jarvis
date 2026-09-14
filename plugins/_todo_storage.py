"""
plugins/_todo_storage.py — pluggable storage backend for the To-Do List.

plugins/todo_list.py used to read/write memory/todo_tasks.json directly.
That's fine for the desktop app alone, but it means a whatsapp_service
deployed to a separate cloud host has its own, different file — tasks
added from WhatsApp never show up in desktop JARVIS and vice versa unless
you manually sync the file yourself.

This module adds a second backend so both sides can share one store with
no extra syncing code anywhere: whichever backend is active, todo_list.py
just calls load()/save() and doesn't know or care where the data lives.

Selected via the TODO_STORAGE_BACKEND env var:

  "file" (default) — a local JSON file. Unchanged from the original
      behaviour; exactly what you want for a single machine.
      Path resolution order:
        1. TODO_STORAGE_PATH env var, if set.
        2. <project_root>/memory/todo_tasks.json

  "firestore" — a free Firebase Firestore database. Set this the same way
      on BOTH the desktop JARVIS machine and wherever whatsapp_service is
      deployed, pointing at the same Firebase project, and every task
      read/write goes to the one shared document — add from WhatsApp,
      see it next time desktop JARVIS asks; add from desktop, see it next
      time you ask WhatsApp.

      Needs ONE of:
        FIREBASE_CREDENTIALS_JSON — the full service-account key, as a
            single-line JSON string. This is the one to use on a cloud
            host's environment-variables dashboard (Render, Railway,
            Fly.io, ...) since most of those don't offer file uploads.
        FIREBASE_CREDENTIALS_PATH — path to the downloaded service-account
            key .json file. Handy for local/desktop use — just save the
            file somewhere (e.g. config/firebase_key.json) and point at it.

      Optional:
        FIRESTORE_COLLECTION (default "jarvis")
        FIRESTORE_DOCUMENT   (default "todo_tasks")

`firebase-admin` is only imported inside _firestore_doc(), lazily, so
nothing about the default "file" backend gains a new dependency — the
package only needs to be installed on whichever machine(s) actually use
TODO_STORAGE_BACKEND=firestore.

This module is prefixed with "_" so core/plugin_loader.py's plugin
discovery (which skips files starting with "_" in plugins/) never tries
to load it as a standalone plugin.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# Load the root JARVIS .env so this storage module works correctly
# even when it is imported directly by a standalone service.
try:
    from dotenv import load_dotenv

    _ROOT_DIR = Path(__file__).resolve().parent.parent
    _ROOT_ENV = _ROOT_DIR / ".env"

    if _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV, override=False)
except ImportError:
    pass


_LOCK = threading.Lock()
_firestore_client = None  # lazy singleton, one per process

_EMPTY: dict = {"next_id": 1, "tasks": []}


def _empty() -> dict:
    return {"next_id": 1, "tasks": []}


# --------------------------------------------------------------------
# Backend: local file (original behaviour)
# --------------------------------------------------------------------

def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _file_path() -> Path:
    override = os.environ.get("TODO_STORAGE_PATH")
    if override:
        return Path(override).expanduser()
    return _project_root() / "memory" / "todo_tasks.json"


def _file_load() -> dict:
    path = _file_path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("next_id", 1)
        data.setdefault("tasks", [])
        return data
    except Exception:
        # Corrupt/empty file — never crash the assistant over a bad JSON file.
        return _empty()


def _file_save(data: dict) -> None:
    path = _file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX and Windows


# --------------------------------------------------------------------
# Backend: Firebase Firestore (shared cloud store)
# --------------------------------------------------------------------

def _firestore_doc():
    global _firestore_client
    if _firestore_client is None:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
            cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH")
            if cred_json:
                cred = credentials.Certificate(json.loads(cred_json))
            elif cred_path:
                cred = credentials.Certificate(cred_path)
            else:
                raise RuntimeError(
                    "TODO_STORAGE_BACKEND=firestore needs FIREBASE_CREDENTIALS_JSON "
                    "or FIREBASE_CREDENTIALS_PATH set."
                )
            firebase_admin.initialize_app(cred)
        _firestore_client = firestore.client()

    collection = os.environ.get("FIRESTORE_COLLECTION", "jarvis")
    document = os.environ.get("FIRESTORE_DOCUMENT", "todo_tasks")
    return _firestore_client.collection(collection).document(document)


def _firestore_load() -> dict:
    snap = _firestore_doc().get()
    if not snap.exists:
        return _empty()
    data = snap.to_dict() or {}
    data.setdefault("next_id", 1)
    data.setdefault("tasks", [])
    return data


def _firestore_save(data: dict) -> None:
    _firestore_doc().set(data)


# --------------------------------------------------------------------
# Public API — used by plugins/todo_list.py
# --------------------------------------------------------------------

def backend_name() -> str:
    return os.environ.get("TODO_STORAGE_BACKEND", "file").strip().lower()


def load() -> dict:
    with _LOCK:
        if backend_name() == "firestore":
            return _firestore_load()
        return _file_load()


def save(data: dict) -> None:
    with _LOCK:
        if backend_name() == "firestore":
            _firestore_save(data)
        else:
            _file_save(data)
