"""Continue with Google" - covers the shared account-lookup logic (_google_login_existing),
the web redirect/callback routes, the mobile start/callback/status bridge, and the
register()/api_register() google_link_token handoff. A Google identity nobody's seen
before is never turned into an account directly - it's handed to the normal sign-up form
as a name/email starting point (see app.py's auth_google_callback), and only becomes a
real, Google-linked account once that form is actually submitted.

Real network calls to Google are never made - google_oauth.fetch_user is monkeypatched
everywhere a callback needs a profile, exactly like _stub_send_email stands in for Resend
elsewhere in this suite.
"""
import uuid

import database
import app as app_module


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")


def _unique_sub():
    # A real Google `sub` is globally unique per Google account forever - fixed test
    # literals would collide with rows a previous run of this same test already committed
    # to the DB (no per-test transaction rollback in this suite - see conftest.py).
    return f"gsub-{uuid.uuid4().hex}"


def _fake_profile(sub, email, name="Test Business"):
    return {"sub": sub, "email": email, "email_verified": True, "name": name}


def _start_web_google_auth(client):
    """GET /auth/google and pull the `state` it embeds in the redirect - shared setup for
    every web-flow test below."""
    start = client.get("/auth/google")
    return start.headers["Location"].split("state=")[1].split("&")[0]


def _start_web_google_auth_login(client):
    """Same as _start_web_google_auth above, but as if the visitor clicked "Continue with
    Google" on the Login screen (login.html passes ?intent=login) rather than Create
    Account - the case where a brand-new Google identity must get an error, not a hand-off
    to sign-up."""
    start = client.get("/auth/google?intent=login")
    return start.headers["Location"].split("state=")[1].split("&")[0]


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


# --- Web flow: CSRF / state handling ---

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


# --- Web flow: a Google identity never seen before ---

def test_auth_google_callback_new_identity_redirects_to_register_prefilled(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email, name="Jane's Salon"))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("/register") or "/register" in location
    assert "google_link_token=" in location

    # No account created, nobody logged in yet - it's purely a prefilled starting point.
    with client.session_transaction() as sess:
        assert sess.get("business_id") is None
    assert database.business_by_contact_email(unique_email) is None

    # Following the redirect shows the sign-up form with name/email pre-filled.
    register_page = client.get(location)
    html = register_page.get_data(as_text=True)
    assert "Jane&#39;s Salon" in html or "Jane's Salon" in html
    assert unique_email in html


