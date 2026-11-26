"""Continue with Google" - covers the shared account-lookup logic (google_oauth.login_existing),
account creation (google_oauth.create_account), the web redirect/callback routes, the
mobile start/callback/status bridge, and the register()/api_register()
google_link_token handoff kept as a fallback for older app installs. A Google identity
nobody's seen before (register intent) gets an account created for it on the spot - see
google_oauth.create_account - and is logged straight in, exactly like an existing
account would be; a login-intent attempt with no matching account still just fails.

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
    """Same as _start_web_google_auth above, but with the legacy ?intent=login query param -
    login.html no longer sends this itself (a brand-new Google identity from the Login
    screen is now handed to sign-up prefilled, same as Create Account - see auth_google()'s
    own comment), but the route still honors it for any caller that does, so this covers
    that fallback path directly rather than through the template."""
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


def test_login_page_google_button_sends_no_login_intent(client, monkeypatch):
    """login.html's Google button (plain href and GIS script alike) must not send
    ?intent=login/"login" anymore - a brand-new Google identity from the Login screen is
    now handed to sign-up prefilled instead of shown a "no account" error, same as the
    mobile app's own login-google-btn (mobile/app.js)."""
    _configure_google(monkeypatch)
    html = client.get("/login").get_data(as_text=True)
    assert "intent=login" not in html
    assert 'intent: "login"' not in html


def test_login_page_create_account_button_moved_into_email_form(client, monkeypatch):
    """"Create account" must only render inside the email-form panel (revealed by
    "Continue with email"), not in the initial Google/email choice screen above it."""
    _configure_google(monkeypatch)
    html = client.get("/login").get_data(as_text=True)
    choice_start = html.index('id="login-choice"')
    form_wrap_start = html.index('id="login-form-wrap"')
    register_link_pos = html.index('data-i18n="login.registerLink"')
    assert choice_start < form_wrap_start < register_link_pos


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

def test_auth_google_callback_new_identity_creates_account_and_logs_in(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email, name="Jane's Salon"))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/register" not in location
    assert location.endswith("/") or "home" in location

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[11] is True  # email_verified - Google already proved it
    assert business[21] == sub  # google_id linked

    with client.session_transaction() as sess:
        assert sess.get("business_id") == business[0]


def test_auth_google_callback_new_identity_with_login_intent_shows_error_not_signup(client, monkeypatch, unique_email):
    """Legacy ?intent=login behavior (see _start_web_google_auth_login) - no account
    linked to that Google identity sends the visitor back to /login with an error rather
    than a hand-off to sign-up or creating an account outright. Nothing in the UI sends
    this intent anymore, but the route still supports it."""
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


def test_auth_google_callback_existing_account_with_login_intent_still_logs_in(client, monkeypatch, unique_email):
    """intent=login must not change anything for a Google identity that DOES match an
    existing account - it should log straight in exactly like before."""
    from conftest import register_business

    register_business(client, unique_email)

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


