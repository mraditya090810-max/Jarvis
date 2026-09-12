"""
tests/test_todo_storage.py — covers plugins/_todo_storage.py (both backends)
and plugins/todo_list.py's public API sitting on top of it.

The Firestore backend is tested against a small fake client (no network,
no real Firebase project needed) so these tests run fully offline.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


# --------------------------------------------------------------------
# File backend
# --------------------------------------------------------------------

@pytest.fixture()
def storage_module(monkeypatch, tmp_path):
    """Fresh import of _todo_storage with TODO_STORAGE_PATH pointed at a
    throwaway file, and the module-level firestore client singleton reset."""
    monkeypatch.setenv("TODO_STORAGE_PATH", str(tmp_path / "todo_tasks.json"))
    monkeypatch.delenv("TODO_STORAGE_BACKEND", raising=False)
    sys.modules.pop("plugins._todo_storage", None)
    mod = importlib.import_module("plugins._todo_storage")
    yield mod
    sys.modules.pop("plugins._todo_storage", None)


def test_file_backend_round_trip(storage_module):
    assert storage_module.load() == {"next_id": 1, "tasks": []}
    data = {"next_id": 2, "tasks": [{"id": 1, "description": "Test"}]}
    storage_module.save(data)
    assert storage_module.load() == data


def test_file_backend_survives_missing_file(storage_module, tmp_path):
    # Nothing written yet — should return the empty shape, not raise.
    assert storage_module.load() == {"next_id": 1, "tasks": []}


def test_file_backend_survives_corrupt_file(storage_module, tmp_path, monkeypatch):
    path = tmp_path / "todo_tasks.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("TODO_STORAGE_PATH", str(path))
    assert storage_module.load() == {"next_id": 1, "tasks": []}


def test_default_backend_is_file(storage_module):
    assert storage_module.backend_name() == "file"


# --------------------------------------------------------------------
# Firestore backend (mocked — no real network / project needed)
# --------------------------------------------------------------------

class _FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, store: dict, key: str):
        self._store = store
        self._key = key

    def get(self):
        return _FakeSnapshot(self._store.get(self._key))

    def set(self, data):
        self._store[self._key] = dict(data)


class _FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return _FakeDocRef(self._store, doc_id)


class _FakeFirestoreClient:
    def __init__(self):
        self._collections: dict[str, dict] = {}

    def collection(self, name: str):
        self._collections.setdefault(name, {})
        return _FakeCollection(self._collections[name])


@pytest.fixture()
def firestore_storage_module(monkeypatch):
    """Import _todo_storage with TODO_STORAGE_BACKEND=firestore, and stub
    out firebase_admin entirely so no real credentials or network are
    needed — _firestore_doc() gets a fake client instead."""
    monkeypatch.setenv("TODO_STORAGE_BACKEND", "firestore")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')

    fake_client = _FakeFirestoreClient()

    fake_firebase_admin = types.ModuleType("firebase_admin")
    fake_firebase_admin._apps = []

    def _initialize_app(cred):
        fake_firebase_admin._apps.append(object())

    fake_firebase_admin.initialize_app = _initialize_app

    fake_credentials = types.ModuleType("firebase_admin.credentials")
    fake_credentials.Certificate = lambda info: info

    fake_firestore = types.ModuleType("firebase_admin.firestore")
    fake_firestore.client = lambda: fake_client

    monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "firebase_admin.firestore", fake_firestore)

    sys.modules.pop("plugins._todo_storage", None)
    mod = importlib.import_module("plugins._todo_storage")
    yield mod, fake_client
    sys.modules.pop("plugins._todo_storage", None)


def test_firestore_backend_round_trip(firestore_storage_module):
    mod, fake_client = firestore_storage_module
    assert mod.backend_name() == "firestore"
    assert mod.load() == {"next_id": 1, "tasks": []}

    data = {"next_id": 3, "tasks": [{"id": 1, "description": "Buy milk"}]}
    mod.save(data)
    assert mod.load() == data
    # Actually landed in the fake "cloud" store, not a local file.
    assert fake_client._collections["jarvis"]["todo_tasks"] == data


def test_firestore_backend_missing_credentials_raises(monkeypatch):
    monkeypatch.setenv("TODO_STORAGE_BACKEND", "firestore")
    monkeypatch.delenv("FIREBASE_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_CREDENTIALS_PATH", raising=False)

    fake_firebase_admin = types.ModuleType("firebase_admin")
    fake_firebase_admin._apps = []
    fake_firebase_admin.initialize_app = lambda cred: None
    fake_credentials = types.ModuleType("firebase_admin.credentials")
    fake_credentials.Certificate = lambda info: info
    fake_firestore = types.ModuleType("firebase_admin.firestore")
    fake_firestore.client = lambda: _FakeFirestoreClient()
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "firebase_admin.firestore", fake_firestore)

    sys.modules.pop("plugins._todo_storage", None)
    mod = importlib.import_module("plugins._todo_storage")
    with pytest.raises(RuntimeError, match="FIREBASE_CREDENTIALS"):
        mod.load()
    sys.modules.pop("plugins._todo_storage", None)


# --------------------------------------------------------------------
# todo_list.py public API sitting on top of the storage module
# --------------------------------------------------------------------

@pytest.fixture()
def todo_list_module(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_STORAGE_PATH", str(tmp_path / "todo_tasks.json"))
    monkeypatch.delenv("TODO_STORAGE_BACKEND", raising=False)
    sys.modules.pop("plugins._todo_storage", None)
    sys.modules.pop("plugins.todo_list", None)
    mod = importlib.import_module("plugins.todo_list")
    yield mod
    sys.modules.pop("plugins.todo_list", None)
    sys.modules.pop("plugins._todo_storage", None)


def test_todo_list_add_and_list(todo_list_module):
    todo_list_module.add_task("buy milk")
    tasks = todo_list_module.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["description"] == "Buy milk"


def test_todo_list_complete_and_delete(todo_list_module):
    todo_list_module.add_task("walk the dog")
    ok, task = todo_list_module.complete_task("walk")
    assert ok and task["completed"] is True

    todo_list_module.add_task("wash the car")
    ok, task = todo_list_module.delete_task("wash")
    assert ok and task["description"] == "Wash the car"
    assert all(t["description"] != "Wash the car" for t in todo_list_module.list_tasks(include_completed=True))
