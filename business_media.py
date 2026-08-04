"""Shared business logic behind uploading/replacing/deleting a business's website media
(logo, gallery photos, hero video) - both the web onboarding wizard's session-authed
route (app.py's /api/onboarding/media, used by the browser and settings.html) and the
mobile app's Bearer-authed route (api.py's /api/media) call these instead of each
re-implementing the same upload/replace/delete rules against database.py and storage.py.
"""
from database import (
    add_business_media, business_media_for_business, business_media_by_role,
    business_media_by_id, delete_business_media,
)
import storage

# 'logo' and 'hero_video' are single-slot roles - a new upload replaces whatever was
# there before (cloud asset included, see upload_media below). 'gallery' is many-slot,
# capped at MAX_GALLERY_IMAGES.
ROLES = {"logo", "gallery", "hero_video"}
MAX_GALLERY_IMAGES = 12


class MediaError(Exception):
    """Carries an HTTP status alongside the message so both app.py's and api.py's route
    handlers can turn any failure into the right response with one except block."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _folder(business_id, role):
    return f"business/{business_id}/{role}"


def upload_media(business_id, role, file_storage):
    if role not in ROLES:
        raise MediaError("Unknown media type.")
    if file_storage is None or not file_storage.filename:
        raise MediaError("No file received.")

    # Checked before ever calling out to Cloudinary - an over-the-cap upload would
    # otherwise cost a real upload just to be deleted again a moment later.
    existing = business_media_by_role(business_id, role) if role == "gallery" else None
    if existing is not None and len(existing) >= MAX_GALLERY_IMAGES:
        raise MediaError(f"You can upload up to {MAX_GALLERY_IMAGES} gallery photos - delete one first.")

    upload = storage.upload_video if role == "hero_video" else storage.upload_image
    media_type = "video" if role == "hero_video" else "image"
    try:
        url, public_id = upload(file_storage, _folder(business_id, role))
    except storage.UnsupportedFileType:
        message = "Video must be MP4 or MOV." if role == "hero_video" else "Image must be JPG, PNG, or WEBP."
        raise MediaError(message)
    except storage.FileTooLarge:
        limit_mb = (storage.MAX_VIDEO_BYTES if role == "hero_video" else storage.MAX_IMAGE_BYTES) // (1024 * 1024)
        raise MediaError(f"File exceeds the {limit_mb}MB limit.")
    except storage.StorageNotConfigured:
        raise MediaError("Media storage isn't configured yet - contact support.", status=503)

    if role == "gallery":
        position = len(existing)
    else:
        for row in business_media_by_role(business_id, role):
            storage.delete_file(row[3], resource_type=row[4])
            delete_business_media(row[0])
        position = 0

    media_id = add_business_media(business_id, url, public_id, media_type, role, position)
    return {"id": media_id, "url": url, "type": media_type, "role": role}


def delete_media(business_id, media_id):
    """Raises MediaError(status=404) both for a nonexistent id and for one owned by a
    different business - a caller can't distinguish "doesn't exist" from "not yours"."""
    row = business_media_by_id(media_id)
    if row is None or row[1] != business_id:
        raise MediaError("Not found.", status=404)
    storage.delete_file(row[3], resource_type=row[4])
    delete_business_media(media_id)


def list_media(business_id):
    return [
        {"id": row[0], "url": row[2], "type": row[4], "role": row[5]}
        for row in business_media_for_business(business_id)
    ]


def media_for_page(business_id):
    """Grouped shape the public page template wants: a logo URL (or None), an ordered
    list of gallery URLs, and a hero video URL (or None)."""
    logo_url = None
    gallery_urls = []
    hero_video_url = None
    for row in business_media_for_business(business_id):
        role = row[5]
        if role == "logo":
            logo_url = row[2]
        elif role == "gallery":
            gallery_urls.append(row[2])
        elif role == "hero_video":
            hero_video_url = row[2]
    return {"logo_url": logo_url, "gallery_urls": gallery_urls, "hero_video_url": hero_video_url}
