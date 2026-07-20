"""Test environment setup - must run before `app` (and its import chain: database,
send_email, generate_reply, instagram_api, extensions) is imported anywhere, since
several of those read required env vars at MODULE LOAD time and raise immediately if
missing (FLASK_SECRET_KEY, DATABASE_URL, ENCRYPTION_KEY, OPENAI_API_KEY). CI provides a
real Postgres service container for DATABASE_URL - see .github/workflows/tests.yml. The
rest are throwaway test-only placeholders, never real secrets, generated fresh for this
purpose (the local dev machine can't run these anyway - psycopg2's DLL is blocked by a
Windows Application Control policy here, see the deploy-railway-state memory - so this
suite only ever actually runs in CI).
"""
import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
# Valid Fernet-format key (encryption.py's `Fernet(_key)` raises at import time otherwise).
os.environ.setdefault("ENCRYPTION_KEY", "lq6DN50PbDefktBQDzopjb_x9c2Ry7lxHtn3o3q69dU=")
# openai.OpenAI() validates this is non-empty at construction time (generate_reply.py,
# module load) - never actually called by this suite, no real API calls are made.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-not-real")
os.environ.setdefault("INSTAGRAM_APP_SECRET", "test-instagram-app-secret")
os.environ.setdefault("FLASK_DEBUG", "true")  # test client uses plain http - avoids SESSION_COOKIE_SECURE dropping the session cookie

import uuid

import pytest

import app as app_module
from extensions import limiter


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # Flask-Limiter keys by remote address, which the test client always reports as the
    # same value - without this, a test hitting a "N per hour" route would inherit a
    # stale count left over from an earlier, unrelated test and fail with 429.
    limiter.reset()
    yield


@pytest.fixture()
def unique_email():
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture()
def unique_username():
    return f"test_{uuid.uuid4().hex[:10]}"


def register_business(client, username, email, **overrides):
    """Shared helper for tests that need a real registered business - posts to the same
    /api/register endpoint the mobile app uses, so tests exercise the real code path
    rather than inserting rows directly."""
    payload = {
        "name": "Test Salon",
        "username": username,
        "password": "TestPassword123",
        "contact_email": email,
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": True,
    }
    payload.update(overrides)
    return client.post("/api/register", json=payload)
