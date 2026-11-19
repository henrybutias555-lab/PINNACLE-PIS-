"""Pull plain text out of uploaded PDF / Word / text / image files."""
from __future__ import annotations

import logging
import re
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
    except Exception as exc:  # corrupt or encrypted PDF
        log.warning("PDF text extraction failed for %s: %s", path, exc)
        return ""
    try:
        # Layout mode keeps table columns lined up, so tables can be rebuilt.
        layout = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
        if layout.strip():
            return tidy_pdf_text(layout)
    except Exception as exc:
        log.info("PDF layout extraction unavailable for %s (%s); using plain text", path, exc)
    try:
        return tidy_pdf_text("\n".join((page.extract_text() or "") for page in reader.pages))
    except Exception as exc:
        log.warning("PDF text extraction failed for %s: %s", path, exc)
        return ""


_GAP = re.compile(r"\S(?: \S)*")  # a run of text where words are separated by single spaces


def _cells(line: str) -> list[tuple[int, str]]:
    """[(column, text)] for a layout line, splitting where there are 3+ spaces."""
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: {1,2}\S+)*", line)]


def _is_table_line(line: str) -> bool:
    return len(_cells(line)) >= 3 and bool(re.search(r"\S {3,}\S", line))


def tidy_pdf_text(raw: str) -> str:
    """Rebuild tables as 'a | b | c' rows and rejoin sentences broken across lines."""
    from .reader import _is_heading

    lines = [ln.rstrip() for ln in raw.splitlines()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_table_line(line) and i + 1 < len(lines):
            header = _cells(line)
            starts = [c for c, _ in header]
            rows: list[list[str]] = [[t for _, t in header]]
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if not ln.strip():
                    # a blank line inside a table is fine if a table row follows
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines) and (_is_table_line(lines[k]) or (lines[k][:max(starts[1] - 1, 1)].strip() == ""
                                                                         and lines[k].strip())):
                        j = k
                        continue
                    break
                cells = _cells(ln)
                if not cells:
                    break
                row = [""] * len(starts)
                for col, text in cells:
                    idx = max((n for n, s in enumerate(starts) if s <= col + 2), default=0)
                    row[idx] = f"{row[idx]} {text}".strip()
                if row[0] or _is_table_line(ln):
                    rows.append(row)            # a new row
                elif len(rows) > 1:             # wrapped text of the previous row's cells
                    rows[-1] = [f"{a} {b}".strip() for a, b in zip(rows[-1], row)]
                else:
                    break
                j += 1
            if len(rows) >= 2:
                out.extend(" | ".join(r) for r in rows)
                i = j
                continue
        out.append(line.strip())
        i += 1

    # Rejoin paragraph lines: a line that doesn't end a sentence continues on the next line.
    joined: list[str] = []
    for line in out:
        prev = joined[-1] if joined else ""
        if (line and prev and "|" not in line and "|" not in prev and not _is_heading(line) and not _is_heading(prev)
                and not re.search(r"[.:;!?]$", prev) and not re.match(r"(?:\d{1,2}[.)]|[•\-–*])\s", line)):
            joined[-1] = f"{prev} {line}"
        else:
            joined.append(line)
    return "\n".join(ln for ln in joined if ln.strip())


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
