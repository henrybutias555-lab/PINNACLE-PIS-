"""Show a Research Bank document in (close to) its original form: Word with its headings, bold, lists and
tables; Excel sheets as tables; PowerPoint slide by slide. PDFs and images are shown as they are by the page."""
from __future__ import annotations

import html
import io

_E = html.escape


def _runs(paragraph) -> str:
    out = []
    for run in paragraph.runs:
        text = _E(run.text or "")
        if not text:
            continue
        if run.bold:
            text = f"<b>{text}</b>"
        if run.italic:
            text = f"<i>{text}</i>"
        if run.underline:
            text = f"<u>{text}</u>"
        out.append(text)
    return "".join(out) or _E(paragraph.text or "")


def docx_html(data: bytes) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    parts, in_list = [], None
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            style = (p.style.name if p.style is not None else "") or ""
            body = _runs(p)
            numbered = child.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr") is not None
            is_list = numbered or style.lower().startswith("list")
            if is_list:
                kind = "ol" if "number" in style.lower() else "ul"
                if in_list != kind:
                    if in_list:
                        parts.append(f"</{in_list}>")
                    parts.append(f"<{kind}>")
                    in_list = kind
                parts.append(f"<li>{body}</li>")
                continue
            if in_list:
                parts.append(f"</{in_list}>")
                in_list = None
            if not (p.text or "").strip():
                continue
            low = style.lower()
            if low == "title":
                parts.append(f"<h2>{body}</h2>")
            elif low.startswith("heading"):
                level = next((int(c) for c in low if c.isdigit()), 2)
                parts.append(f"<h{min(level + 2, 6)}>{body}</h{min(level + 2, 6)}>")
            else:
                align = {1: "center", 2: "right", 3: "justify"}.get(int(p.alignment) if p.alignment is not None else 0)
                style_attr = ' style="text-align:%s"' % align if align else ""
                parts.append(f"<p{style_attr}>{body}</p>")
        elif tag == "tbl":
            if in_list:
                parts.append(f"</{in_list}>")
                in_list = None
            t = Table(child, d)
            rows = []
            for i, row in enumerate(t.rows):
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) in seen:        # merged cells repeat
                        continue
                    seen.add(id(cell._tc))
                    content = "<br>".join(_E(par.text) for par in cell.paragraphs if par.text.strip())
                    cells.append(f"<{'th' if i == 0 else 'td'}>{content}</{'th' if i == 0 else 'td'}>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            parts.append('<div class="table-wrap"><table class="doc-table">' + "".join(rows) + "</table></div>")
    if in_list:
        parts.append(f"</{in_list}>")
    return "\n".join(parts)


def xlsx_html(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 2000:
                rows.append("<tr><td>… (first 2,000 rows shown)</td></tr>")
                break
            if not any(c not in (None, "") for c in row):
                continue
            cell = "th" if not rows else "td"
            rows.append("<tr>" + "".join(f"<{cell}>{_E('' if c is None else str(c))}</{cell}>" for c in row) + "</tr>")
        parts.append(f"<h4>{_E(ws.title)}</h4><div class=\"table-wrap\"><table class=\"doc-table\">{''.join(rows)}</table></div>")
    return "\n".join(parts)


def pptx_html(data: bytes) -> str | None:
    try:
        from pptx import Presentation
    except ImportError:
        return None
    parts = []
    for i, slide in enumerate(Presentation(io.BytesIO(data)).slides, start=1):
        texts = [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
        body = "".join(f"<p>{_E(t).replace(chr(10), '<br>')}</p>" for t in texts)
        parts.append(f'<section class="slide"><h4>Slide {i}</h4>{body}</section>')
    return "\n".join(parts)


def view(doc) -> dict:
    """{'kind': 'pdf' | 'image' | 'html' | 'text', 'html'?}"""
    ftype = doc.file_type
    if ftype == "pdf":
        return {"kind": "pdf"}
    if ftype == "image":
        return {"kind": "image"}
    try:
        if ftype == "docx":
            return {"kind": "html", "html": docx_html(doc.file_data)}
        if ftype == "xlsx":
            return {"kind": "html", "html": xlsx_html(doc.file_data)}
        if ftype == "pptx":
            out = pptx_html(doc.file_data)
            if out:
                return {"kind": "html", "html": out}
    except Exception:
        pass
    return {"kind": "text"}
