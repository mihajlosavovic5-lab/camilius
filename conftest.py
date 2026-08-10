"""Test environment setup - must run before `app` (and its import chain: database,
send_email, generate_reply, extensions) is imported anywhere, since
several of those read required env vars at MODULE LOAD time and raise immediately if
missing (FLASK_SECRET_KEY, DATABASE_URL, ENCRYPTION_KEY, OPENAI_API_KEY). CI provides a
real Postgres service container for DATABASE_URL - see .github/workflows/tests.yml; run
locally against any reachable Postgres by exporting the same env vars yourself first
(switching the DB driver from psycopg2 to pg8000 - a pure-Python driver with no native
DLL - is what makes that possible on a machine where a Windows Application Control
policy blocks psycopg2's, see the deploy-railway-state memory). The rest are throwaway
test-only placeholders, never real secrets, generated fresh for this purpose.
"""
import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
# Valid Fernet-format key (encryption.py's `Fernet(_key)` raises at import time otherwise).
os.environ.setdefault("ENCRYPTION_KEY", "lq6DN50PbDefktBQDzopjb_x9c2Ry7lxHtn3o3q69dU=")
# openai.OpenAI() validates this is non-empty at construction time (generate_reply.py,
# module load) - never actually called by this suite, no real API calls are made.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-not-real")
os.environ.setdefault("FLASK_DEBUG", "true")  # test client uses plain http - avoids SESSION_COOKIE_SECURE dropping the session cookie

import re
import uuid
from datetime import date, timedelta

import pytest

import app as app_module
import database
import reminders as reminders_module
from extensions import limiter


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _stub_send_email(monkeypatch):
    # Registration (and other flows) sends a real verification/notification email via
    # Resend on every call - without this, any test that registers a business makes a
    # live network call and fails outside of a valid RESEND_API_KEY. Individual tests
    # that care about what was "sent" re-monkeypatch these with their own recording stub.
    monkeypatch.setattr(app_module, "send_email", lambda **kwargs: None)
    monkeypatch.setattr(reminders_module, "send_email", lambda **kwargs: None)


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
def future_date():
    """ISO date string (YYYY-MM-DD) for a weekday safely in the future, no matter when
    the suite runs. Schedule tests used to hardcode a specific calendar date instead -
    fine while it was still in the future, but once that date became "today" its fixed
    booking times (09:00, 10:00, ...) started falling in the past for whatever time of
    day CI happened to run at, and every booking was rejected as unbookable. Picking a
    date 7+ days out sidesteps that permanently. The test businesses' working hours are
    open Monday-Friday, so any weekday works."""
    d = date.today() + timedelta(days=7)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d += timedelta(days=1)
    return d.isoformat()


def register_business(client, email, **overrides):
    """Shared helper for tests that need a real registered business - posts to the same
    /api/register endpoint the mobile app uses, so tests exercise the real code path
    rather than inserting rows directly.

    Since email confirmation is now required before login (see api_login()'s
    email_verified check), this also marks the new account verified - exactly what
    clicking the confirmation link would do - so callers that log in right after
    registering don't need to know about that step themselves."""
    payload = {
        "name": "Test Salon",
        "password": "TestPassword123",
        "contact_email": email,
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": True,
    }
    payload.update(overrides)
    response = client.post("/api/register", json=payload)
    if response.status_code == 200:
        database.mark_email_verified(response.get_json()["business_id"])
    return response


def register_and_login(client, email, password="TestPassword123", **overrides):
    """register_business() no longer hands back a usable bearer token directly - the
    account must confirm its email first (see register_business's own docstring above).
    This does both steps and returns (token, business_id), for tests that just need a
    working bearer token for a fresh business rather than exercising the registration
    response itself."""
    reg = register_business(client, email, password=password, **overrides)
    business_id = reg.get_json()["business_id"]
    login = client.post("/api/login", json={"email": email, "password": password})
    return login.get_json()["token"], business_id


def _csrf_token_from_html(html):
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    return match.group(1) if match else None


def register_business_web(client, email, **overrides):
    """Web-form equivalent of register_business() above - posts to /register (not the
    JSON /api/register the mobile app uses), so the Flask test client's session cookie
    ends up logged in exactly like a browser would, for tests that need to exercise
    session+CSRF-protected web routes (e.g. Schedule) rather than the Bearer-token API.
    working_hours defaults to the day-by-day picker format (see booking.parse_working_
    hours) rather than register_business()'s free-text placeholder, so a real business
    day (Monday) is actually open for tests that need bookable slots.

    Registering alone no longer logs the session in (it redirects to /verify-pending
    until the confirmation link is clicked - see register()/verify_email() in app.py),
    so on a successful registration this also marks the account verified and completes
    the same session["business_id"]/session["business_name"] login that clicking the
    email link would, for callers that need an already-logged-in web session."""
    get_response = client.get("/register")
    csrf_token = _csrf_token_from_html(get_response.get_data(as_text=True))

    form = {
        "csrf_token": csrf_token,
        "name": "Test Salon",
        "password": "TestPassword123",
        "confirm_password": "TestPassword123",
        "contact_email": email,
        "working_hours": "Monday: 09:00-18:00\nTuesday: 09:00-18:00\nWednesday: 09:00-18:00\n"
                          "Thursday: 09:00-18:00\nFriday: 09:00-18:00\nSaturday: closed\nSunday: closed",
        "price_list": "Haircut: 20 EUR",
        "faq": "",
        "accept_terms": "on",
    }
    form.update(overrides)
    response = web_post(client, "/register", form)
    if response.status_code == 302:
        business = database.business_by_contact_email(email)
        database.mark_email_verified(business[0])
        with client.session_transaction() as session:
            session["business_id"] = business[0]
            session["business_name"] = business[1]
            session.pop("pending_verify_business_id", None)
    return response


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
