"""Tests for the mobile app's Bearer-authed /api/website-chat route (api.py) - in
particular that an uploaded gallery photo's id, sent alongside a chat message, actually
reaches answer_website_chat_stream() as a real image URL instead of being silently
dropped (the bug this route's image_ids handling was added to fix), and that the route's
newline-delimited-JSON streaming (Faza 20 - see website_assistant.answer_website_chat_stream
and mobile/app.js's website-chat-status-pill) carries every status event plus the final
reply, in order."""
import io
import json

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


def _ndjson_events(response):
    text = response.get_data(as_text=True)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_website_chat_forwards_uploaded_image_url(client, unique_email, monkeypatch):
    token, _ = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)
    media_id = _upload_gallery_photo(client, headers)

    captured = {}

    def fake_stream(message, history=None, business_context=None, image_urls=None, tool_impls=None):
        captured["image_urls"] = image_urls
        yield {"type": "done", "reply": "Nice photo!"}

    monkeypatch.setattr(api, "answer_website_chat_stream", fake_stream)

    response = client.post(
        "/api/website-chat",
        json={"message": "Here's my shop", "history": [], "image_ids": [media_id]},
        headers=headers,
    )

    assert response.status_code == 200
    events = _ndjson_events(response)
    assert events == [{"type": "done", "reply": "Nice photo!"}]
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

    def fake_stream(message, history=None, business_context=None, image_urls=None, tool_impls=None):
        captured["image_urls"] = image_urls
        yield {"type": "done", "reply": "ok"}

    monkeypatch.setattr(api, "answer_website_chat_stream", fake_stream)

    response = client.post(
        "/api/website-chat",
        json={"message": "hi", "history": [], "image_ids": [foreign_media_id]},
        headers=headers_b,
    )

    assert response.status_code == 200
    assert captured["image_urls"] == []


def test_website_chat_streams_status_events_before_the_final_reply(client, unique_email, monkeypatch):
    token, _ = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}

    def fake_stream(message, history=None, business_context=None, image_urls=None, tool_impls=None):
        yield {"type": "status", "key": "websiteChat.status.thinking"}
        yield {"type": "status", "key": "websiteChat.status.updatePriceList"}
        yield {"type": "done", "reply": "Updated your price list."}

    monkeypatch.setattr(api, "answer_website_chat_stream", fake_stream)

    response = client.post(
        "/api/website-chat",
        json={"message": "Cut is 1500 din", "history": []},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.mimetype == "application/x-ndjson"
    events = _ndjson_events(response)
    assert events == [
        {"type": "status", "key": "websiteChat.status.thinking"},
        {"type": "status", "key": "websiteChat.status.updatePriceList"},
        {"type": "done", "reply": "Updated your price list."},
    ]