def test_register_with_valid_google_link_token_creates_verified_linked_account(client, monkeypatch, unique_email):
    """create_account() is now the primary path for a brand-new Google identity, but
    register()/api_register() must keep honoring a google_link_token minted directly
    (google_oauth.make_link_token) as a fallback for older app installs that still send
    one - see google_oauth.py's own comment above LINK_TOKEN_MAX_AGE."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    link_token = app_module.google_oauth.make_link_token(sub, unique_email)

    from conftest import _csrf_token_from_html
    csrf = _csrf_token_from_html(client.get("/register").get_data(as_text=True))

    response = client.post("/register", data={
        "csrf_token": csrf,
        "google_link_token": link_token,
        "name": "Jane's Salon",
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
    assert business[11] is True  # email_verified - Google already proved it, no confirmation link needed
    assert business[21] == sub  # google_id linked

    # Logged straight in - no verify_pending detour.
    with client.session_transaction() as sess:
        assert sess.get("business_id") == business[0]
        assert sess.get("pending_verify_business_id") is None


def test_register_with_mismatched_email_ignores_google_link_token(client, monkeypatch, unique_email):
    """Visitor changed the pre-filled email before submitting - the token was issued for a
    different address, so it must NOT silently attach to whatever they typed instead.
    Same fallback path as the test above - the token is minted directly rather than via
    the callback, which no longer hands one out for a brand-new identity."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    link_token = app_module.google_oauth.make_link_token(sub, f"google-{unique_email}")

    from conftest import _csrf_token_from_html
    csrf = _csrf_token_from_html(client.get("/register").get_data(as_text=True))

    response = client.post("/register", data={
        "csrf_token": csrf,
        "google_link_token": link_token,
        "name": "Jane's Salon",
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
    assert business[11] is False  # not auto-verified - normal email confirmation still required
    assert business[21] is None  # not linked to the Google account

    with client.session_transaction() as sess:
        assert sess.get("pending_verify_business_id") == business[0]  # normal verify_pending flow


# --- Web flow: a Google identity that's already linked (or matches by email) ---

def test_auth_google_callback_links_existing_account_by_email(client, monkeypatch, unique_email):
    from conftest import register_business

    register_business(client, unique_email)
    existing = database.business_by_contact_email(unique_email)
    assert existing[21] is None  # not linked yet

    _configure_google(monkeypatch)
    state = _start_web_google_auth(client)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert response.status_code == 302
    assert "/register" not in response.headers["Location"]

    linked = database.business_by_id(existing[0])
    assert linked[21] == sub
    assert linked[11] is True  # now verified via Google, even though the original signup wasn't

    with client.session_transaction() as sess:
        assert sess.get("business_id") == existing[0]


def test_auth_google_callback_reuses_existing_google_link(client, monkeypatch, unique_email):
    """Same Google account signs in a second time, after already being created by a first
    attempt - should log straight into that same account, not create a second one."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))

    state1 = _start_web_google_auth(client)
    client.get(f"/auth/google/callback?code=abc&state={state1}")
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


def test_mobile_google_login_new_identity_status_is_done(client, monkeypatch, unique_email):
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
    assert status["status"] == "done"
    assert status["token"]  # logged straight in - account created on the spot

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[11] is True  # email_verified
    assert business[21] == sub  # google_id linked

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


def test_mobile_google_login_existing_account_with_login_intent_status_is_done(client, monkeypatch, unique_email):
    from conftest import register_business

    register_business(client, unique_email)

    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start", json={"intent": "login"}).get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    client.get(f"/auth/google/callback?code=abc&state={start['state']}")

    status = client.get(f"/api/google-login/status?state={start['state']}").get_json()
    assert status["status"] == "done"
    assert status["token"]


def test_mobile_api_register_with_valid_google_link_token_returns_token_directly(client, monkeypatch, unique_email):
    """create_account() is now the primary path for a brand-new Google identity (see the
    mobile bridge tests above), but api_register must keep honoring a google_link_token
    minted directly as a fallback for older app installs that still send one."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    link_token = app_module.google_oauth.make_link_token(sub, unique_email)

    response = client.post("/api/register", json={
        "name": "Jane's Salon",
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
    assert business[11] is True
    assert business[21] == sub


def test_mobile_google_login_existing_account_status_is_done(client, monkeypatch, unique_email):
    """A Google identity that already completed sign-up once logs straight in on a later
    attempt, same as the web equivalent test above."""
    from conftest import register_business

    register_business(client, unique_email)

    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    client.get(f"/auth/google/callback?code=abc&state={start['state']}")

    status = client.get(f"/api/google-login/status?state={start['state']}").get_json()
    assert status["status"] == "done"
    assert status["token"]
    assert status["business_id"]


def test_mobile_google_login_success_callback_redirects_to_app_link_bridge(client, monkeypatch, unique_email):
    """A plain successful mobile login (existing account, no signup/error to report)
    redirects to /auth/google/done with no outcome param - the verified Android App Link
    target (see AndroidManifest.xml) that a signed APK with the matching manifest entry
    intercepts before Chrome/the Custom Tab ever loads it. Falling back to actually
    rendering it (as this test client does, with no App Link interception involved) must
    land on the bare google_auth_mobile_blank.html - not google_auth_mobile_result.html's
    full base.html-derived page - so nothing from the site's own chrome (install banner,
    language picker, brand logo, etc.) has a chance to flash on screen."""
    from conftest import register_business

    register_business(client, unique_email)

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


def test_mobile_google_login_callback_records_cancelled_on_denied_consent(client, monkeypatch):
    # Denying/backing out of Google's own consent screen isn't a failure - it's recorded as
    # its own 'cancelled' status (not 'error') so the app can quietly return to the login
    # screen instead of showing a "something went wrong" error (see app.py's
    # auth_google_callback and mobile/app.js's applyGoogleLoginResult).
    _configure_google(monkeypatch)
    start = client.post("/api/google-login/start").get_json()
    state = start["state"]

    response = client.get(f"/auth/google/callback?state={state}&error=access_denied")
    assert response.status_code == 302
    assert "/auth/google/done" in response.headers["Location"]
    assert client.get(response.headers["Location"]).status_code == 200

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "cancelled"


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


def test_auth_google_done_hands_back_to_app_by_package(client, monkeypatch):
    """The plain success page must send the Custom Tab back to the app (intent:// URL naming
    the package) and offer a tappable fallback - not leave the visitor on a blank page."""
    _configure_google(monkeypatch)
    html = client.get("/auth/google/done?state=abc123").get_data(as_text=True)
    assert "intent://" in html
    assert "package=com.ai_auto_reply_mail.app.twa" in html
    assert "/auth/google/done?state=abc123" in html
    assert 'id="back-link"' in html


# --- Mobile native bottom sheet (Credential Manager): /api/google-login/verify-id-token ---
#
# The Android app's Credential Manager flow hands the app an ID token directly (no browser
# tab, no polling) - google_oauth.verify_id_token is monkeypatched here exactly like
# fetch_user is above, standing in for the real call to Google's tokeninfo endpoint.

def test_mobile_manifest_exposes_google_web_client_id(client, monkeypatch):
    """mobile/app.js's ensureGoogleSocialLoginInitialized() reads this to initialize the
    native Credential Manager plugin - see api_mobile_manifest's own comment on why
    piggybacking it on this endpoint (rather than GOOGLE_CLIENT_ID being a secret) is fine."""
    _configure_google(monkeypatch)
    data = client.get("/api/mobile-manifest").get_json()
    assert data["google_web_client_id"] == "test-client-id"


def test_mobile_manifest_google_web_client_id_absent_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    data = client.get("/api/mobile-manifest").get_json()
    assert not data["google_web_client_id"]


def test_verify_id_token_404s_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = client.post("/api/google-login/verify-id-token", json={"id_token": "x"})
    assert response.status_code == 404


def test_verify_id_token_missing_token_is_error(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.post("/api/google-login/verify-id-token", json={})
    assert response.get_json()["status"] == "error"


def test_verify_id_token_invalid_token_is_error(client, monkeypatch):
    _configure_google(monkeypatch)
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda id_token: None)
    response = client.post("/api/google-login/verify-id-token", json={"id_token": "bad"})
    assert response.get_json()["status"] == "error"


def test_verify_id_token_new_identity_is_done(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda id_token: _fake_profile(sub, unique_email, name="Jane's Salon"))
    response = client.post("/api/google-login/verify-id-token", json={"id_token": "tok", "intent": "register"})
    data = response.get_json()
    assert data["status"] == "done"
    assert data["token"]

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[11] is True  # email_verified
    assert business[21] == sub  # google_id linked


def test_verify_id_token_new_identity_with_login_intent_is_no_account(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda id_token: _fake_profile(sub, unique_email))
    response = client.post("/api/google-login/verify-id-token", json={"id_token": "tok", "intent": "login"})
    assert response.get_json()["status"] == "no_account"
    assert database.business_by_contact_email(unique_email) is None


def test_verify_id_token_existing_account_is_done(client, monkeypatch, unique_email):
    from conftest import register_business

    register_business(client, unique_email)

    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda id_token: _fake_profile(sub, unique_email))
    response = client.post("/api/google-login/verify-id-token", json={"id_token": "tok"})
    data = response.get_json()
    assert data["status"] == "done"
    assert data["token"]
    assert data["business_id"]


def test_verify_id_token_link_token_works_with_register(client, monkeypatch, unique_email):
    """create_account() is now the primary path for a brand-new Google identity (see the
    test above), but api_register must keep honoring a google_link_token minted directly
    (google_oauth.make_link_token) as a fallback for older app installs that still send
    one."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    link_token = app_module.google_oauth.make_link_token(sub, unique_email)

    response = client.post("/api/register", json={
        "name": "Jane's Salon",
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
    assert data["token"]

    business = database.business_by_contact_email(unique_email)
    assert business[11] is True
    assert business[21] == sub


# --- Web native bottom sheet (Google Identity Services): /auth/google/token ---
#
# login.html's GIS script posts the credential Google hands it client-side here - no
# browser redirect, no session["oauth_state"] round trip. google_oauth.verify_id_token is
# monkeypatched here exactly like fetch_user/verify_id_token are above, standing in for
# the real call to Google's tokeninfo endpoint.

def test_login_page_google_button_is_plain_link_without_one_tap(client, monkeypatch):
    # One Tap used to intercept this button with the href as a fallback, which could ask
    # the visitor to pick their account twice (see login.html's own comment). The button
    # must be just the /auth/google link, with no GIS script on the page.
    _configure_google(monkeypatch)
    html = client.get("/login").get_data(as_text=True)
    assert "accounts.google.com/gsi/client" not in html
    assert "login-google-btn" in html
    assert 'href="/auth/google?intent=prefill"' in html


def test_login_page_omits_gis_script_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    html = client.get("/login").get_data(as_text=True)
    assert "accounts.google.com/gsi/client" not in html


def test_auth_google_token_404s_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = client.post("/auth/google/token", json={"credential": "x"})
    assert response.status_code == 404


def test_auth_google_token_missing_credential_redirects_to_login_with_error(client, monkeypatch):
    _configure_google(monkeypatch)
    response = client.post("/auth/google/token", json={})
    data = response.get_json()
    assert "/login" in data["redirect"]
    assert "error=" in data["redirect"]


def test_auth_google_token_invalid_credential_redirects_to_login_with_error(client, monkeypatch):
    _configure_google(monkeypatch)
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda credential: None)
    response = client.post("/auth/google/token", json={"credential": "bad"})
    data = response.get_json()
    assert "/login" in data["redirect"]
    assert "error=" in data["redirect"]


def test_auth_google_token_new_identity_with_login_intent_redirects_to_login_with_no_account_error(client, monkeypatch, unique_email):
    """Legacy ?intent=login behavior, same as the plain redirect flow's own login-intent
    test above - login.html's GIS script no longer sends this itself (see its own script
    block), but the endpoint still honors it if sent directly."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda credential: _fake_profile(sub, unique_email))
    response = client.post("/auth/google/token", json={"credential": "tok", "intent": "login"})
    data = response.get_json()
    assert "/register" not in data["redirect"]
    assert "/login" in data["redirect"]

    login_page = client.get(data["redirect"])
    html = login_page.get_data(as_text=True)
    assert "No Camilius account" in html or "no account" in html.lower()
    assert database.business_by_contact_email(unique_email) is None


def test_auth_google_token_new_identity_with_register_intent_creates_account_and_logs_in(client, monkeypatch, unique_email):
    """This is exactly what login.html's GIS script sends today (see its own script
    block) - a brand-new Google identity gets an account created for it and is logged
    straight in, same as the plain redirect flow (auth_google_callback)."""
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda credential: _fake_profile(sub, unique_email, name="Jane's Salon"))
    response = client.post("/auth/google/token", json={"credential": "tok", "intent": "register"})
    data = response.get_json()
    assert "/register" not in data["redirect"]
    assert data["redirect"].endswith(app_module.url_for("home"))

    business = database.business_by_contact_email(unique_email)
    assert business is not None
    assert business[11] is True  # email_verified
    assert business[21] == sub  # google_id linked

    with client.session_transaction() as sess:
        assert sess.get("business_id") == business[0]


def test_auth_google_token_existing_account_logs_in(client, monkeypatch, unique_email):
    from conftest import register_business

    register_business(client, unique_email)

    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda credential: _fake_profile(sub, unique_email))
    response = client.post("/auth/google/token", json={"credential": "tok", "intent": "login"})
    data = response.get_json()
    assert data["redirect"].endswith(app_module.url_for("home"))

    with client.session_transaction() as sess:
        assert sess.get("business_id") is not None


