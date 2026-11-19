"""Reading papers for the Research Bank: flowing text (not the table layout used for valuation reports),
publisher cover pages removed, and the title / journal / year / abstract picked out."""
from __future__ import annotations

import io
import re
import unicodedata


def _nfkc(text: str) -> str:
    """'proﬁles' -> 'profiles' (PDF ligatures), full-width and other look-alike characters made plain."""
    return unicodedata.normalize("NFKC", text or "")

# ResearchGate / Academia / SSRN download pages and footers
_COVER_LINE = re.compile(
    r"see\s*discussions,?\s*stats,?\s*and\s*author\s*profiles|researchgate\.net/(?:publication|profile)|"
    r"all\s*content\s*following\s*this\s*page\s*was\s*uploaded|the\s*user\s*has\s*requested\s*enhancement|"
    r"^\s*see\s*profile\s*$|^\s*\d+\s*publications?\s+[\d,]+\s*citations?\s*$|\bauthors?,?\s*including:?\s*$|"
    r"^\s*(?:citations?|reads)\s*\d|^\s*\d[\d,]*\s*(?:citations?|reads)\s*$|some of the authors of this publication|"
    r"view project|^\s*project\s*$|downloaded from|academia\.edu|electronic copy available at|"
    r"https?://ssrn\.com|this article was downloaded by", re.I)
_PIPES = re.compile(r"(?:\s*\|){2,}\s*|\s+\|\s*$")


def clean_lines(text: str) -> list[str]:
    lines = []
    for ln in _nfkc(text).splitlines():
        ln = _PIPES.sub(" ", ln).replace("­", "").strip(" |\t")
        ln = re.sub(r"\s{2,}", " ", ln)
        if ln and not _COVER_LINE.search(ln):
            lines.append(ln)
    return lines


def join_paragraphs(lines: list[str]) -> str:
    """Re-join a PDF's broken lines into paragraphs; keep headings and list items on their own lines."""
    out: list[str] = []
    for ln in lines:
        if not out:
            out.append(ln)
            continue
        prev = out[-1]
        heading_like = len(ln) < 60 and not re.search(r"[.,;:]$", ln) and ln[:1].isupper() and len(prev) < 60
        starts_new = (re.match(r"^(?:[-•●▪*]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s", ln) or prev.endswith((".", "?", "!", ":"))
                      and (ln[:1].isupper() or ln[:1].isdigit()) or heading_like)
        if prev.endswith("-") and ln[:1].islower():
            out[-1] = prev[:-1] + ln                      # 'reinvest-' + 'ment'
        elif not starts_new and len(prev) > 40:
            out[-1] = prev + " " + ln
        else:
            out.append(ln)
    return "\n".join(out)


