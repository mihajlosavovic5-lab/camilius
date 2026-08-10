"""Tests for the mobile app's Bearer-authed /api/media routes (api.py) - the same
business_media.py rules app.py's session-authed /api/onboarding/media exercises in
tests/test_business_media.py, just reached the way the mobile app reaches everything
else. storage.upload_image/upload_video/delete_file are monkeypatched to avoid any real
Cloudinary/network call."""
import io

import database
import storage
from conftest import register_and_login


def _fake_upload(prefix):
    counter = {"n": 0}

    def upload(file_storage, folder):
        counter["n"] += 1
        return f"https://res.cloudinary.com/demo/{prefix}-{counter['n']}.jpg", f"{folder}/{prefix}-{counter['n']}"

    return upload


def _mock_storage(monkeypatch):
    monkeypatch.setattr(storage, "upload_image", _fake_upload("image"))
    monkeypatch.setattr(storage, "upload_video", _fake_upload("video"))
    deleted = []
    monkeypatch.setattr(storage, "delete_file", lambda public_id, resource_type="image": deleted.append((public_id, resource_type)))
    return deleted


def _upload(client, headers, role, filename="photo.jpg", content_type="image/jpeg"):
    return client.post(
        "/api/media",
        data={"role": role, "file": (io.BytesIO(b"fake bytes"), filename, content_type)},
        content_type="multipart/form-data",
        headers=headers,
    )


def test_mobile_upload_list_and_delete(client, unique_email, monkeypatch):
    token, business_id = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)

    upload_response = _upload(client, headers, "gallery")
    assert upload_response.status_code == 200
    media_id = upload_response.get_json()["id"]

    list_response = client.get("/api/media", headers=headers)
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.get_json()["media"]] == [media_id]

    delete_response = client.delete(f"/api/media/{media_id}", headers=headers)
    assert delete_response.status_code == 200
    assert database.business_media_for_business(business_id) == []


def test_mobile_media_requires_auth(client):
    response = client.get("/api/media")
    assert response.status_code == 401


def test_mobile_upload_rejects_unsupported_role(client, unique_email, monkeypatch):
    token, _ = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)

    response = _upload(client, headers, "banner")
    assert response.status_code == 400
