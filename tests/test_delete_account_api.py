import database
from conftest import register_and_login


def test_delete_account_requires_auth(client):
    response = client.post("/api/delete-account")
    assert response.status_code == 401


def test_delete_account_requires_correct_password(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)

    response = client.post(
        "/api/delete-account",
        json={"password": "wrong-password", "reason_category": "other"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert database.business_by_id(business_id) is not None


def test_delete_account_requires_a_reason(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)

    response = client.post(
        "/api/delete-account",
        json={"password": "TestPassword123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert database.business_by_id(business_id) is not None


def test_delete_account_removes_the_business(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)

    response = client.post(
        "/api/delete-account",
        json={"password": "TestPassword123", "reason_category": "not_using", "reason_details": "Just testing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    assert database.business_by_id(business_id) is None

    # The old token must no longer work
    dashboard = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert dashboard.status_code == 401


def test_delete_account_records_the_reason(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)

    client.post(
        "/api/delete-account",
        json={"password": "TestPassword123", "reason_category": "too_expensive", "reason_details": "Pricey"},
        headers={"Authorization": f"Bearer {token}"},
    )

    deletions = database.recent_account_deletions()
    match = next(d for d in deletions if d[1] == business_id)
    assert match[7] == "too_expensive"
    assert match[8] == "Pricey"
    assert match[9] == "mobile"
