import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_plugin_loader_handles_missing_and_invalid_plugins(tmp_path):
    loader = importlib.import_module("core.plugin_loader")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()

    valid = plugin_dir / "good_plugin.py"
    valid.write_text(
        "def register():\n    return {'name': 'good_plugin'}\n",
        encoding="utf-8",
    )
    invalid = plugin_dir / "bad_plugin.py"
    invalid.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    result = loader.PluginLoader(plugin_dir).load_plugins()
    assert any(item["name"] == "good_plugin" for item in result)
    assert not any(item["name"] == "bad_plugin" for item in result)


def test_confirmation_and_undo_helpers():
    confirm_mod = importlib.import_module("core.confirm")
    undo_mod = importlib.import_module("core.undo")

    assert confirm_mod.requires_confirmation("delete file", {"confirmed": True}) is True
    assert confirm_mod.requires_confirmation("delete file", {"confirmed": "yes"}) is True
    assert confirm_mod.requires_confirmation("delete file", {}) is False

    stack = undo_mod.UndoStack()
    stack.push("demo", lambda: "done")
    assert stack.undo() == "done"


def test_audio_devices_and_background_monitor_are_safe():
    audio_mod = importlib.import_module("core.audio_devices")
    monitor_mod = importlib.import_module("actions.background_monitor")

    devices = audio_mod.get_audio_devices()
    assert isinstance(devices, list)

    monitor = monitor_mod.BackgroundMonitor(interval_seconds=1, cooldown_seconds=0)
    assert monitor.should_check() is True
    assert isinstance(monitor.build_status(), dict)
