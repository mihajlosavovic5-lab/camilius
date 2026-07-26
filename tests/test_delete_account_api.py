import database
from conftest import register_and_login


def test_delete_account_requires_auth(client):
    response = client.post("/api/delete-account")
    assert response.status_code == 401


def test_delete_account_removes_the_business(client, unique_username, unique_email):
    token, business_id = register_and_login(client, unique_username, unique_email)

    response = client.post("/api/delete-account", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    assert database.business_by_id(business_id) is None

    # The old token must no longer work
    dashboard = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert dashboard.status_code == 401
