"""
Offline tests for telegram_service — no network access, no real Telegram
bot token required. Covers: the sender allowlist, webhook secret-token
verification, and message routing between to-do commands and general Q&A.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from telegram_service import config as tg_config
from telegram_service import handler, security


# --------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------

def test_is_authorized_matches_configured_id(monkeypatch):
    monkeypatch.setattr(tg_config, "AUTHORIZED_TELEGRAM_USER_ID", "123456789")
    assert security.is_authorized(123456789) is True


def test_is_authorized_rejects_other_ids(monkeypatch):
    monkeypatch.setattr(tg_config, "AUTHORIZED_TELEGRAM_USER_ID", "123456789")
    assert security.is_authorized(999999999) is False


def test_is_authorized_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(tg_config, "AUTHORIZED_TELEGRAM_USER_ID", "")
    assert security.is_authorized(123456789) is False


# --------------------------------------------------------------------------
# Webhook secret-token verification
# --------------------------------------------------------------------------

def test_validate_secret_token_accepts_matching_value(monkeypatch):
    monkeypatch.setattr(tg_config, "TELEGRAM_WEBHOOK_SECRET", "my-secret")
    assert security.validate_secret_token("my-secret") is True


def test_validate_secret_token_rejects_wrong_value(monkeypatch):
    monkeypatch.setattr(tg_config, "TELEGRAM_WEBHOOK_SECRET", "my-secret")
    assert security.validate_secret_token("guessed-value") is False


def test_validate_secret_token_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(tg_config, "TELEGRAM_WEBHOOK_SECRET", "my-secret")
    assert security.validate_secret_token("") is False


def test_validate_secret_token_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(tg_config, "TELEGRAM_WEBHOOK_SECRET", "")
    assert security.validate_secret_token("anything") is False


# --------------------------------------------------------------------------
# Message routing (handler.generate_reply)
# --------------------------------------------------------------------------

@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_STORAGE_PATH", str(tmp_path / "tasks.json"))


def test_start_command_returns_help_text(isolated_storage):
    reply = handler.generate_reply("/start")
    assert reply == handler.HELP_TEXT


def test_add_task_command_is_routed_to_todo_handler(isolated_storage):
    reply = handler.generate_reply("add buy milk")
    assert "Added to your To-Do List" in reply
    assert "milk" in reply.lower()


def test_list_tasks_command_is_routed_to_todo_handler(isolated_storage):
    handler.generate_reply("add buy milk")
    reply = handler.generate_reply("what are my tasks")
    assert "milk" in reply.lower()


def test_unrecognized_message_is_routed_to_general_qa(isolated_storage, monkeypatch):
    monkeypatch.setattr(
        "telegram_service.qa.answer_question", lambda text: "The capital of France is Paris."
    )
    reply = handler.generate_reply("what is the capital of France")
    assert reply == "The capital of France is Paris."


def test_handle_incoming_message_ignores_unauthorized_sender(isolated_storage, monkeypatch):
    monkeypatch.setattr(tg_config, "AUTHORIZED_TELEGRAM_USER_ID", "111")
    sent = []
    monkeypatch.setattr(
        "telegram_service.telegram_client.send_text_message",
        lambda chat_id, text: sent.append((chat_id, text)),
    )
    handler.handle_incoming_message(user_id=999, chat_id=999, text="add buy milk")
    assert sent == []  # silently ignored, no reply sent


def test_handle_incoming_message_replies_for_authorized_sender(isolated_storage, monkeypatch):
    monkeypatch.setattr(tg_config, "AUTHORIZED_TELEGRAM_USER_ID", "111")
    sent = []
    monkeypatch.setattr(
        "telegram_service.telegram_client.send_text_message",
        lambda chat_id, text: sent.append((chat_id, text)),
    )
    handler.handle_incoming_message(user_id=111, chat_id=111, text="add buy milk")
    assert len(sent) == 1
    assert sent[0][0] == 111
    assert "Added to your To-Do List" in sent[0][1]
