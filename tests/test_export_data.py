from conftest import register_business


def test_export_data_requires_login_redirects(client):
    response = client.get("/export-data")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_export_data_link_requires_auth(client):
    response = client.get("/api/export-data-link")
    assert response.status_code == 401


def test_export_data_mobile_flow_end_to_end(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    token = reg.get_json()["token"]

    link_response = client.get("/api/export-data-link", headers={"Authorization": f"Bearer {token}"})
    assert link_response.status_code == 200
    url = link_response.get_json()["url"]
    export_token = url.split("/export-data/", 1)[1]

    download = client.get(f"/export-data/{export_token}")
    assert download.status_code == 200
    data = download.get_json()
    assert data["business"]["username"] == unique_username
    assert data["business"]["name"] == "Test Salon"
    assert data["conversations"] == []
    # Secrets must never end up in the export
    assert "password" not in data["business"]
    assert "instagram_access_token" not in data["business"]


def test_export_data_mobile_rejects_bad_token(client):
    response = client.get("/export-data/not-a-real-token")
    assert response.status_code == 404
