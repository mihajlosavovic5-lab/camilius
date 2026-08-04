"""Shared business logic behind uploading/listing/deleting a business's source documents
(PDF/Word) used as extra factual context for AI page generation - both the web onboarding
wizard's session-authed route (app.py's /api/onboarding/document) and the mobile app's
Bearer-authed route (api.py's /api/documents) call these instead of each re-implementing
the same upload/extract/delete rules against database.py, document_extract.py and
storage.py. Mirrors business_media.py's own split for the same reason.
"""
import io

from database import (
    add_business_document, business_documents_for_business, business_document_by_id,
    delete_business_document,
)
import document_extract
import storage

# Keeps a business from piling up dozens of documents that all get concatenated into
# every future page-draft generation - 5 is plenty for a menu, a price list, and a
# couple of brochures.
MAX_DOCUMENTS_PER_BUSINESS = 5


class DocumentError(Exception):
    """Carries an HTTP status alongside the message so both app.py's and api.py's route
    handlers can turn any failure into the right response with one except block."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class _BytesFileStorage:
    """Minimal FileStorage-alike (the only interface storage.py's _upload() needs:
    .mimetype and .read(size)) wrapping bytes already read out of the real upload once -
    lets upload_document() below extract text and upload to Cloudinary from the same
    in-memory copy instead of reading the original file_storage stream twice (its second
    read would come back empty, since the first .read() already exhausted it)."""

    def __init__(self, data, mimetype):
        self._buffer = io.BytesIO(data)
        self.mimetype = mimetype

    def read(self, *args, **kwargs):
        return self._buffer.read(*args, **kwargs)


def _folder(business_id):
    return f"business/{business_id}/documents"


def upload_document(business_id, file_storage):
    if file_storage is None or not file_storage.filename:
        raise DocumentError("No file received.")

    if len(business_documents_for_business(business_id)) >= MAX_DOCUMENTS_PER_BUSINESS:
        raise DocumentError(f"You can upload up to {MAX_DOCUMENTS_PER_BUSINESS} documents - delete one first.")

    content_type = (getattr(file_storage, "mimetype", "") or "").lower()

    # Read once into memory - both the extraction below and the Cloudinary upload after
    # it need the full file, and file_storage.read() can only be drained once.
    data = file_storage.read(storage.MAX_DOCUMENT_BYTES + 1)
    if len(data) > storage.MAX_DOCUMENT_BYTES:
        limit_mb = storage.MAX_DOCUMENT_BYTES // (1024 * 1024)
        raise DocumentError(f"File exceeds the {limit_mb}MB limit.")

    try:
        extracted_text = document_extract.extract_text(data, content_type)
    except document_extract.DocumentExtractionError as error:
        raise DocumentError(str(error))

    try:
        url, public_id = storage.upload_document(_BytesFileStorage(data, content_type), _folder(business_id))
    except storage.UnsupportedFileType:
        raise DocumentError("File must be a PDF or Word (.docx) document.")
    except storage.StorageNotConfigured:
        raise DocumentError("Document storage isn't configured yet - contact support.", status=503)

    document_type = "pdf" if content_type == "application/pdf" else "docx"
    document_id = add_business_document(
        business_id, url, public_id, document_type, file_storage.filename, extracted_text,
    )
    return {"id": document_id, "url": url, "filename": file_storage.filename, "type": document_type}


def delete_document(business_id, document_id):
    """Raises DocumentError(status=404) both for a nonexistent id and for one owned by a
    different business - a caller can't distinguish "doesn't exist" from "not yours"."""
    row = business_document_by_id(document_id)
    if row is None or row[1] != business_id:
        raise DocumentError("Not found.", status=404)
    storage.delete_file(row[3], resource_type="raw")
    delete_business_document(document_id)


def list_documents(business_id):
    return [
        {"id": row[0], "url": row[2], "filename": row[5], "type": row[4]}
        for row in business_documents_for_business(business_id)
    ]


def combined_document_text(business_id):
    """Concatenated extracted text from every document this business has uploaded, ready
    to hand to page_generator.generate_page_draft() as document_text. Empty string if
    none have been uploaded - callers don't need to special-case that, generate_page_draft
    treats a blank document_text exactly like the argument being omitted."""
    texts = [row[6] for row in business_documents_for_business(business_id) if row[6]]
    return "\n\n---\n\n".join(texts)
