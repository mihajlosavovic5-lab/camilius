"""Tests for business_documents.py and app.py's /api/onboarding/document route - the web
onboarding wizard's document upload (see onboarding_describe.html) and settings.html both
go through this. storage.upload_document/delete_file and document_extract.extract_text
are monkeypatched to avoid any real Cloudinary/network call, same "never make a real
upload call in tests" rule test_business_media.py already follows for images/video."""
import io

import database
import storage
import document_extract
import business_documents
from conftest import register_business_web, web_post, get_csrf_token


def _fake_upload():
    counter = {"n": 0}

    def upload(file_storage, folder):
        counter["n"] += 1
        return f"https://res.cloudinary.com/demo/doc-{counter['n']}.pdf", f"{folder}/doc-{counter['n']}"

    return upload


def _mock_storage(monkeypatch):
    monkeypatch.setattr(storage, "upload_document", _fake_upload())
    deleted = []
    monkeypatch.setattr(storage, "delete_file", lambda public_id, resource_type="image": deleted.append((public_id, resource_type)))
    monkeypatch.setattr(document_extract, "extract_text", lambda data, content_type: "Haircut: 20 EUR\nOpen Mon-Fri 9-18")
    return deleted


def _upload(client, csrf_token, filename="menu.pdf", content_type="application/pdf"):
    return client.post(
        "/api/onboarding/document",
        data={"csrf_token": csrf_token, "file": (io.BytesIO(b"fake bytes"), filename, content_type)},
        content_type="multipart/form-data",
        headers={"Referer": "http://localhost/api/onboarding/document"},
    )


def test_document_upload_stores_row_and_extracted_text(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token)
    assert response.status_code == 200
    body = response.get_json()
    assert body["filename"] == "menu.pdf"
    assert body["type"] == "pdf"

    rows = database.business_documents_for_business(business_id)
    assert len(rows) == 1
    assert rows[0][6] == "Haircut: 20 EUR\nOpen Mon-Fri 9-18"  # extracted_text


def test_document_upload_rejects_no_file(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = client.post(
        "/api/onboarding/document",
        data={"csrf_token": csrf_token},
        content_type="multipart/form-data",
        headers={"Referer": "http://localhost/api/onboarding/document"},
    )
    assert response.status_code == 400


def test_document_upload_rejects_unsupported_type(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_storage(monkeypatch)
    monkeypatch.setattr(storage, "upload_document", lambda file_storage, folder: (_ for _ in ()).throw(storage.UnsupportedFileType()))
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token, filename="photo.jpg", content_type="image/jpeg")
    assert response.status_code == 400


def test_document_extraction_failure_is_reported_before_any_upload(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    deleted = _mock_storage(monkeypatch)
    monkeypatch.setattr(document_extract, "extract_text", lambda data, content_type: (_ for _ in ()).throw(document_extract.DocumentExtractionError("no text found")))
    uploaded = []
    monkeypatch.setattr(storage, "upload_document", lambda file_storage, folder: uploaded.append(1) or ("url", "id"))
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token)
    assert response.status_code == 400
    assert "no text found" in response.get_json()["error"].lower()
    # Extraction is checked before ever calling out to Cloudinary - a scanned PDF with no
    # text never costs a real upload.
    assert uploaded == []
    assert deleted == []


def test_document_cap_is_enforced(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    for _ in range(business_documents.MAX_DOCUMENTS_PER_BUSINESS):
        response = _upload(client, csrf_token)
        assert response.status_code == 200

    response = _upload(client, csrf_token)
    assert response.status_code == 400
    assert "up to" in response.get_json()["error"].lower()


def test_delete_removes_row_and_cloud_asset(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    deleted = _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    document_id = _upload(client, csrf_token).get_json()["id"]

    delete_response = client.delete(
        f"/api/onboarding/document/{document_id}",
        headers={"Referer": "http://localhost/api/onboarding/document", "X-CSRFToken": csrf_token},
    )
    assert delete_response.status_code == 200
    assert database.business_documents_for_business(business_id) == []
    assert deleted == [(f"business/{business_id}/documents/doc-1", "raw")]


def test_cannot_delete_another_businesss_document(client, unique_username, unique_email, monkeypatch):
    other_username, other_email = f"{unique_username}-other", f"other-{unique_email}"
    register_business_web(client, other_username, other_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    document_id = _upload(client, csrf_token).get_json()["id"]
    client.get("/logout")

    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    response = client.delete(
        f"/api/onboarding/document/{document_id}",
        headers={"Referer": "http://localhost/api/onboarding/document", "X-CSRFToken": csrf_token},
    )
    assert response.status_code == 404


def test_document_upload_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = _upload(client, csrf_token)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_combined_document_text_joins_multiple_documents(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    _upload(client, csrf_token, filename="menu.pdf")
    _upload(client, csrf_token, filename="prices.pdf")

    combined = business_documents.combined_document_text(business_id)
    assert combined.count("Haircut: 20 EUR") == 2
    assert "---" in combined


def test_website_generation_uses_uploaded_document_as_extra_context(client, unique_username, unique_email, monkeypatch):
    """The whole point of the feature: generate_page_draft() must receive the extracted
    document text alongside the typed description when the onboarding "Generate my
    website" step runs."""
    import page_generator

    register_business_web(client, unique_username, unique_email)
    _mock_storage(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    _upload(client, csrf_token)

    captured = {}

    def fake_generate(description, document_text=None):
        captured["description"] = description
        captured["document_text"] = document_text
        return {
            "tagline": "t", "about": "a",
            "hours": {day: {"open": None, "close": None} for day in [
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            ]},
            "services": [], "faq": [],
        }

    import app as app_module
    monkeypatch.setattr(app_module, "generate_page_draft", fake_generate)
    # onboarding_website_describe() also calls ai_theme.generate_theme_options() (Faza 14) -
    # mocked off the network too, same reasoning as generate_page_draft above.
    monkeypatch.setattr(app_module.ai_theme, "generate_theme_options", lambda *args, **kwargs: [])

    response = web_post(client, "/onboarding/website", {
        "csrf_token": csrf_token, "description": "A barbershop downtown.",
    })
    assert response.status_code == 302
    assert captured["description"] == "A barbershop downtown."
    assert captured["document_text"] == "Haircut: 20 EUR\nOpen Mon-Fri 9-18"