def test_auth_google_callback_new_identity_with_login_intent_shows_error_not_signup(client, monkeypatch, unique_email):
    """Clicked "Continue with Google" on the Login screen, but no account is linked to
    that Google identity - must send the visitor back to /login with an error, never hand
    them to the sign-up form or create an account outright."""
    _configure_google(monkeypatch)
    state = _start_web_google_auth_login(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/register" not in location
    assert "/login" in location

    assert database.business_by_contact_email(unique_email) is None
    with client.session_transaction() as sess:
        assert sess.get("business_id") is None

    login_page = client.get(location)
    html = login_page.get_data(as_text=True)
    assert "doesn&#39;t exist" in html or "isn&#39;t linked" in html or "No Camilius account" in html or "no account" in html.lower()


def test_auth_google_callback_existing_account_with_login_intent_still_logs_in(client, monkeypatch, unique_username, unique_email):
    """intent=login must not change anything for a Google identity that DOES match an
    existing account - it should log straight in exactly like before."""
    from conftest import register_business

    register_business(client, unique_username, unique_email)

    _configure_google(monkeypatch)
    state = _start_web_google_auth_login(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    assert "/register" not in response.headers["Location"]
    assert "/login" not in response.headers["Location"]

    with client.session_transaction() as sess:
        assert sess.get("business_id") is not None


def test_register_with_valid_google_link_token_creates_verified_linked_account(client, monkeypatch, unique_username, unique_email):
    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    redirect_response = client.get(f"/auth/google/callback?code=abc&state={state}")
    link_token = redirect_response.headers["Location"].split("google_link_token=")[1].split("&")[0]

    from conftest import _csrf_token_from_html
    csrf = _csrf_token_from_html(client.get("/register").get_data(as_text=True))

    response = client.post("/register", data={
        "csrf_token": csrf,
        "google_link_token": link_token,
        "name": "Jane's Salon",
        "username": unique_username,
        "password": "TestPassword123",
        "confirm_password": "TestPassword123",
        "contact_email": unique_email,  # same email the token was issued for
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": "on",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/") or "home" in response.headers["Location"] or response.headers["Location"] == "/"

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[12] is True  # email_verified - Google already proved it, no confirmation link needed
    assert business[22] == sub  # google_id linked

    # Logged straight in - no verify_pending detour.
    with client.session_transaction() as sess:
        assert sess.get("business_id") == business[0]
        assert sess.get("pending_verify_business_id") is None


def test_register_with_mismatched_email_ignores_google_link_token(client, monkeypatch, unique_username, unique_email):
    """Visitor changed the pre-filled email before submitting - the token was issued for a
    different address, so it must NOT silently attach to whatever they typed instead."""
    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, f"google-{unique_email}"))
    redirect_response = client.get(f"/auth/google/callback?code=abc&state={state}")
    link_token = redirect_response.headers["Location"].split("google_link_token=")[1].split("&")[0]

    from conftest import _csrf_token_from_html
    csrf = _csrf_token_from_html(client.get("/register").get_data(as_text=True))

    response = client.post("/register", data={
        "csrf_token": csrf,
        "google_link_token": link_token,
        "name": "Jane's Salon",
        "username": unique_username,
        "password": "TestPassword123",
        "confirm_password": "TestPassword123",
        "contact_email": unique_email,  # NOT the email the token was issued for
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": "on",
    })
    assert response.status_code == 302

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[12] is False  # not auto-verified - normal email confirmation still required
    assert business[22] is None  # not linked to the Google account

    with client.session_transaction() as sess:
        assert sess.get("pending_verify_business_id") == business[0]  # normal verify_pending flow


# --- Web flow: a Google identity that's already linked (or matches by email) ---

def test_auth_google_callback_links_existing_account_by_email(client, monkeypatch, unique_username, unique_email):
    from conftest import register_business

    register_business(client, unique_username, unique_email)
    existing = database.business_by_contact_email(unique_email)
    assert existing[22] is None  # not linked yet

    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    assert "/register" not in response.headers["Location"]

    linked = database.business_by_id(existing[0])
    assert linked[22] == sub
    assert linked[12] is True  # now verified via Google, even though the original signup wasn't

    with client.session_transaction() as sess:
        assert sess.get("business_id") == existing[0]


def test_auth_google_callback_reuses_existing_google_link(client, monkeypatch, unique_username, unique_email):
    """Same Google account signs in a second time, after already completing sign-up once -
    should log straight in, not redirect to register again."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    state1 = _start_web_google_auth(client)
    first_redirect = client.get(f"/auth/google/callback?code=abc&state={state1}")
    link_token = first_redirect.headers["Location"].split("google_link_token=")[1].split("&")[0]

    from conftest import _csrf_token_from_html
    csrf = _csrf_token_from_html(client.get("/register").get_data(as_text=True))
    client.post("/register", data={
        "csrf_token": csrf,
        "google_link_token": link_token,
        "name": "Jane's Salon",
        "username": unique_username,
        "password": "TestPassword123",
        "confirm_password": "TestPassword123",
        "contact_email": unique_email,
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": "on",
    })
    with client.session_transaction() as sess:
        first_business_id = sess["business_id"]
        sess.clear()

    state2 = _start_web_google_auth(client)
    second_response = client.get(f"/auth/google/callback?code=abc&state={state2}")
    assert "/register" not in second_response.headers["Location"]
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


def test_mobile_google_login_new_identity_status_is_needs_signup(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    state = start["state"]

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "pending"

    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email, name="Jane's Salon"))
    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    # Redirects to the App Link bridge page (see app.py's auth_google_done) rather than
    # rendering directly - Android intercepts this on a signed APK with the manifest
    # entry; here (no such interception in a test client) it just renders as a fallback.
    assert callback.status_code == 302
    assert "/auth/google/done" in callback.headers["Location"]
    assert client.get(callback.headers["Location"]).status_code == 200

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "needs_signup"
    assert status["google_name"] == "Jane's Salon"
    assert status["google_email"] == unique_email
    assert status["google_link_token"]
    assert not status["token"]  # not logged in - no account exists yet

    # Single-use - a second poll finds nothing left.
    status_again = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status_again["status"] == "expired"


def test_mobile_google_login_new_identity_with_login_intent_status_is_no_account(client, monkeypatch, unique_email):
    """Mobile equivalent of the web login-intent test above - the app's Login screen
    passes intent="login" to /api/google-login/start (see mobile/app.js's
    startGoogleLogin), so a brand-new identity must come back as 'no_account', not
    'needs_signup' - the app must not open the Create Account wizard on its own."""
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start", json={"intent": "login"}).get_json()
    state = start["state"]

    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert callback.status_code == 302
    assert "/auth/google/done" in callback.headers["Location"]
    assert client.get(callback.headers["Location"]).status_code == 200

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "no_account"
    assert not status["token"]

    assert database.business_by_contact_email(unique_email) is None

    # Single-use, same as every other terminal status.
    status_again = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status_again["status"] == "expired"


def test_mobile_google_login_existing_account_with_login_intent_status_is_done(client, monkeypatch, unique_username, unique_email):
    from conftest import register_business

    register_business(client, unique_username, unique_email)

    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start", json={"intent": "login"}).get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    client.get(f"/auth/google/callback?code=abc&state={start['state']}")

    status = client.get(f"/api/google-login/status?state={start['state']}").get_json()
    assert status["status"] == "done"
    assert status["token"]


def test_mobile_api_register_with_valid_google_link_token_returns_token_directly(client, monkeypatch, unique_username, unique_email):
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    client.get(f"/auth/google/callback?code=abc&state={start['state']}")
    status = client.get(f"/api/google-login/status?state={start['state']}").get_json()
    link_token = status["google_link_token"]

    response = client.post("/api/register", json={
        "name": "Jane's Salon",
        "username": unique_username,
        "password": "TestPassword123",
        "contact_email": unique_email,
        "working_hours": "Mon-Fri 09:00-18:00",
        "price_list": "Cut: 100",
        "faq": "",
        "accept_terms": True,
        "google_link_token": link_token,
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data["token"]  # logged in directly, no pending_verification
    assert "pending_verification" not in data or not data["pending_verification"]

    business = database.business_by_contact_email(unique_email)
    assert business[12] is True
    assert business[22] == sub


def test_mobile_google_login_existing_account_status_is_done(client, monkeypatch, unique_username, unique_email):
    """A Google identity that already completed sign-up once logs straight in on a later
    attempt, same as the web equivalent test above."""
    from conftest import register_business

    register_business(client, unique_username, unique_email)

    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    client.get(f"/auth/google/callback?code=abc&state={start['state']}")

    status = client.get(f"/api/google-login/status?state={start['state']}").get_json()
    assert status["status"] == "done"
    assert status["token"]
    assert status["business_id"]


def test_mobile_google_login_success_callback_redirects_to_app_link_bridge(client, monkeypatch, unique_username, unique_email):
    """A plain successful mobile login (existing account, no signup/error to report)
    redirects to /auth/google/done with no outcome param - the verified Android App Link
    target (see AndroidManifest.xml) that a signed APK with the matching manifest entry
    intercepts before Chrome/the Custom Tab ever loads it. Falling back to actually
    rendering it (as this test client does, with no App Link interception involved) must
    land on the bare google_auth_mobile_blank.html - not google_auth_mobile_result.html's
    full base.html-derived page - so nothing from the site's own chrome (install banner,
    language picker, brand logo, etc.) has a chance to flash on screen."""
    from conftest import register_business

    register_business(client, unique_username, unique_email)

    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    callback = client.get(f"/auth/google/callback?code=abc&state={start['state']}")

    assert callback.status_code == 302
    location = callback.headers["Location"]
    assert "/auth/google/done" in location
    assert "outcome=" not in location

    fallback = client.get(location)
    assert fallback.status_code == 200
    html = fallback.get_data(as_text=True)
    assert "mobile-install-banner" not in html
    assert "site-lang-select-btn" not in html
    assert "auth-brand" not in html
    assert "Camilius" in html  # just the bare page's <title>


def test_mobile_google_login_status_unknown_state_is_expired(client):
    response = client.get("/api/google-login/status?state=never-existed")
    assert response.get_json()["status"] == "expired"


def test_mobile_google_login_callback_records_error_on_denied_consent(client, monkeypatch):
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    state = start["state"]

    response = client.get(f"/auth/google/callback?state={state}&error=access_denied")
    assert response.status_code == 302
    assert "/auth/google/done" in response.headers["Location"]
    assert client.get(response.headers["Location"]).status_code == 200

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "error"


# --- /auth/google/done (Android App Link bridge fallback rendering) ---

def test_auth_google_done_404s_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = client.get("/auth/google/done")
    assert response.status_code == 404


def test_auth_google_done_outcome_no_account_renders_error_message(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.get("/auth/google/done?state=x&outcome=no_account")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No account found" in html


def test_auth_google_done_outcome_needs_signup_renders_signup_message(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.get("/auth/google/done?state=x&outcome=needs_signup")
    assert response.status_code == 200
    assert "Almost there" in response.get_data(as_text=True)


def test_auth_google_done_no_outcome_renders_bare_page(client, monkeypatch):
    """No `outcome` (the plain successful-login case) falls back to the same bare,
    resource-free page as a direct hit - no install banner/language picker/brand chrome."""
    _configure_google(monkeypatch)
    response = client.get("/auth/google/done?state=x")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "mobile-install-banner" not in html
    assert "auth-brand" not in html
