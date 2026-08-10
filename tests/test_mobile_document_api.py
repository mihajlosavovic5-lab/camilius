"""Tests for the mobile app's Bearer-authed /api/documents routes (api.py) - the same
business_documents.py rules app.py's session-authed /api/onboarding/document exercises in
tests/test_business_documents.py, just reached the way the mobile app reaches everything
else. storage.upload_document/delete_file and document_extract.extract_text are
monkeypatched to avoid any real Cloudinary/network call."""
import io

import database
import storage
import document_extract
from conftest import register_and_login


def _mock_storage(monkeypatch):
    counter = {"n": 0}

    def upload(file_storage, folder):
        counter["n"] += 1
        return f"https://res.cloudinary.com/demo/doc-{counter['n']}.pdf", f"{folder}/doc-{counter['n']}"

    monkeypatch.setattr(storage, "upload_document", upload)
    deleted = []
    monkeypatch.setattr(storage, "delete_file", lambda public_id, resource_type="image": deleted.append((public_id, resource_type)))
    monkeypatch.setattr(document_extract, "extract_text", lambda data, content_type: "Open Mon-Fri 9-18")
    return deleted


def _upload(client, headers, filename="menu.pdf", content_type="application/pdf"):
    return client.post(
        "/api/documents",
        data={"file": (io.BytesIO(b"fake bytes"), filename, content_type)},
        content_type="multipart/form-data",
        headers=headers,
    )


def test_mobile_upload_list_and_delete(client, unique_email, monkeypatch):
    token, business_id = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)

    upload_response = _upload(client, headers)
    assert upload_response.status_code == 200
    document_id = upload_response.get_json()["id"]

    list_response = client.get("/api/documents", headers=headers)
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.get_json()["documents"]] == [document_id]

    delete_response = client.delete(f"/api/documents/{document_id}", headers=headers)
    assert delete_response.status_code == 200
    assert database.business_documents_for_business(business_id) == []


def test_mobile_documents_requires_auth(client):
    response = client.get("/api/documents")
    assert response.status_code == 401


def test_mobile_upload_rejects_no_file(client, unique_email, monkeypatch):
    token, _ = register_and_login(client, unique_email)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_storage(monkeypatch)

    response = client.post("/api/documents", data={}, content_type="multipart/form-data", headers=headers)
    assert response.status_code == 400
