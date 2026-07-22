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

import re
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


def _csrf_token_from_html(html):
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    return match.group(1) if match else None


def register_business_web(client, username, email, **overrides):
    """Web-form equivalent of register_business() above - posts to /register (not the
    JSON /api/register the mobile app uses), so the Flask test client's session cookie
    ends up logged in exactly like a browser would, for tests that need to exercise
    session+CSRF-protected web routes (e.g. Schedule) rather than the Bearer-token API.
    working_hours defaults to the day-by-day picker format (see booking.parse_working_
    hours) rather than register_business()'s free-text placeholder, so a real business
    day (Monday) is actually open for tests that need bookable slots."""
    get_response = client.get("/register")
    csrf_token = _csrf_token_from_html(get_response.get_data(as_text=True))

    form = {
        "csrf_token": csrf_token,
        "name": "Test Salon",
        "username": username,
        "password": "TestPassword123",
        "contact_email": email,
        "working_hours": "Monday: 09:00-18:00\nTuesday: 09:00-18:00\nWednesday: 09:00-18:00\n"
                          "Thursday: 09:00-18:00\nFriday: 09:00-18:00\nSaturday: closed\nSunday: closed",
        "price_list": "Haircut: 20 EUR",
        "faq": "",
        "accept_terms": "on",
    }
    form.update(overrides)
    return web_post(client, "/register", form)


def get_csrf_token(client, path):
    """Fetches a fresh CSRF token from any page's rendered HTML - CSRF tokens are
    session-scoped (not page-scoped), so a token pulled from any page works for a POST
    to any other page in the same session."""
    response = client.get(path)
    return _csrf_token_from_html(response.get_data(as_text=True))


def web_post(client, path, data, **kwargs):
    """POSTs to a session+CSRF-protected web route with a Referer header set - Flask-
    WTF's CSRF protection also checks Referer when WTF_CSRF_SSL_STRICT is on, so this
    keeps every web-route test consistent instead of each one needing to remember it."""
    kwargs.setdefault("headers", {})["Referer"] = f"http://localhost{path}"
    return client.post(path, data=data, follow_redirects=False, **kwargs)
