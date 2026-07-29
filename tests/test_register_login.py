import database
from conftest import register_business, register_business_web, register_and_login, web_post, _csrf_token_from_html


def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_register_web_rejects_password_mismatch(client, unique_username, unique_email):
    response = register_business_web(client, unique_username, unique_email, confirm_password="SomethingElse123")
    assert response.status_code == 200  # re-renders the form with an error, doesn't redirect
    assert "match" in response.get_data(as_text=True)


def test_register_creates_account_and_logs_in(client, unique_username, unique_email):
    # Registration itself no longer returns a token - the account can't log in until its
    # email is confirmed (register_business() marks it verified here, standing in for
    # clicking the confirmation link - see conftest.py).
    response = register_business(client, unique_username, unique_email)
    assert response.status_code == 200
    data = response.get_json()
    assert data["pending_verification"] is True

    login = client.post("/api/login", json={"username": unique_username, "password": "TestPassword123"})
    assert login.status_code == 200
    assert login.get_json()["token"]


def test_register_rejects_missing_terms(client, unique_username, unique_email):
    response = register_business(client, unique_username, unique_email, accept_terms=False)
    assert response.status_code == 400


def test_register_rejects_short_password(client, unique_username, unique_email):
    response = register_business(client, unique_username, unique_email, password="short")
    assert response.status_code == 400


def test_register_rejects_duplicate_username(client, unique_username, unique_email):
    first = register_business(client, unique_username, unique_email)
    assert first.status_code == 200

    second = register_business(client, unique_username, f"other-{unique_email}")
    assert second.status_code == 409


def test_login_rejects_wrong_password(client, unique_username, unique_email):
    register_business(client, unique_username, unique_email)
    response = client.post("/api/login", json={"username": unique_username, "password": "WrongPassword"})
    assert response.status_code == 401


def test_login_rejects_unknown_username(client):
    response = client.post("/api/login", json={"username": "no-such-user", "password": "whatever"})
    assert response.status_code == 401


def test_login_with_email_instead_of_username(client, unique_username, unique_email):
    """Login now accepts username, contact email, or phone number - same "any of these
    identifies you" pattern most consumer apps use."""
    register_business(client, unique_username, unique_email)
    response = client.post("/api/login", json={"username": unique_email, "password": "TestPassword123"})
    assert response.status_code == 200
    assert response.get_json()["token"]


