"""Pull plain text out of uploaded PDF / Word / text / image files."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "txt", ".csv": "txt",
             ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image", ".tiff": "image",
             ".webp": "image"}


def file_type_for(filename: str) -> str | None:
    return SUPPORTED.get(Path(filename).suffix.lower())


def extract_text(path: Path, file_type: str) -> str:
    if file_type == "pdf":
        text = _pdf_text(path)
        if len(text.strip()) < 40:  # likely a scanned PDF
            text = (_ocr_pdf(path) or text)
        return text
    if file_type == "docx":
        return _docx_text(path)
    if file_type == "txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if file_type == "image":
        return _ocr_image(path) or ""
    return ""


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # corrupt or encrypted PDF
        log.warning("PDF text extraction failed for %s: %s", path, exc)
        return ""


def _docx_text(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _ocr_image(path: Path) -> str | None:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    try:
        return pytesseract.image_to_string(Image.open(path))
    except Exception as exc:
        log.warning("OCR failed for %s: %s", path, exc)
        return None


def _ocr_pdf(path: Path) -> str | None:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        return None
    try:
        return "\n".join(pytesseract.image_to_string(img) for img in convert_from_path(str(path)))
    except Exception as exc:
        log.warning("PDF OCR failed for %s: %s", path, exc)
        return None