def test_auth_google_token_reuses_existing_google_link(client, monkeypatch, unique_email):
    """A Google identity already linked (via the plain redirect flow, or a previous GIS
    sign-in) logs in the same way on a later GIS attempt - no re-linking needed."""
    from conftest import register_business

    register_business(client, unique_email)

    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    state = _start_web_google_auth_login(client)
    client.get(f"/auth/google/callback?code=abc&state={state}")  # links google_id to the account

    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda credential: _fake_profile(sub, unique_email))
    response = client.post("/auth/google/token", json={"credential": "tok", "intent": "login"})
    data = response.get_json()
    assert data["redirect"].endswith(app_module.url_for("home"))


# --- intent="prefill": Login screen's Google button - no account => Create Account form ---

def test_web_prefill_intent_new_identity_redirects_to_register_prefilled(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    start = client.get("/auth/google?intent=prefill")
    state = start.headers["Location"].split("state=")[1].split("&")[0]
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email, "Ana Test"))

    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert callback.status_code == 302
    location = callback.headers["Location"]
    assert "/register" in location and "google_link_token=" in location
    assert database.business_by_contact_email(unique_email) is None  # nothing created yet

    page = client.get(location).get_data(as_text=True)
    assert unique_email in page and "Ana Test" in page


def test_web_prefill_intent_existing_identity_logs_in(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email))
    # First visit with register intent creates the account...
    state = _start_web_google_auth(client)
    client.get(f"/auth/google/callback?code=abc&state={state}")
    client.get("/logout")
    # ...then the Login screen's prefill intent just logs it in.
    start = client.get("/auth/google?intent=prefill")
    state = start.headers["Location"].split("state=")[1].split("&")[0]
    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert callback.status_code == 302
    assert "/register" not in callback.headers["Location"]


