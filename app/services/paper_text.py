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
        pages = pages[1:]
    text = join_paragraphs(clean_lines("\n".join(pages)))
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
    return text, details


def abstract_of(text: str) -> str | None:
    """The abstract (or summary) section, without the heading; stops at keywords / introduction."""
    m = re.search(r"(?is)(?:^|\n)\s*(?:abstract|summary|executive summary)\s*[:.\-–]?\s*\n?(.{80,2500}?)"
                  r"(?=\n\s*(?:key ?words?|jel|introduction|1\.?\s+introduction|background|\d\.?\s+[A-Z])\b|\Z)", text)
    if not m:
        return None
    body = " ".join(m.group(1).split())
    return (body[:1200].rsplit(" ", 1)[0] + "…") if len(body) > 1200 else body
