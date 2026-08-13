"""Tests for push_notifications.send_push_to_business's own diagnostics - every reason
nothing went out (not just outright failures) now gets logged via log_client_error
(visible at /admin/client-errors), instead of a couple of these being entirely silent.
See push_notifications.py's own docstring for why: three very different situations
("Firebase was never configured", "this device never registered a token", "the push
itself failed") were previously indistinguishable from the app's side - no notification
arrives either way - which made a real bug (the AI-task-done push never firing) hard to
tell apart from a deployment/config issue."""
import database
import push_notifications
from conftest import register_and_login


def _last_error(business_id):
    errors = database.recent_client_errors(limit=20)
    for row in errors:
        # (id, business_id, message, stack, context, app_version, created_at)
        if row[1] == business_id:
            return row
    return None


def test_send_push_logs_when_firebase_is_not_configured(client, unique_email, monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    _, business_id = register_and_login(client, unique_email)

    push_notifications.send_push_to_business(business_id, "Title", "Body")

    row = _last_error(business_id)
    assert row is not None
    assert "Firebase isn't configured" in row[2]
    assert row[4] == "push-skipped"


def test_send_push_logs_when_push_disabled_in_settings(client, unique_email, monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"client_email": "x", "private_key": "y"}')
    _, business_id = register_and_login(client, unique_email)
    database.set_push_enabled(business_id, False)

    push_notifications.send_push_to_business(business_id, "Title", "Body")

    row = _last_error(business_id)
    assert row is not None
    assert "turned off in Settings" in row[2]
    assert row[4] == "push-skipped"


def test_send_push_logs_when_no_device_token_registered(client, unique_email, monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"client_email": "x", "private_key": "y"}')
    _, business_id = register_and_login(client, unique_email)

    push_notifications.send_push_to_business(business_id, "Title", "Body")

    row = _last_error(business_id)
    assert row is not None
    assert "no device has registered a push token" in row[2]
    assert row[4] == "push-skipped"
