import json
from datetime import date
from pathlib import Path

import plugins.study_manager as sm


def test_storage_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "study_plans.json"
    monkeypatch.setenv("STUDY_MANAGER_STORAGE_PATH", str(path))
    data = {"active_plan_id": "study-1", "plans": [{"id": "study-1", "title": "Test", "days": []}]}
    sm._save(data)
    assert sm._load() == data


def test_safe_json():
    raw = '```json\n{"days": [{"date": "2026-09-14", "tasks": []}]}\n```'
    assert sm._safe_json(raw)["days"][0]["date"] == "2026-09-14"


def test_clamps():
    assert sm._clamp_days(9999) == 365
    assert sm._clamp_days(-2) == 1
    assert sm._clamp_hours(99) == 12
    assert sm._clamp_hours(0.1) == 0.5


def test_task_key_is_stable():
    assert sm._task_key("study-abc", "2026-09-14", "d1t1") == (
        "study::study-abc::2026-09-14::d1t1"
    )


def test_today_view():
    plan = {
        "title": "Physics",
        "duration_days": 2,
        "goal_summary": "Learn physics",
        "days": [
            {
                "date": date.today().isoformat(),
                "day_number": 1,
                "theme": "Kinematics",
                "tasks": [{"title": "Study motion", "minutes": 60, "type": "learn"}],
            }
        ],
    }
    out = sm._today_view(plan)
    assert "Today's study plan" in out
    assert "Study motion" in out
