"""
tests/test_whatsapp_webhook.py — covers:

  1. _is_meta_test_event(): the phone_number_id-mismatch detector that
     stops Meta's Dashboard "Test" button payload from ever reaching
     handler.handle_incoming_message() (and therefore never attempts an
     outbound send that would 131030).
  2. The full /webhook POST endpoint: a real message dispatches to the
     handler in the background; a synthetic test payload does not.
  3. whatsapp_service/local_test.py's dry-run capture path.

None of this hits the network or a real Firestore project — the handler
call chain is exercised against the local-file todo storage backend.
"""
from __future__ import annotations

import importlib
import sys
import time

import pytest


# --------------------------------------------------------------------
# Common env setup: real config + local-file todo storage, all isolated
# per test via monkeypatch/tmp_path.
# --------------------------------------------------------------------

REAL_PHONE_NUMBER_ID = "999888777666555"
TEST_DASHBOARD_PHONE_NUMBER_ID = "123456123"  # Meta's fixed dummy value
AUTHORIZED_NUMBER = "15551234567"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", REAL_PHONE_NUMBER_ID)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "fake-verify-token")
    monkeypatch.setenv("AUTHORIZED_WHATSAPP_NUMBER", AUTHORIZED_NUMBER)
    monkeypatch.setenv("TODO_STORAGE_PATH", str(tmp_path / "todo_tasks.json"))
    monkeypatch.delenv("TODO_STORAGE_BACKEND", raising=False)

    for mod in list(sys.modules):
        if mod.startswith("whatsapp_service") or mod.startswith("plugins"):
            sys.modules.pop(mod, None)

    yield

    for mod in list(sys.modules):
        if mod.startswith("whatsapp_service") or mod.startswith("plugins"):
            sys.modules.pop(mod, None)


# --------------------------------------------------------------------
# _is_meta_test_event
# --------------------------------------------------------------------

def test_synthetic_dashboard_payload_is_detected_as_test_event(env):
    webhook = importlib.import_module("whatsapp_service.webhook")
    value = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "16505551111",
            "phone_number_id": TEST_DASHBOARD_PHONE_NUMBER_ID,
        },
        "contacts": [{"profile": {"name": "test user name"}, "wa_id": "16315551181"}],
        "messages": [{"from": "16315551181", "id": "wamid.TEST", "type": "text",
                       "text": {"body": "This is a test message"}}],
    }
    assert webhook._is_meta_test_event(value) is True


def test_real_message_is_not_detected_as_test_event(env):
    webhook = importlib.import_module("whatsapp_service.webhook")
    value = {
        "metadata": {"phone_number_id": REAL_PHONE_NUMBER_ID},
        "messages": [{"from": AUTHORIZED_NUMBER, "id": "wamid.REAL", "type": "text",
                       "text": {"body": "add buy milk"}}],
    }
    assert webhook._is_meta_test_event(value) is False


def test_missing_metadata_is_not_flagged_as_test_event(env):
    # Malformed/unexpected payload shape — must not crash, and must not
    # be misclassified either way; just falls through to "not a test event"
    # so it still gets a chance at normal dispatch/validation downstream.
    webhook = importlib.import_module("whatsapp_service.webhook")
    assert webhook._is_meta_test_event({}) is False


# --------------------------------------------------------------------
# Full /webhook POST endpoint
# --------------------------------------------------------------------

def _synthetic_meta_test_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "16505551111",
                        "phone_number_id": TEST_DASHBOARD_PHONE_NUMBER_ID,
                    },
                    "contacts": [{"profile": {"name": "test user name"}, "wa_id": "16315551181"}],
                    "messages": [{
                        "from": "16315551181",
                        "id": "wamid.ABGGFlCGg0cvAgo-sJQh43L5Pe4W",
                        "timestamp": "1603059201",
                        "text": {"body": "This is a test message"},
                        "type": "text",
                    }],
                },
            }],
        }],
    }


def _real_message_payload(text: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "real-waba-id",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15550009999",
                        "phone_number_id": REAL_PHONE_NUMBER_ID,
                    },
                    "contacts": [{"profile": {"name": "Me"}, "wa_id": AUTHORIZED_NUMBER}],
                    "messages": [{
                        "from": AUTHORIZED_NUMBER,
                        "id": "wamid.REALMSG",
                        "timestamp": "1700000000",
                        "text": {"body": text},
                        "type": "text",
                    }],
                },
            }],
        }],
    }


def test_synthetic_test_webhook_returns_200_and_never_calls_send(env, monkeypatch):
    webhook = importlib.import_module("whatsapp_service.webhook")
    whatsapp_client = importlib.import_module("whatsapp_service.whatsapp_client")

    send_calls = []
    monkeypatch.setattr(whatsapp_client, "send_text_message",
                         lambda to, body: send_calls.append((to, body)) or True)
    # handler.py already imported whatsapp_client at module load time —
    # patch its reference too so the substitution actually takes effect.
    handler = importlib.import_module("whatsapp_service.handler")
    monkeypatch.setattr(handler, "whatsapp_client", whatsapp_client)

    from fastapi.testclient import TestClient
    client = TestClient(webhook.app)

    resp = client.post("/webhook", json=_synthetic_meta_test_payload())
    assert resp.status_code == 200
    time.sleep(0.05)  # background task runs after the response in real ASGI servers
    assert send_calls == []  # the whole point: no 131030-triggering send attempt


def test_real_message_webhook_dispatches_and_sends_reply(env, monkeypatch):
    webhook = importlib.import_module("whatsapp_service.webhook")
    whatsapp_client = importlib.import_module("whatsapp_service.whatsapp_client")
    handler = importlib.import_module("whatsapp_service.handler")

    send_calls = []
    monkeypatch.setattr(handler, "whatsapp_client", whatsapp_client)
    monkeypatch.setattr(whatsapp_client, "send_text_message",
                         lambda to, body: send_calls.append((to, body)) or True)

    from fastapi.testclient import TestClient
    client = TestClient(webhook.app)

    resp = client.post("/webhook", json=_real_message_payload("add buy milk"))
    assert resp.status_code == 200
    time.sleep(0.05)
    assert len(send_calls) == 1
    to, body = send_calls[0]
    assert to == AUTHORIZED_NUMBER
    assert "buy milk" in body.lower()


def test_health_endpoint_reports_configured(env):
    webhook = importlib.import_module("whatsapp_service.webhook")
    from fastapi.testclient import TestClient
    client = TestClient(webhook.app)
    resp = client.get("/health")
    assert resp.json() == {"status": "ok", "configured": True, "missing_env_vars": []}


# --------------------------------------------------------------------
# local_test.py dry-run path
# --------------------------------------------------------------------

def test_local_test_dry_run_captures_reply_without_network_call(env, monkeypatch, capsys):
    local_test = importlib.import_module("whatsapp_service.local_test")

    calls = []

    def _guard(to, body):
        calls.append((to, body))
        raise AssertionError("real send_text_message must not be called in dry-run mode")

    monkeypatch.setattr(
        importlib.import_module("whatsapp_service.whatsapp_client"),
        "send_text_message",
        _guard,
    )

    local_test._run_one("add buy milk", send=False)
    out = capsys.readouterr().out
    assert "buy milk" in out.lower()
    assert calls == []  # never touched the real (guarded) sender
