import importlib


def test_browser_agent_uses_headless_chromium_and_no_real_window():
    mod = importlib.import_module("plugins.browser_agent")
    text = (mod.PLUGIN["description"] or "").lower()
    assert "headless" in text
    assert "chromium" in text
    assert "real chrome" in text or "does not open" in text
    assert "use browser_agent" in text or "browser agent" in text
    assert "hud" in text or "embedded" in text


def test_browser_control_defers_to_browser_agent_when_requested():
    import main

    text = "".join(
        d["description"] for d in main.TOOL_DECLARATIONS if d["name"] == "browser_control"
    ).lower()
    assert "when the user explicitly says browser_agent" in text or "browser_agent" in text
    assert "browser_agent" in text


def test_browser_agent_error_mentions_venv_mismatch_and_fix():
    import plugins.browser_agent as mod

    msg = mod.run({"task": "test task"}, player=None)
    lowered = msg.lower()
    assert "venv" in lowered or ".venv" in lowered
    assert "different python environment" in lowered or "same environment" in lowered or "different python interpreter" in lowered
