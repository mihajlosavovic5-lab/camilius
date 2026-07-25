from conftest import register_business, register_business_web


def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_register_web_rejects_password_mismatch(client, unique_username, unique_email):
    response = register_business_web(client, unique_username, unique_email, confirm_password="SomethingElse123")
    assert response.status_code == 200  # re-renders the form with an error, doesn't redirect
    assert "match" in response.get_data(as_text=True)


def test_register_creates_account_and_logs_in(client, unique_username, unique_email):
    response = register_business(client, unique_username, unique_email)
    assert response.status_code == 200
    data = response.get_json()
    assert data["token"]
    assert data["business_name"] == "Test Salon"

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


def test_login_with_phone_number(client, unique_username, unique_email):
    phone = f"+38160{unique_username[-7:]}"
    register_business(client, unique_username, unique_email, phone_number=phone)
    response = client.post("/api/login", json={"username": phone, "password": "TestPassword123"})
    assert response.status_code == 200
    assert response.get_json()["token"]
