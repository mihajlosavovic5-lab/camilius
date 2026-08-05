"""Continue with Google" - covers the shared find-or-create logic (_google_login_or_register),
the web redirect/callback routes, and the mobile start/callback/status bridge. Real network
calls to Google are never made - google_oauth.fetch_user is monkeypatched everywhere a
callback needs a profile, exactly like _stub_send_email stands in for Resend elsewhere in
this suite.
"""
import uuid

import database
import app as app_module


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")


def _unique_sub():
    # A real Google `sub` is globally unique per Google account forever - fixed test
    # literals like "google-sub-1" would collide with rows a previous run of this same
    # test already committed to the DB (no per-test transaction rollback in this suite -
    # see conftest.py), which then hits the legitimate "already linked, log straight in"
    # branch instead of the "brand new" one a test meant to exercise.
    return f"gsub-{uuid.uuid4().hex}"


def _fake_profile(sub, email, name="Test Business"):
    return {"sub": sub, "email": email, "email_verified": True, "name": name}


# --- google_oauth.is_configured() gating ---

def test_auth_google_404s_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = client.get("/auth/google")
    assert response.status_code == 404


def test_login_page_hides_google_button_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    html = client.get("/login").get_data(as_text=True)
    assert "auth_google" not in html
    assert "/auth/google" not in html


def test_login_page_shows_google_button_when_configured(client, monkeypatch):
    _configure_google(monkeypatch)
    html = client.get("/login").get_data(as_text=True)
    assert "/auth/google" in html


# --- Web flow ---

def test_auth_google_redirects_to_google_and_sets_session_state(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.get("/auth/google")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    with client.session_transaction() as sess:
        assert sess.get("oauth_state")


def test_auth_google_callback_rejects_state_mismatch(client, monkeypatch):
    _configure_google(monkeypatch)
    client.get("/auth/google")  # seeds session["oauth_state"]
    response = client.get("/auth/google/callback?code=abc&state=wrong-state")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(app_module.url_for("login")) or "/login" in response.headers["Location"]


def test_auth_google_callback_creates_new_account_and_logs_in_web(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    start = client.get("/auth/google")
    state = start.headers["Location"].split("state=")[1].split("&")[0]

    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    assert "/onboarding" in response.headers["Location"] or response.headers["Location"].endswith("/")

    with client.session_transaction() as sess:
        business_id = sess.get("business_id")
    assert business_id is not None

    business = database.business_by_id(business_id)
    assert business[4] == unique_email  # contact_email
    assert business[12] is True  # email_verified - Google already proved it
    assert business[22] == sub  # google_id


def test_auth_google_callback_links_existing_account_by_email(client, monkeypatch, unique_username, unique_email):
    from conftest import register_business

    register_business(client, unique_username, unique_email)
    existing = database.business_by_contact_email(unique_email)
    assert existing[22] is None  # not linked yet

    _configure_google(monkeypatch)
    start = client.get("/auth/google")
    state = start.headers["Location"].split("state=")[1].split("&")[0]
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302

    linked = database.business_by_id(existing[0])
    assert linked[22] == sub
    assert linked[12] is True  # now verified via Google, even though the original signup wasn't


def test_auth_google_callback_reuses_existing_google_link(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    sub = _unique_sub()  # same Google account signs in twice - fixed for the whole test
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    start1 = client.get("/auth/google")
    state1 = start1.headers["Location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/google/callback?code=abc&state={state1}")
    with client.session_transaction() as sess:
        first_business_id = sess["business_id"]
        sess.clear()

    start2 = client.get("/auth/google")
    state2 = start2.headers["Location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/google/callback?code=abc&state={state2}")
    with client.session_transaction() as sess:
        second_business_id = sess["business_id"]

    assert first_business_id == second_business_id  # no duplicate account created


# --- Mobile bridge ---

def test_mobile_google_login_start_returns_auth_url_and_state(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.post("/api/google-login/start")
    assert response.status_code == 200
    data = response.get_json()
    assert data["auth_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert data["state"]


def test_mobile_google_login_start_404s_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = client.post("/api/google-login/start")
    assert response.status_code == 404


def test_mobile_google_login_status_is_pending_then_done(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    state = start["state"]

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "pending"

    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(_unique_sub(), unique_email))
    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert callback.status_code == 200  # renders the "you can close this tab" page, no redirect

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "done"
    assert status["token"]
    assert status["business_id"]

    # Single-use - a second poll finds nothing left.
    status_again = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status_again["status"] == "expired"


def test_mobile_google_login_status_unknown_state_is_expired(client):
    response = client.get("/api/google-login/status?state=never-existed")
    assert response.get_json()["status"] == "expired"


def test_mobile_google_login_callback_records_error_on_denied_consent(client, monkeypatch):
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    state = start["state"]

    response = client.get(f"/auth/google/callback?state={state}&error=access_denied")
    assert response.status_code == 200

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "error"
