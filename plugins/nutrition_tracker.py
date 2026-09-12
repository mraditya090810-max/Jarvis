
"""
JARVIS Nutrition & Meal Tracker Plugin

A simple, non-restrictive food and hydration tracker.

Supported actions:
- log_meal
- log_water
- meals
- water
- summary
- clear

Examples:
    "Log breakfast: eggs, toast and fruit"
    "Log 500 ml of water"
    "What have I eaten today?"
    "How much water have I logged?"
    "Give me today's nutrition summary"
"""

from __future__ import annotations

from datetime import datetime
from threading import RLock


_LOCK = RLock()

_MEALS: list[dict] = []
_WATER_ML = 0


PLUGIN = {
    "name": "nutrition_tracker",
    "description": (
        "A general nutrition and hydration tracker. Log meals, foods, water, "
        "and review a simple daily summary. Do not use it for calorie limits, "
        "weight loss targets, or restrictive eating."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "log_meal | log_water | meals | water | summary | clear"
                ),
            },
            "food": {
                "type": "STRING",
                "description": "Food or meal description to record.",
            },
            "water_ml": {
                "type": "NUMBER",
                "description": "Amount of water consumed in millilitres.",
            },
        },
        "required": ["action"],
    },
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _time() -> str:
    return datetime.now().strftime("%H:%M")


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _WATER_ML

    try:
        action = str(
            parameters.get("action", "summary") or "summary"
        ).strip().lower()

        aliases = {
            "log_food": "log_meal",
            "add_meal": "log_meal",
            "eat": "log_meal",
            "add_water": "log_water",
            "drink": "log_water",
            "show_meals": "meals",
            "today": "summary",
            "water_status": "water",
            "reset": "clear",
        }

        action = aliases.get(action, action)

        if action == "log_meal":
            food = str(parameters.get("food", "") or "").strip()

            if not food:
                return "Please tell me what food or meal you want to log."

            with _LOCK:
                _MEALS.append({
                    "date": _today(),
                    "time": _time(),
                    "food": food,
                })

            return f"Logged your meal: {food}."

        if action == "log_water":
            try:
                amount = float(parameters.get("water_ml", 0))
            except (TypeError, ValueError):
                amount = 0

            if amount <= 0:
                return "Please provide a positive amount of water in millilitres."

            # Prevent accidental absurd values from corrupting the tracker.
            amount = min(amount, 10000)

            with _LOCK:
                _WATER_ML += amount
                total = _WATER_ML

            return (
                f"Logged {amount:g} ml of water. "
                f"Today's logged total is {total:g} ml."
            )

        if action == "meals":
            today = _today()

            with _LOCK:
                meals = [
                    meal for meal in _MEALS
                    if meal["date"] == today
                ]

            if not meals:
                return "You haven't logged any meals today."

            lines = ["Today's logged meals:"]

            for meal in meals:
                lines.append(
                    f"- {meal['time']}: {meal['food']}"
                )

            return "\n".join(lines)

        if action == "water":
            with _LOCK:
                total = _WATER_ML

            if total <= 0:
                return "No water has been logged yet today."

            return f"You have logged {total:g} ml of water today."

        if action == "summary":
            today = _today()

            with _LOCK:
                meals = [
                    meal for meal in _MEALS
                    if meal["date"] == today
                ]
                water = _WATER_ML

            meal_count = len(meals)

            if meal_count == 0 and water == 0:
                return "There is no nutrition or hydration data logged today."

            parts = [
                f"Today's nutrition summary: {meal_count} meal"
                f"{'' if meal_count == 1 else 's'} logged."
            ]

            if water > 0:
                parts.append(
                    f"Water logged: {water:g} ml."
                )

            if meals:
                foods = ", ".join(
                    meal["food"] for meal in meals
                )
                parts.append(f"Foods logged: {foods}.")

            return " ".join(parts)

        if action == "clear":
            with _LOCK:
                _MEALS.clear()
                _WATER_ML = 0

            return "Today's nutrition and hydration log has been cleared."

        return (
            "Unknown nutrition tracker action. Use "
            "log_meal, log_water, meals, water, summary, or clear."
        )

    except Exception as exc:
        return f"Nutrition tracker error: {exc}"

