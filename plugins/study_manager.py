"""
plugins/study_manager.py — JARVIS autonomous Study Manager.

What it does:
- Accepts a student's end goal (exam, certification, subject, project, etc.).
- Runs research in a daemon background worker so the main JARVIS conversation stays responsive.
- Uses JARVIS's existing web-research pipeline (Gemini grounded search + DDG fallback).
- Uses Gemini to turn the research into a structured day-wise study plan.
- Persists the plan in memory/study_plans.json.
- Automatically adds the plan's daily tasks to the existing todo_list plugin.
- Reconciles the plan whenever the plugin is used: missed Mon-Sat study tasks are moved
  to the next Sunday as catch-up work.
- Keeps one task per study item and never duplicates tasks for the same plan/day.
- Supports status, today's plan, full plan, progress, pause/resume, and rebuild.

This is deliberately a drop-in plugin: core/plugin_loader.py does not need changes.
Long research/planning work is always backgrounded.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional


PLUGIN = {
    "name": "study_manager",
    "description": (
        "Autonomous study planner and tracker. When the user gives an end goal such as "
        "'I want to crack JEE Main', 'learn Python for an exam', or 'prepare for a certification', "
        "research the goal/topic, build a day-wise plan, save it, and automatically add the daily "
        "study tasks to the existing To-Do List. Long research/planning runs in the background. "
        "Use actions: CREATE_PLAN, START_PLAN, TODAY, STATUS, SHOW_PLAN, PROGRESS, "
        "PAUSE_PLAN, RESUME_PLAN, REBUILD_PLAN. If the user asks to create a plan from a goal, "
        "call this plugin instead of manually inventing a schedule."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "CREATE_PLAN | START_PLAN | TODAY | STATUS | SHOW_PLAN | PROGRESS | "
                    "PAUSE_PLAN | RESUME_PLAN | REBUILD_PLAN"
                ),
            },
            "goal": {
                "type": "STRING",
                "description": "The student's end goal, exam, subject, certification, or learning objective.",
            },
            "duration_days": {
                "type": "NUMBER",
                "description": "Optional planning horizon in days. If omitted, Gemini infers a sensible horizon from the goal.",
            },
            "daily_hours": {
                "type": "NUMBER",
                "description": "Optional target study hours per day. Default is 3.",
            },
            "start_date": {
                "type": "STRING",
                "description": "Optional ISO start date YYYY-MM-DD. Defaults to today.",
            },
            "plan_id": {
                "type": "STRING",
                "description": "Optional plan ID. If omitted, the active plan is used.",
            },
        },
        "required": ["action"],
    },
}


_LOCK = threading.RLock()
_WORKER: dict[str, Any] = {
    "state": "idle",
    "job_id": None,
    "message": "",
    "error": "",
    "started_at": None,
    "finished_at": None,
}
_MAX_DURATION_DAYS = 365
_DEFAULT_DAILY_HOURS = 3.0
_RESEARCH_QUERIES = 4


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _storage_path() -> Path:
    override = os.environ.get("STUDY_MANAGER_STORAGE_PATH")
    if override:
        return Path(override).expanduser()
    return _project_root() / "memory" / "study_plans.json"


def _load() -> dict[str, Any]:
    path = _storage_path()
    if not path.exists():
        return {"active_plan_id": None, "plans": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("active_plan_id", None)
        data.setdefault("plans", [])
        return data
    except Exception:
        return {"active_plan_id": None, "plans": []}


def _save(data: dict[str, Any]) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _log(player, message: str) -> None:
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {message}")
    except Exception:
        pass


def _notify(player, message: str) -> None:
    _log(player, message)
    try:
        speak = getattr(player, "speak", None)
        if callable(speak):
            speak(message)
    except Exception:
        pass


def _today() -> date:
    return date.today()


def _parse_date(value: Optional[str], fallback: Optional[date] = None) -> date:
    if not value:
        return fallback or _today()
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return fallback or _today()


def _clamp_days(value: Any) -> Optional[int]:
    if value in (None, "", 0):
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(1, min(_MAX_DURATION_DAYS, n))


def _clamp_hours(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = _DEFAULT_DAILY_HOURS
    return max(0.5, min(12.0, n))


def _active_plan(data: dict[str, Any], plan_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    wanted = plan_id or data.get("active_plan_id")
    if wanted:
        for plan in data["plans"]:
            if plan.get("id") == wanted:
                return plan
    return data["plans"][-1] if data["plans"] else None


def _task_key(plan_id: str, day: str, item_id: str) -> str:
    return f"study::{plan_id}::{day}::{item_id}"


def _clean_title(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:180] or fallback


def _safe_json(text: str) -> dict[str, Any]:
    """Extract a JSON object even if Gemini wrapped it in markdown."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("Research planner returned no JSON object.")
    return json.loads(match.group(0))


