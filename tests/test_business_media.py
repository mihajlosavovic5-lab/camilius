"""Tests for business_media.py and app.py's /api/onboarding/media route - the web
onboarding wizard's media step (see onboarding_media.html) and settings.html both go
through this. storage.upload_image/upload_video/delete_file are monkeypatched to avoid
any real Cloudinary/network call, the same way test_onboarding_wizard.py mocks the
OpenAI draft call - this suite is about the upload/replace/delete/limit rules, not
Cloudinary itself (see test_storage.py for that module's own validation)."""
import io

import database
import storage
import business_media
from conftest import register_business_web, web_post, get_csrf_token


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


def _upload(client, csrf_token, role, filename="photo.jpg", content_type="image/jpeg"):
    return client.post(
        "/api/onboarding/media",
        data={"csrf_token": csrf_token, "role": role, "file": (io.BytesIO(b"fake bytes"), filename, content_type)},
        content_type="multipart/form-data",
        headers={"Referer": "http://localhost/api/onboarding/media"},
    )


def test_logo_upload_replaces_previous_logo_and_deletes_old_asset(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    deleted = _mock_storage(monkeypatch)

    csrf_token = get_csrf_token(client, "/onboarding/website")
    first = _upload(client, csrf_token, "logo")
    assert first.status_code == 200
    first_id = first.get_json()["id"]

    second = _upload(client, csrf_token, "logo")
    assert second.status_code == 200

    rows = business_media.list_media(business_id)
    logos = [row for row in rows if row["role"] == "logo"]
    assert len(logos) == 1
    assert logos[0]["id"] != first_id
    assert any(public_id.endswith("image-1") for public_id, _ in deleted)


def test_gallery_cap_is_enforced_without_ever_uploading_the_rejected_file(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    deleted = _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    for _ in range(business_media.MAX_GALLERY_IMAGES):
        response = _upload(client, csrf_token, "gallery")
        assert response.status_code == 200

    response = _upload(client, csrf_token, "gallery")
    assert response.status_code == 400
    assert "up to" in response.get_json()["error"].lower()
    # The cap is checked before ever calling out to Cloudinary, so a rejected upload
    # never costs a real upload+delete round trip.
    assert len(deleted) == 0


def test_unknown_role_is_rejected(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token, "banner")
    assert response.status_code == 400


def test_delete_removes_row_and_cloud_asset(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    deleted = _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    upload_response = _upload(client, csrf_token, "gallery")
    media_id = upload_response.get_json()["id"]

    delete_response = client.delete(
        f"/api/onboarding/media/{media_id}",
        headers={"Referer": "http://localhost/api/onboarding/media", "X-CSRFToken": csrf_token},
    )
    assert delete_response.status_code == 200
    assert business_media.list_media(business_id) == []
    assert len(deleted) == 1


def test_cannot_delete_another_businesss_media(client, unique_username, unique_email, monkeypatch):
    other_username, other_email = f"{unique_username}-other", f"other-{unique_email}"
    register_business_web(client, other_username, other_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    media_id = _upload(client, csrf_token, "gallery").get_json()["id"]
    client.get("/logout")

    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    response = client.delete(
        f"/api/onboarding/media/{media_id}",
        headers={"Referer": "http://localhost/api/onboarding/media", "X-CSRFToken": csrf_token},
    )
    assert response.status_code == 404


def test_media_upload_requires_login(client):
    # CSRF tokens are session- not user-scoped, so an anonymous visitor can still get a
    # valid one (e.g. off the login page) - this isolates "no session" as the actual
    # reason for the redirect, rather than failing on the CSRF check first.
    csrf_token = get_csrf_token(client, "/login")
    response = _upload(client, csrf_token, "logo")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_public_page_renders_uploaded_logo_gallery_and_hero_video(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    logo_url = _upload(client, csrf_token, "logo").get_json()["url"]
    gallery_url = _upload(client, csrf_token, "gallery").get_json()["url"]
    video_url = _upload(client, csrf_token, "hero_video", filename="hero.mp4", content_type="video/mp4").get_json()["url"]

    slug = f"media-page-{unique_username}"
    database.upsert_business_page(business_id, slug, True, "Tag", "About")

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert logo_url in html
    assert gallery_url in html
    assert video_url in html
