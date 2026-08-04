"""Reusable object-storage helper backed by Cloudinary - business-uploaded files never
touch local disk (Railway/Heroku-style deploys have an ephemeral filesystem, so anything
written there vanishes on the next deploy/restart). Shared by the website media upload
(app.py's /api/onboarding/media, api.py's /api/media) and the source-document upload
(app.py's /api/onboarding/document, api.py's /api/documents, see business_documents.py)
rather than duplicating the upload/validate/delete logic against a second Cloudinary
integration.

DB-agnostic like generate_reply.py/page_generator.py - this module never touches the
database; callers persist whatever (url, storage_key) it hands back.
"""
import io
import os
import uuid

import cloudinary
import cloudinary.uploader

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

# Cloudinary's own resource_type ("image"/"video") is derived from these, not from the
# uploaded filename - keeps validation and the actual upload call from ever disagreeing
# about what kind of file this is.
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime"}
ALLOWED_DOCUMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}


class StorageError(Exception):
    """Base for every error this module raises - callers can catch this alone to turn
    any storage failure into a 400/503 without needing to know the specific subclass."""


class StorageNotConfigured(StorageError):
    pass


class UnsupportedFileType(StorageError):
    pass


class FileTooLarge(StorageError):
    pass


_configured = False


def _ensure_configured():
    global _configured
    if _configured:
        return
    # cloudinary.config() with no args already reads CLOUDINARY_URL from the environment
    # on its own, but calling it lazily (here, not at import time) means a deploy without
    # CLOUDINARY_URL set still boots fine - it only fails the specific request that tries
    # to upload/delete something, same as every other optional-integration check in this
    # codebase (e.g. paddle_billing.is_configured()).
    if not os.environ.get("CLOUDINARY_URL"):
        raise StorageNotConfigured("CLOUDINARY_URL is not set.")
    cloudinary.config(cloudinary_url=os.environ["CLOUDINARY_URL"])
    _configured = True


def _upload(file_storage, *, folder, resource_type, max_bytes, allowed_content_types):
    content_type = (getattr(file_storage, "mimetype", "") or "").lower()
    if content_type not in allowed_content_types:
        raise UnsupportedFileType(f"Unsupported file type: {content_type or 'unknown'}")

    # Read one byte past the cap so an oversized upload is caught here instead of being
    # silently truncated mid-file and uploaded as a corrupt/partial asset - same pattern
    # as app.py's onboarding_voice_transcribe route.
    data = file_storage.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)}MB limit.")

    _ensure_configured()
    public_id = f"{folder}/{uuid.uuid4().hex}"
    result = cloudinary.uploader.upload(
        io.BytesIO(data),
        public_id=public_id,
        resource_type=resource_type,
        overwrite=False,
        unique_filename=False,
    )
    return result["secure_url"], result["public_id"]


def upload_image(file_storage, folder):
    """Uploads a single image (jpg/png/webp, up to MAX_IMAGE_BYTES) under `folder` (e.g.
    "business/<id>/gallery"). Returns (secure_url, public_id) - public_id is what
    delete_file() needs later to remove it. Raises UnsupportedFileType/FileTooLarge/
    StorageNotConfigured; callers turn those into a 400/503 response."""
    return _upload(
        file_storage, folder=folder, resource_type="image",
        max_bytes=MAX_IMAGE_BYTES, allowed_content_types=ALLOWED_IMAGE_CONTENT_TYPES,
    )


def upload_video(file_storage, folder):
    """Same as upload_image but for mp4/mov, up to MAX_VIDEO_BYTES."""
    return _upload(
        file_storage, folder=folder, resource_type="video",
        max_bytes=MAX_VIDEO_BYTES, allowed_content_types=ALLOWED_VIDEO_CONTENT_TYPES,
    )


def upload_document(file_storage, folder):
    """Same as upload_image but for a PDF/Word source document (up to MAX_DOCUMENT_BYTES),
    uploaded as Cloudinary's "raw" resource type since it's neither an image nor a video.
    Callers must pass resource_type="raw" to delete_file() to remove it again."""
    return _upload(
        file_storage, folder=folder, resource_type="raw",
        max_bytes=MAX_DOCUMENT_BYTES, allowed_content_types=ALLOWED_DOCUMENT_CONTENT_TYPES,
    )


def delete_file(public_id, resource_type="image"):
    """Best-effort delete of a previously uploaded asset (e.g. the old logo when a
    business replaces it). Never raises on a Cloudinary-side failure - a stray orphaned
    asset costs a few cents of storage; blocking the business's upload/replace action on
    a cleanup call that failed would be a much worse trade."""
    try:
        _ensure_configured()
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except StorageError:
        pass
    except Exception:
        pass