def _research_goal(goal: str, player=None) -> str:
    """Research the goal using the existing JARVIS web-search stack."""
    from actions.web_search import _search

    queries = [
        f"{goal} official syllabus topics curriculum exam objectives",
        f"{goal} latest syllabus important chapters topics weightage",
        f"{goal} recommended preparation sequence prerequisites",
        f"{goal} common mistakes revision practice strategy",
    ]
    results: list[str] = []
    for i, query in enumerate(queries, 1):
        _log(player, f"Study Manager: researching source set {i}/{len(queries)}…")
        try:
            results.append(_search(query))
        except Exception as exc:
            results.append(f"Research query failed: {exc}")
    return "\n\n".join(results)


def _build_plan(goal: str, research: str, duration_days: Optional[int],
                daily_hours: float, start_date: date) -> dict[str, Any]:
    from google import genai

    api_config = _project_root() / "config" / "api_keys.json"
    data = json.loads(api_config.read_text(encoding="utf-8"))
    api_key = data.get("gemini_api_key")
    if not api_key:
        raise RuntimeError("Gemini API key is not configured.")

    client = genai.Client(api_key=api_key)

    duration_hint = (
        f"Use exactly {duration_days} calendar days."
        if duration_days
        else "Infer a realistic calendar duration from the goal; prefer 30–180 days for substantial exams."
    )

    prompt = f"""
You are the academic planning engine inside a study assistant.

STUDENT END GOAL:
{goal}

TARGET START DATE:
{start_date.isoformat()}

DAILY STUDY CAPACITY:
{daily_hours} hours/day

DURATION:
{duration_hint}

RESEARCH MATERIAL:
{research[:45000]}

Create a realistic, evidence-informed day-wise study plan. Do not invent an official syllabus
when the research does not support it. Prioritize foundational prerequisites before advanced
topics. Include active recall, problem practice, revision, and periodic tests. Keep Sunday lighter
and use it as a catch-up/revision day.

Return ONLY valid JSON with this exact top-level structure:
{{
  "title": "short plan title",
  "goal_summary": "one sentence",
  "duration_days": 0,
  "weekly_strategy": "short paragraph",
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "day_number": 1,
      "theme": "short theme",
      "tasks": [
        {{
          "id": "d1t1",
          "title": "specific study task",
          "minutes": 60,
          "type": "learn|practice|revision|test|catch_up",
          "priority": "high|medium|low"
        }}
      ]
    }}
  ]
}}

Rules:
- duration_days must equal the number of entries in days.
- Every date must be consecutive starting at {start_date.isoformat()}.
- Total planned minutes for a normal day should be <= {int(daily_hours * 60)}.
- Sunday should generally be <= 70% of the normal daily study load.
- Every task must be concrete enough to become a To-Do item.
- Do not put motivational filler into task titles.
- Include at least one revision/practice component most days.
- Do not schedule tasks after the inferred/explicit duration.
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    raw = ""
    try:
        for part in response.candidates[0].content.parts:
            if getattr(part, "text", None):
                raw += part.text
    except Exception:
        raw = getattr(response, "text", "") or ""

    plan = _safe_json(raw)
    days = plan.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("Planner returned an empty or invalid day list.")

    # Normalize and validate dates/tasks before saving.
    normalized_days = []
    expected = start_date
    for idx, day in enumerate(days, 1):
        if not isinstance(day, dict):
            continue
        d = _parse_date(day.get("date"), expected)
        if idx == 1:
            expected = d
        else:
            d = expected
        tasks = []
        for ti, task in enumerate(day.get("tasks", []), 1):
            if not isinstance(task, dict):
                continue
            title = _clean_title(task.get("title"), f"Study task {ti}")
            try:
                minutes = max(10, min(720, int(float(task.get("minutes", 30)))))
            except (TypeError, ValueError):
                minutes = 30
            tasks.append({
                "id": str(task.get("id") or f"d{idx}t{ti}"),
                "title": title,
                "minutes": minutes,
                "type": str(task.get("type") or "learn"),
                "priority": str(task.get("priority") or "medium"),
            })
        if tasks:
            normalized_days.append({
                "date": d.isoformat(),
                "day_number": idx,
                "theme": _clean_title(day.get("theme"), "Study"),
                "tasks": tasks,
            })
        expected = d + timedelta(days=1)

    if not normalized_days:
        raise ValueError("Planner produced no usable study tasks.")

    return {
        "title": _clean_title(plan.get("title"), f"Study plan: {goal}"),
        "goal": goal,
        "goal_summary": _clean_title(plan.get("goal_summary"), goal),
        "duration_days": len(normalized_days),
        "daily_hours": daily_hours,
        "weekly_strategy": str(plan.get("weekly_strategy") or ""),
        "start_date": normalized_days[0]["date"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "active",
        "version": 1,
        "days": normalized_days,
    }


def _todo_add(description: str, due: str) -> Optional[dict[str, Any]]:
    from plugins.todo_list import add_task
    try:
        return add_task(description, due)
    except Exception:
        return None


def _todo_tasks(include_completed: bool = True) -> list[dict[str, Any]]:
    from plugins.todo_list import list_tasks
    try:
        return list_tasks(include_completed=include_completed)
    except Exception:
        return []


def _ensure_plan_tasks(plan: dict[str, Any]) -> int:
    """
    Add missing plan tasks to the existing To-Do List.
    We identify our own tasks by the stable '[STUDY:<id>]' marker.
    """
    existing = _todo_tasks(True)
    existing_descriptions = {str(t.get("description", "")) for t in existing}
    added = 0

    for day in plan.get("days", []):
        day_date = day.get("date")
        for item in day.get("tasks", []):
            marker = _task_key(plan["id"], day_date, item["id"])
            desc = f"[STUDY:{marker}] {day.get('theme', 'Study')}: {item['title']}"
            if desc in existing_descriptions:
                continue
            task = _todo_add(desc, day_date)
            if task:
                existing_descriptions.add(desc)
                added += 1
    return added


def _find_study_todo(task_desc: str, plan_id: str) -> bool:
    return task_desc.startswith(f"[STUDY:study::{plan_id}::")


def _reconcile_missed_tasks(plan: dict[str, Any], today: Optional[date] = None) -> int:
    """
    Any incomplete Mon-Sat study task whose due date is before today is moved
    to the next Sunday. We update the To-Do item in place when possible.

    Existing todo_list.py does not currently expose an edit_due API, so this
    function uses its public delete/add APIs. The task text remains unchanged
    and receives the new Sunday due date.
    """
    from plugins.todo_list import delete_task

    today = today or _today()
    next_sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    # If today is Sunday, use today rather than next Sunday.
    if today.weekday() == 6:
        next_sunday = today

    pending = _todo_tasks(False)
    moved = 0

    for task in pending:
        desc = str(task.get("description", ""))
        if not _find_study_todo(desc, plan["id"]):
            continue

        due_raw = task.get("due")
        due = _parse_date(due_raw, None) if due_raw else None
        if due is None or due >= today:
            continue

        # Already a Sunday task should not be moved again.
        if due.weekday() == 6:
            continue

        # Preserve original task, but change its due date by delete/re-add.
        # todo_list's numeric resolver means "1" = first displayed task, not
        # the internal database ID. Therefore always resolve by our exact marker
        # description here to avoid ever deleting an unrelated task.
        ok, _ = delete_task(desc)
        if ok:
            if _todo_add(desc, next_sunday.isoformat()):
                moved += 1

    return moved


def _reconcile(plan: dict[str, Any]) -> tuple[int, int]:
    added = _ensure_plan_tasks(plan)
    moved = _reconcile_missed_tasks(plan)
    return added, moved


def _start_background(goal: str, duration_days: Optional[int], daily_hours: float,
                      start_date: date, player=None, rebuild: bool = False) -> str:
    with _LOCK:
        if _WORKER["state"] == "running":
            return "A study-plan research job is already running. Ask for study status."

        job_id = uuid.uuid4().hex[:10]
        _WORKER.update({
            "state": "running",
            "job_id": job_id,
            "message": f"Researching and building a plan for: {goal}",
            "error": "",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
        })

    def worker():
        try:
            research = _research_goal(goal, player)
            plan = _build_plan(goal, research, duration_days, daily_hours, start_date)
            plan["id"] = f"study-{uuid.uuid4().hex[:10]}"
            plan["research_summary"] = research[:12000]
            with _LOCK:
                data = _load()
                if rebuild:
                    old = _active_plan(data)
                    if old:
                        old["status"] = "archived"
                data["plans"].append(plan)
                data["active_plan_id"] = plan["id"]
                _save(data)

            added, moved = _reconcile(plan)

            with _LOCK:
                _WORKER.update({
                    "state": "completed",
                    "message": f"Plan ready: {plan['title']}. Added {added} study tasks.",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                })

            _notify(
                player,
                f"Study plan ready. I researched your goal, created {plan['duration_days']} days, "
                f"and added {added} study tasks to your To-Do List."
                + (f" I moved {moved} missed tasks to Sunday." if moved else ""),
            )
        except Exception as exc:
            with _LOCK:
                _WORKER.update({
                    "state": "failed",
                    "message": "Study plan creation failed.",
                    "error": str(exc),
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                })
            _notify(player, f"Study Manager couldn't finish the plan: {exc}")

    threading.Thread(target=worker, name="JarvisStudyManager", daemon=True).start()
    return (
        f"Understood. I'm researching '{goal}' and building your day-wise study plan in the "
        f"background. JARVIS stays responsive; I'll notify you when it's ready."
    )


def _today_view(plan: dict[str, Any]) -> str:
    today = _today().isoformat()
    for day in plan.get("days", []):
        if day.get("date") == today:
            lines = [f"Today's study plan — {day.get('theme', 'Study')}"]
            for i, item in enumerate(day.get("tasks", []), 1):
                lines.append(f"{i}. {item['title']} ({item['minutes']} min, {item['type']})")
            return "\n".join(lines)
    return "There is no scheduled study day for today in the active plan."


def _progress(plan: dict[str, Any]) -> str:
    tasks = _todo_tasks(True)
    total = done = 0
    for task in tasks:
        desc = str(task.get("description", ""))
        if not _find_study_todo(desc, plan["id"]):
            continue
        total += 1
        if task.get("completed"):
            done += 1
    percent = (done / total * 100) if total else 0
    return f"Study progress: {done}/{total} tasks completed ({percent:.0f}%)."


def _show_plan(plan: dict[str, Any]) -> str:
    lines = [f"{plan['title']} — {plan['duration_days']} days", plan["goal_summary"], ""]
    for day in plan["days"]:
        lines.append(f"Day {day['day_number']} — {day['date']} — {day['theme']}")
        for item in day["tasks"]:
            lines.append(f"  • {item['title']} ({item['minutes']} min)")
    return "\n".join(lines)



def _daily_sync_loop() -> None:
    """Keep the active plan synchronized once per calendar day while JARVIS runs."""
    while True:
        try:
            now = datetime.now()
            next_day = (now + timedelta(days=1)).replace(
                hour=0, minute=2, second=0, microsecond=0
            )
            time.sleep(max(30.0, (next_day - now).total_seconds()))
            data = _load()
            plan = _active_plan(data)
            if plan and plan.get("status") == "active":
                _reconcile(plan)
        except Exception:
            # The daily helper must never be able to kill JARVIS.
            time.sleep(60.0)


# Start only a daemon thread: it exits automatically when JARVIS exits.
threading.Thread(
    target=_daily_sync_loop,
    name="JarvisStudyDailySync",
    daemon=True,
).start()


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "").strip().upper()
    goal = str(parameters.get("goal") or "").strip()
    duration_days = _clamp_days(parameters.get("duration_days"))
    daily_hours = _clamp_hours(parameters.get("daily_hours"))
    start_date = _parse_date(parameters.get("start_date"), _today())
    plan_id = str(parameters.get("plan_id") or "").strip() or None

    try:
        if action in {"CREATE_PLAN", "START_PLAN"}:
            if not goal:
                return "Tell me your end goal first, for example: 'I want to crack JEE Main.'"
            return _start_background(
                goal, duration_days, daily_hours, start_date, player, rebuild=False
            )

        if action == "REBUILD_PLAN":
            if not goal:
                data = _load()
                current = _active_plan(data, plan_id)
                goal = current.get("goal", "") if current else ""
            if not goal:
                return "Tell me the goal you want me to rebuild the study plan for."
            return _start_background(
                goal, duration_days, daily_hours, start_date, player, rebuild=True
            )

        data = _load()
        plan = _active_plan(data, plan_id)

        if action == "STATUS":
            with _LOCK:
                worker = dict(_WORKER)
            if worker["state"] == "running":
                return worker["message"]
            if worker["state"] == "failed":
                return f"Study Manager failed: {worker['error']}"
            if not plan:
                return "No study plan is active yet. Give me your end goal and I'll research it."
            added, moved = _reconcile(plan)
            return (
                f"Active plan: {plan['title']}. "
                f"{plan['duration_days']} days. { _progress(plan) }"
                f" I synchronized {added} missing tasks"
                + (f" and moved {moved} missed tasks to Sunday." if moved else ".")
            )

        if not plan:
            return "No active study plan. Tell me your end goal and I'll build one."

        if action == "TODAY":
            _reconcile(plan)
            return _today_view(plan)

        if action == "PROGRESS":
            _reconcile(plan)
            return _progress(plan)

        if action == "SHOW_PLAN":
            return _show_plan(plan)

        if action == "PAUSE_PLAN":
            plan["status"] = "paused"
            _save(data)
            return f"Paused the study plan '{plan['title']}'."

        if action == "RESUME_PLAN":
            plan["status"] = "active"
            added, moved = _reconcile(plan)
            _save(data)
            return f"Resumed the study plan. Added {added} missing tasks." + (
                f" Moved {moved} missed tasks to Sunday." if moved else ""
            )

        return (
            "Unknown Study Manager action. Use CREATE_PLAN, TODAY, STATUS, SHOW_PLAN, "
            "PROGRESS, PAUSE_PLAN, RESUME_PLAN, or REBUILD_PLAN."
        )

    except Exception as exc:
        _log(player, f"Study Manager error: {exc}")
        return f"Study Manager failed: {exc}"
