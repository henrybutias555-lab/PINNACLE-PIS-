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

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    parts = []
    # Walk the body in order so a table stays under its heading (e.g. "Comparable evidence").
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            parts.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells, seen = [], set()
                for cell in row.cells:  # merged cells repeat; keep each once
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" ".join(cell.text.split()))
                parts.append(" | ".join(cells))
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