def test_register_honors_push_enabled_choice(client, unique_username, unique_email):
    """The mobile registration wizard asks up front whether to enable notifications -
    whatever the user picks there should be the account's starting push_enabled value,
    not always-on with the user having to find Settings to opt back out."""
    token, business_id = register_and_login(client, unique_username, unique_email, push_enabled=False)
    response = client.get("/api/notification-preferences", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["push_enabled"] is False


def test_register_defaults_push_enabled_to_true(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)
    response = client.get("/api/notification-preferences", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["push_enabled"] is True


def test_login_with_phone_number(client, unique_username, unique_email):
    phone = f"+38160{unique_username[-7:]}"
    reg = register_business(client, unique_username, unique_email, phone_number=phone)
    # A phone number also needs its own confirmation before it's usable to log in with
    # (independent of the email one - see business_by_login_identifier's phone_verified
    # check), normally done from the Profile screen rather than at registration time.
    database.mark_phone_verified(reg.get_json()["business_id"])
    response = client.post("/api/login", json={"username": phone, "password": "TestPassword123"})
    assert response.status_code == 200
    assert response.get_json()["token"]


def _api_register_payload(username, email, **overrides):
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
    return payload


def test_register_resumes_abandoned_unverified_signup(client, unique_username, unique_email):
    """Registering again with an email stuck on an unverified account (e.g. the
    confirmation link landed in spam and the app was closed/reopened in the meantime)
    used to dead-end on "already taken" with no way back in. It should instead resume
    that same account and resend the confirmation link, not create a second one.

    Posts to /api/register directly (not the register_business() helper), which
    auto-verifies on success - the whole point here is to leave the first account
    unverified, exactly the stuck state this is meant to recover from."""
    first = client.post("/api/register", json=_api_register_payload(unique_username, unique_email))
    first_id = first.get_json()["business_id"]

    second = client.post("/api/register", json=_api_register_payload(f"other-{unique_username}", unique_email))
    assert second.status_code == 200
    data = second.get_json()
    assert data["pending_verification"] is True
    assert data["business_id"] == first_id

    database.mark_email_verified(first_id)
    login = client.post("/api/login", json={"username": unique_username, "password": "TestPassword123"})
    assert login.status_code == 200


def test_register_still_rejects_verified_duplicate_email(client, unique_username, unique_email):
    """Once the original account has actually confirmed its email, a second signup with
    that same address is a genuine conflict again, not a resumable one."""
    first = register_business(client, unique_username, unique_email)
    database.mark_email_verified(first.get_json()["business_id"])

    second = register_business(client, f"other-{unique_username}", unique_email)
    assert second.status_code == 409


def _register_web_form(client, username, email):
    """Raw equivalent of conftest's register_business_web(), but without that helper's
    auto-verify-and-log-in step - needed here to leave the account genuinely unverified,
    which is exactly the state this test is exercising."""
    get_response = client.get("/register")
    csrf_token = _csrf_token_from_html(get_response.get_data(as_text=True))
    form = {
        "csrf_token": csrf_token,
        "name": "Test Salon",
        "username": username,
        "password": "TestPassword123",
        "confirm_password": "TestPassword123",
        "contact_email": email,
        "working_hours": "Monday: 09:00-18:00",
        "price_list": "Haircut: 20 EUR",
        "faq": "",
        "accept_terms": "on",
    }
    return web_post(client, "/register", form)


def test_register_web_resumes_abandoned_unverified_signup(client, unique_username, unique_email):
    first = _register_web_form(client, unique_username, unique_email)
    assert first.status_code == 302
    first_business = database.business_by_username(unique_username)
    assert first_business[12] is False  # email_verified - never confirmed

    second = _register_web_form(client, f"other-{unique_username}", unique_email)
    assert second.status_code == 302
    assert second.headers["Location"].endswith("/verify-pending")
    with client.session_transaction() as session:
        assert session["pending_verify_business_id"] == first_business[0]
    # No second account was created for the new username.
    assert database.business_by_username(f"other-{unique_username}") is None


# --- /api/check-availability - the Create Account wizard's live "already taken" check
# gates its own Next/step button on this, so it has to agree with api_register()'s
# resume behavior above or the wizard would block the user before they ever reach
# submit (see api_check_availability()'s own comment in api.py). ---

def test_check_availability_treats_unverified_email_as_available_when_anonymous(client, unique_username, unique_email):
    client.post("/api/register", json=_api_register_payload(unique_username, unique_email))
    response = client.post("/api/check-availability", json={"field": "contact_email", "value": unique_email})
    assert response.status_code == 200
    assert response.get_json()["taken"] is False


def test_check_availability_still_blocks_verified_email_when_anonymous(client, unique_username, unique_email):
    reg = client.post("/api/register", json=_api_register_payload(unique_username, unique_email))
    database.mark_email_verified(reg.get_json()["business_id"])
    response = client.post("/api/check-availability", json={"field": "contact_email", "value": unique_email})
    assert response.status_code == 200
    assert response.get_json()["taken"] is True


def test_check_availability_still_blocks_unverified_email_for_authenticated_caller(client, unique_username, unique_email):
    """The exception above is only for the anonymous Create Account wizard resuming its
    OWN abandoned signup - a logged-in caller (Settings' email field) trying to claim a
    DIFFERENT, unrelated account's still-unverified email must still see it as taken."""
    client.post("/api/register", json=_api_register_payload(unique_username, unique_email))

    other_username = f"other-{unique_username}"
    other_email = f"other-{unique_email}"
    token, _ = register_and_login(client, other_username, other_email)

    response = client.post(
        "/api/check-availability",
        json={"field": "contact_email", "value": unique_email},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.get_json()["taken"] is True
