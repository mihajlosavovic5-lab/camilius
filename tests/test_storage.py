"""Unit tests for storage.py's own validation (content type / size caps) - the parts that
run before ever calling out to Cloudinary, so these need no network access or a real
CLOUDINARY_URL. The actual upload/delete calls are exercised indirectly through
test_business_media.py, which mocks storage.upload_image/upload_video/delete_file the
same way test_onboarding_wizard.py mocks the OpenAI call."""
import io

import pytest

import storage


class _FakeFileStorage:
    def __init__(self, data, mimetype):
        self._stream = io.BytesIO(data)
        self.filename = "upload.bin"
        self.mimetype = mimetype

    def read(self, size=-1):
        return self._stream.read(size)


def test_upload_image_rejects_unsupported_content_type():
    file_storage = _FakeFileStorage(b"not really an image", "application/pdf")
    with pytest.raises(storage.UnsupportedFileType):
        storage.upload_image(file_storage, "business/1/logo")


def test_upload_image_rejects_oversized_file():
    file_storage = _FakeFileStorage(b"x" * (storage.MAX_IMAGE_BYTES + 1), "image/jpeg")
    with pytest.raises(storage.FileTooLarge):
        storage.upload_image(file_storage, "business/1/logo")


def test_upload_video_rejects_unsupported_content_type():
    file_storage = _FakeFileStorage(b"not really a video", "video/avi")
    with pytest.raises(storage.UnsupportedFileType):
        storage.upload_video(file_storage, "business/1/hero_video")


def test_upload_video_rejects_oversized_file():
    file_storage = _FakeFileStorage(b"x" * (storage.MAX_VIDEO_BYTES + 1), "video/mp4")
    with pytest.raises(storage.FileTooLarge):
        storage.upload_video(file_storage, "business/1/hero_video")


def test_upload_document_rejects_unsupported_content_type():
    file_storage = _FakeFileStorage(b"not really a document", "image/jpeg")
    with pytest.raises(storage.UnsupportedFileType):
        storage.upload_document(file_storage, "business/1/documents")


def test_upload_document_rejects_oversized_file():
    file_storage = _FakeFileStorage(b"x" * (storage.MAX_DOCUMENT_BYTES + 1), "application/pdf")
    with pytest.raises(storage.FileTooLarge):
        storage.upload_document(file_storage, "business/1/documents")


def test_upload_document_accepts_docx_content_type(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    storage._configured = False
    file_storage = _FakeFileStorage(
        b"a real-looking docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    # No CLOUDINARY_URL in tests - this should get past content-type/size validation and
    # fail on StorageNotConfigured, not UnsupportedFileType, proving .docx is accepted.
    with pytest.raises(storage.StorageNotConfigured):
        storage.upload_document(file_storage, "business/1/documents")


def test_upload_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    storage._configured = False
    file_storage = _FakeFileStorage(b"a real-looking jpeg", "image/jpeg")
    with pytest.raises(storage.StorageNotConfigured):
        storage.upload_image(file_storage, "business/1/logo")


def test_delete_file_never_raises_even_when_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    storage._configured = False
    storage.delete_file("business/1/logo/whatever", resource_type="image")  # must not raise
