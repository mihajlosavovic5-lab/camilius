"""Unit tests for document_extract.py's PDF/DOCX text extraction - real (if minimal)
PDF/DOCX bytes are built in-memory so these run with no fixtures/network, same spirit as
test_storage.py's own validation-only tests."""
import io

import pytest
from docx import Document as DocxDocument

import document_extract


def _minimal_pdf_bytes(text):
    """Hand-built single-page PDF with one text-showing content stream - avoids pulling
    in a PDF-authoring library just for tests, since pypdf itself can only read PDFs, not
    write ones with real text content."""
    content = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        "/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    body = "%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body.encode("latin-1")))
        body += f"{index} 0 obj\n{obj}\nendobj\n"
    xref_offset = len(body.encode("latin-1"))
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n"
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    return (body + xref + trailer).encode("latin-1")


def _minimal_pdf_with_no_text():
    """A structurally valid PDF page with an empty content stream - stands in for a
    scanned/image-only PDF (nothing for pypdf to extract), without needing an actual
    scanned image fixture."""
    return _minimal_pdf_bytes("")


def _docx_bytes(paragraphs, table_rows=None):
    document = DocxDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row_values in enumerate(table_rows):
            for col_index, value in enumerate(row_values):
                table.cell(row_index, col_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- extract_text: PDF ---

def test_extract_text_reads_pdf_content():
    data = _minimal_pdf_bytes("Margherita Pizza 10 EUR")
    text = document_extract.extract_text(data, "application/pdf")
    assert "Margherita Pizza 10 EUR" in text


def test_extract_text_raises_for_pdf_with_no_text():
    data = _minimal_pdf_with_no_text()
    with pytest.raises(document_extract.DocumentExtractionError):
        document_extract.extract_text(data, "application/pdf")


def test_extract_text_raises_for_corrupt_pdf():
    with pytest.raises(document_extract.DocumentExtractionError):
        document_extract.extract_text(b"not a real pdf at all", "application/pdf")


# --- extract_text: DOCX ---

def test_extract_text_reads_docx_paragraphs():
    data = _docx_bytes(["Opening hours: Mon-Fri 9-18", "Haircut: 20 EUR"])
    text = document_extract.extract_text(
        data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Opening hours: Mon-Fri 9-18" in text
    assert "Haircut: 20 EUR" in text


def test_extract_text_reads_docx_tables():
    data = _docx_bytes([], table_rows=[["Service", "Price"], ["Haircut", "20 EUR"]])
    text = document_extract.extract_text(
        data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Haircut" in text and "20 EUR" in text


def test_extract_text_raises_for_corrupt_docx():
    with pytest.raises(document_extract.DocumentExtractionError):
        document_extract.extract_text(
            b"not a real docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


# --- extract_text: general ---

def test_extract_text_rejects_unsupported_content_type():
    with pytest.raises(document_extract.DocumentExtractionError):
        document_extract.extract_text(b"whatever", "image/jpeg")


def test_extract_text_truncates_to_max_length():
    long_paragraph = "x" * (document_extract.MAX_EXTRACTED_TEXT_LENGTH + 500)
    data = _docx_bytes([long_paragraph])
    text = document_extract.extract_text(
        data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert len(text) == document_extract.MAX_EXTRACTED_TEXT_LENGTH