def _pages_text(data: bytes) -> tuple[list[str], dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    meta = {}
    try:
        info = reader.metadata or {}
        meta = {"title": (info.get("/Title") or "").strip(), "author": (info.get("/Author") or "").strip()}
    except Exception:
        pass
    pages = []
    for page in reader.pages:
        try:
            pages.append(_nfkc(page.extract_text() or ""))
        except Exception:
            pages.append("")
    return pages, meta


_COVER_MARKS = ("seediscussions,stats,andauthorprofiles", "seediscussions,statsandauthorprofiles",
                 "allcontentfollowingthispagewasuploaded", "researchgate.net/publication",
                 "theuserhasrequestedenhancementofthedownloadedfile")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", _nfkc(text)).lower()


def _is_cover(page: str) -> bool:
    """A download cover page (ResearchGate & co.), however the PDF spaced its words."""
    flat = _squash(page)
    return any(m in flat for m in _COVER_MARKS) and len(page) < 8000


def read_paper_pdf(data: bytes) -> tuple[str, dict]:
    """(text, details) where details may hold title, author, source (journal) and year."""
    pages, meta = _pages_text(data)
    details: dict = {}
    if pages and _is_cover(pages[0]):
        cover = pages[0]
        m = re.search(r"(?i)(?:article|chapter|conference\s*paper|thesis|book|preprint|technical\s*report|working\s*paper)"
                      r"\s*in\s*(.+?)\s*[·•|]\s*(?:[A-Z][a-z]+\s*)?((?:19|20)\d{2})", cover)
        if m:
            details["source"] = " ".join(m.group(1).split())[:200]
            details["year"] = m.group(2)
        # the title is the line after the ResearchGate link
        lines = [ln.strip() for ln in cover.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if "researchgate.net/publication" in ln.lower().replace(" ", ""):
                after = re.split(r"(?i)/\s*publication\s*/", ln, maxsplit=1)[-1]
                rest = re.sub(r"^\s*\d+\S*\s*", "", after).strip()
                candidate = rest or (lines[i + 1] if i + 1 < len(lines) else "")
                candidate = re.sub(r"(?i)\s*(?:article|chapter|conference paper|thesis|preprint)\s*in\b.*$", "", candidate).strip()
                if 5 <= len(candidate) <= 250 and not _COVER_LINE.search(candidate):
                    details["title"] = candidate
                break
        cover_names = authors_from_cover(cover)
        pages = pages[1:]
    else:
        cover_names = []
    text = join_paragraphs(clean_lines("\n".join(pages)))
    # the paper's own author line lists everyone; a ResearchGate cover shows only some ("authors, including")
    names = authors_from_text(text)
    if len(cover_names) > len(names):
        names = cover_names
    if names:
        details["authors"] = names
    title = meta.get("title") or ""
    if title and 8 <= len(title) <= 250 and not re.match(r"(?i)microsoft word|untitled|^doc\d*$|\.(?:docx?|pdf)$", title):
        details.setdefault("title", title)
    if meta.get("author") and not re.match(r"(?i)^(?:user|admin|owner|author|hp|dell|lenovo)\b", meta["author"]):
        details["author"] = meta["author"][:200]
    if "year" not in details:
        m = re.search(r"\b((?:19|20)\d{2})\b", text[:1500])
        if m:
            details["year"] = m.group(1)
    abstract = abstract_of(text)
    if abstract:
        details["abstract"] = abstract
    words = keywords_of(text)
    if words:
        details["keywords"] = words
    return text, details


_NAME = r"[A-Z][A-Za-z'’\-]+(?:\s+[A-Z]\.?)*(?:\s+[A-Z][A-Za-z'’\-]+){1,3}"
_NOT_NAME = re.compile(r"(?i)universit|college|institute|school|department|faculty|centre|center|ltd|limited|"
                       r"publications?|citations?|reads|profile|journal|article|project|abstract|introduction|keywords|"
                       r"see\b|view|ghana|accra|kumasi|sydney|london|business|finance|property|valuation|approach|"
                       r"\d")


def _names(chunk: str) -> list[str]:
    out = []
    for part in re.split(r"\s*(?:,|;|&|\band\b)\s*", chunk or ""):
        part = re.sub(r"[\d*†‡§¹²³⁴]+$", "", part.strip()).strip()
        if re.fullmatch(_NAME, part) and not _NOT_NAME.search(part) and part not in out:
            out.append(part)
    return out


def authors_from_cover(cover: str) -> list[str]:
    """ResearchGate cover: each author's name sits above their affiliation and 'N PUBLICATIONS N CITATIONS'."""
    lines = [ln.strip() for ln in _nfkc(cover).splitlines() if ln.strip()]
    names = []
    for i, ln in enumerate(lines):
        if re.search(r"(?i)\d+\s*publications?", ln):
            for back in (2, 1, 3):
                if i - back >= 0:
                    found = _names(lines[i - back])
                    if found:
                        names += [n for n in found if n not in names]
                        break
    m = re.search(r"(?is)authors?,?\s*including:?\s*\n(.+?)\n", _nfkc(cover))
    if not names and m:
        names = _names(m.group(1))
    return names[:8]


def authors_from_text(text: str) -> list[str]:
    """'Authors: Nelson Chan and Norman Hacker', 'By A. Mensah, K. Boateng' or a name line under the title."""
    head = (text or "")[:3000]
    m = re.search(r"(?im)^\s*(?:authors?|by|written by|prepared by)\s*[:\-]?\s*(.+)$", head)
    if m:
        chunk = re.split(r"(?i)\s*(?:affiliation|university|department|email|e-mail)\b", m.group(1))[0]
        names = _names(chunk)
        if names:
            return names[:8]
    for ln in head.splitlines()[1:8]:          # a line of names just under the title
        names = _names(ln)
        if names and len(" ".join(names)) >= len(ln.strip()) * 0.6:
            return names[:8]
    return []


def keywords_of(text: str) -> list[str]:
    """The paper's own 'Keywords:' line."""
    m = re.search(r"(?im)^\s*key\s*-?\s*words?\s*[:.\-–]\s*(.+)$", (text or "")[:20000])
    if not m:
        return []
    words = [w.strip(" .;").lower() for w in re.split(r"[,;·•]|\s{2,}", m.group(1)) if 1 < len(w.strip(" .;")) <= 40]
    return list(dict.fromkeys(words))[:10]


def abstract_of(text: str) -> str | None:
    """The abstract (or summary) section, without the heading; stops at keywords / introduction."""
    m = re.search(r"(?is)(?:^|\n)\s*(?:abstract|summary|executive summary)\s*[:.\-–]?\s*\n?(.{80,2500}?)"
                  r"(?=\n\s*(?:key ?words?|jel|introduction|1\.?\s+introduction|background|\d\.?\s+[A-Z])\b|\Z)", text)
    if not m:
        return None
    body = " ".join(m.group(1).split())
    return (body[:1200].rsplit(" ", 1)[0] + "…") if len(body) > 1200 else body
