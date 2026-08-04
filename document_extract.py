"""Extracts plain text from a business-uploaded source document (PDF or Word) so it can
be handed to page_generator.py as extra factual context alongside the owner's typed
description - e.g. a restaurant's menu PDF or an existing brochure. In-memory only, same
DB-agnostic spirit as storage.py/page_generator.py - this module never touches the
database or Cloudinary.

Known limitation: a scanned PDF (a photographed/scanned page with no real text layer)
has nothing for pypdf to extract - OCR is not implemented here. That surfaces to the
caller as the same "no text found" error as an empty file, with a hint to type the
details instead; a future version could add an OCR fallback (e.g. pytesseract) for that
case without changing this module's public interface.
"""
import io

from docx import Document
from pypdf import PdfReader

# Keeps a single document's contribution to the generation prompt bounded - a large PDF
# menu/brochure could otherwise blow up token cost or hit the model's context limit for
# what's meant to be a one-time page draft. page_generator.py applies its own cap too
# (defense in depth, same pattern as MAX_VOICE_AUDIO_BYTES), but capping at the source
# means the (larger) uncapped text is never even stored in the database.
MAX_EXTRACTED_TEXT_LENGTH = 8000

# Keyed by the exact content type storage.ALLOWED_DOCUMENT_CONTENT_TYPES accepts, so the
# two modules can never disagree about what document types the feature supports.
_CONTENT_TYPE_KINDS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


class DocumentExtractionError(Exception):
    """Raised for an unsupported type, a corrupt/unreadable file, or a file with no
    extractable text (e.g. a scanned PDF) - callers turn this into a user-facing error,
    same contract as page_generator.py's ValueError."""


def extract_text(file_bytes, content_type):
    """file_bytes: the raw uploaded file. content_type: its MIME type as reported by the
    browser (e.g. "application/pdf"). Returns extracted text, capped to
    MAX_EXTRACTED_TEXT_LENGTH. Raises DocumentExtractionError if nothing usable could be
    pulled out of it."""
    kind = _CONTENT_TYPE_KINDS.get((content_type or "").lower())
    if kind == "pdf":
        text = _extract_pdf(file_bytes)
    elif kind == "docx":
        text = _extract_docx(file_bytes)
    else:
        raise DocumentExtractionError(f"Unsupported document type: {content_type or 'unknown'}")

    text = text.strip()
    if not text:
        raise DocumentExtractionError(
            "Couldn't find any text in that file - if it's a scanned document or "
            "image-only PDF, try typing the details instead."
        )
    return text[:MAX_EXTRACTED_TEXT_LENGTH]


def _extract_pdf(file_bytes):
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise DocumentExtractionError(f"Couldn't read that PDF: {error}") from error


def _extract_docx(file_bytes):
    try:
        document = Document(io.BytesIO(file_bytes))
    except Exception as error:
        raise DocumentExtractionError(f"Couldn't read that Word document: {error}") from error

    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)
