"""Tests for the mobile app's Bearer-authed /api/website-chat route (api.py) - in
particular that an uploaded gallery photo's id, sent alongside a chat message, actually
reaches answer_website_chat() as a real image URL instead of being silently dropped (the
bug this route's image_ids handling was added to fix - see api.py's api_website_chat and
website_assistant.answer_website_chat)."""
import io

import api
import storage
from conftest import register_and_login


def _mock_storage(monkeypatch):
    def upload(file_storage, folder):
        return "https://res.cloudinary.com/demo/photo-1.jpg", f"{folder}/photo-1"
    monkeypatch.setattr(storage, "upload_image", upload)


def _upload_gallery_photo(client, headers):
    response = client.post(
        "/api/media",
        data={"role": "gallery", "file": (io.BytesIO(b"fake bytes"), "photo.jpg", "image/jpeg")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert response.status_code == 200
    return response.get_json()["id"]


def test_website_chat_forwards_uploaded_image_url(client, unique_email, monkeypatch):
    token, _ = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)
    media_id = _upload_gallery_photo(client, headers)

    captured = {}

    def fake_answer(message, history=None, business_context=None, image_urls=None):
        captured["image_urls"] = image_urls
        return "Nice photo!"

    monkeypatch.setattr(api, "answer_website_chat", fake_answer)

    response = client.post(
        "/api/website-chat",
        json={"message": "Here's my shop", "history": [], "image_ids": [media_id]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["reply"] == "Nice photo!"
    assert captured["image_urls"] == ["https://res.cloudinary.com/demo/photo-1.jpg"]


def test_website_chat_ignores_image_ids_from_another_business(client, unique_email, monkeypatch):
    token_a, _ = register_and_login(client, unique_email)
    other_email = f"other-{unique_email}"
    token_b, _ = register_and_login(client, other_email)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    _mock_storage(monkeypatch)
    foreign_media_id = _upload_gallery_photo(client, headers_a)

    captured = {}

    def fake_answer(message, history=None, business_context=None, image_urls=None):
        captured["image_urls"] = image_urls
        return "ok"

    monkeypatch.setattr(api, "answer_website_chat", fake_answer)

    response = client.post(
        "/api/website-chat",
        json={"message": "hi", "history": [], "image_ids": [foreign_media_id]},
        headers=headers_b,
    )

    assert response.status_code == 200
    assert captured["image_urls"] == []