def test_mobile_prefill_intent_new_identity_status_is_needs_signup(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    state = client.post("/api/google-login/start", json={"intent": "prefill"}).get_json()["state"]
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "fetch_user", lambda code, redirect_uri: _fake_profile(sub, unique_email, "Ana Test"))
    callback = client.get(f"/auth/google/callback?code=abc&state={state}")
    assert "/auth/google/done" in callback.headers["Location"]
    page = client.get(callback.headers["Location"]).get_data(as_text=True)
    assert "intent://" in page  # hands the tab back to the app

    status = client.get(f"/api/google-login/status?state={state}").get_json()
    assert status["status"] == "needs_signup"
    assert status["google_email"] == unique_email
    assert status["google_link_token"]
    assert database.business_by_contact_email(unique_email) is None


def test_verify_id_token_prefill_intent_new_identity_is_needs_signup(client, monkeypatch, unique_email):
    _configure_google(monkeypatch)
    sub = _unique_sub()
    monkeypatch.setattr(app_module.google_oauth, "verify_id_token", lambda id_token: _fake_profile(sub, unique_email))
    data = client.post("/api/google-login/verify-id-token", json={"id_token": "tok", "intent": "prefill"}).get_json()
    assert data["status"] == "needs_signup"
    assert data["google_email"] == unique_email and data["google_link_token"]
    assert database.business_by_contact_email(unique_email) is None
