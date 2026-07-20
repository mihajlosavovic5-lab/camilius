from conftest import register_business


def test_dashboard_requires_auth(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 401


def test_dashboard_rejects_garbage_token(client):
    response = client.get("/api/dashboard", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_dashboard_works_with_valid_token(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    token = reg.get_json()["token"]

    response = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["business_name"] == "Test Salon"
    assert data["conversations"] == []
    assert data["pending_count"] == 0
