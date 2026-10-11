"""Document parsing: pages (with tables and OCR fallback), sections, injection hygiene, chunking."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------- pages
@dataclass
class Page:
    number: int
    text: str
    ocr: bool = False
    tables: int = 0


def _ocr_page(pix_png: bytes) -> str:
    exe = shutil.which("tesseract") or (r"C:\Program Files\Tesseract-OCR\tesseract.exe" if Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists() else None)
    if not exe:
        return ""
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "p.png"
        img.write_bytes(pix_png)
        try:
            out = subprocess.run([exe, str(img), "stdout"], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout


def _pdf_pages(data: bytes) -> list[Page]:
    import fitz

    pages: list[Page] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for i, page in enumerate(pdf, start=1):
            tables = []
            try:
                tables = list(page.find_tables().tables)
            except Exception:  # find_tables is best-effort across PyMuPDF versions
                tables = []
            table_boxes = [fitz.Rect(t.bbox) for t in tables]
            parts = []
            for x0, y0, x1, y1, txt, *_ in page.get_text("blocks"):
                if any(fitz.Rect(x0, y0, x1, y1).intersects(b) for b in table_boxes):
                    continue
                parts.append(txt.strip())
            text = "\n".join(p for p in parts if p)
            for t in tables:
                rows = [" | ".join((c or "").replace("\n", " ").strip() for c in row) for row in t.extract()]
                text += "\n[TABLE]\n" + "\n".join(rows) + "\n[/TABLE]"
            ocr = False
            if len(text.strip()) < 25:  # scanned page: no text layer
                png = page.get_pixmap(dpi=200).tobytes("png")
                text = _ocr_page(png)
                ocr = True
            pages.append(Page(i, text, ocr, len(tables)))
    return pages


def _text_pages(text: str) -> list[Page]:
    chunks = re.split(r"\f|^\s*-{2,}\s*page\s+\d+\s*-{2,}\s*$", text, flags=re.I | re.M)
    chunks = [c for c in chunks if c.strip()] or [text]
    return [Page(i, c) for i, c in enumerate(chunks, start=1)]


def extract_pages(data: bytes, filename: str) -> list[Page]:
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_pages(data)
    return _text_pages(data.decode("utf-8", errors="replace"))


# ------------------------------------------------------------------- injection hygiene (R8)
INJECTION_PATTERNS = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions?|rules?|prompts?)",
    r"disregard (?:all |any |the )?(?:previous|prior|above|earlier|your) ",
    r"(?:you are|act as|pretend to be) (?:now )?(?:an? )?(?:unrestricted|different|new|jailbroken)",
    r"(?:reveal|print|show|output) (?:your |the )?(?:system|hidden) prompt",
    r"\bsystem\s*:\s*",
    r"(?:always|must|should) (?:answer|respond|say|reply)\b[^.\n]{0,60}\b(?:regardless|no matter|instead)",
    r"do not (?:cite|mention|follow) (?:the )?(?:sources?|rules?|policy)",
    r"override (?:the )?(?:precedence|policy|rules?|eligibility)",
    r"mark (?:every|all|any) (?:student|answer)s? (?:as )?(?:eligible|approved)",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)


def neutralise_instructions(text: str) -> tuple[str, list[str]]:
    """Document text is data. Instruction-like sentences are replaced before indexing and flagged."""
    from rag.claims import sentences

    flags: list[str] = []
    for sentence in sentences(text):
        if _INJ.search(sentence):
            flags.append(sentence[:160])
            pattern = r"\s+".join(re.escape(w) for w in sentence.split())
            text = re.sub(pattern, "[instruction-like text removed]", text, count=1)
    return text, flags


# ----------------------------------------------------------------------------- sections
_HEADING = re.compile(
    r"^\s*(?:(?i:clause|section|article|rule|regulation|para(?:graph)?)\s+)?"
    r"(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})(?:[.):]|\s)\s*(?P<title>[A-Z(\[].*)?$",
)


def _heading(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or s.startswith("["):
        return None
    m = _HEADING.match(s)
    if not m:
        return None
    num, title = m.group("num"), (m.group("title") or "").strip()
    dotted = "." in num
    # A dotted number starts a clause even when the clause text follows on the same (long) line;
    # an undotted number must look like a short title ("7 Attendance").
    if not dotted and len(s) > 90:
        return None
    # A bare "1." list item ending with a full stop is body text, not a heading.
    if not dotted and (not title or title.endswith(".") or len(title) > 70):
        return None
    if dotted and re.match(r"^\d+\.\d+\s*(%|marks|credits|hours)", s):
        return None
    if re.fullmatch(r"\d{1,2}\.\d{1,2}", num) and re.match(r"^\d+\.\d+\s*$", s) and not title:
        return num, ""
    return num, title


@dataclass
class Section:
    label: str          # e.g. "7.2" or "p.14" when the document has no numbering
    title: str
    page: int
    text: str


def split_sections(pages: list[Page]) -> list[Section]:
    sections: list[Section] = []
    cur = Section("preamble", "", pages[0].number if pages else 1, "")
    for page in pages:
        for line in page.text.splitlines():
            h = _heading(line)
            if h:
                carry = ""
                if cur.text.strip():
                    # A bare parent heading ("7 Attendance") travels with its first child instead of
                    # becoming a one-line chunk of its own.
                    one_line = cur.text.strip()
                    if len(one_line.splitlines()) == 1 and cur.label != "preamble" and not one_line.endswith("."):
                        carry = cur.text.strip() + "\n"
                    else:
                        sections.append(cur)
                cur = Section(h[0], h[1], page.number, carry + line.strip() + "\n")
            else:
                if not cur.text.strip() and cur.label == "preamble":
                    cur.page = page.number
                cur.text += line + "\n"
    if cur.text.strip():
        sections.append(cur)
    if len(sections) == 1 and sections[0].label == "preamble":
        # Unnumbered document: label by page so citations still point somewhere checkable.
        sections = [Section(f"p.{p.number}", "", p.number, p.text) for p in pages if p.text.strip()]
    return sections


# ------------------------------------------------------------------------------ chunking
@dataclass
class Chunk:
    index: int
    text: str            # text shown to the user / used for claims
    embed_text: str      # text embedded (title + section prefix improves retrieval)
    section: str
    page: int
    meta: dict = field(default_factory=dict)


def _window(text: str, size: int, overlap: int) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + size // 2:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def make_chunks(title: str, pages: list[Page], mode: str = "section", size: int = 900, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    if mode == "fixed":
        # Section-agnostic baseline: sliding window over the whole document; the section
        # label is the nearest heading at the chunk start so citations remain usable.
        flat, marks = "", []
        for page in pages:
            marks.append((len(flat), page.number))
            flat += page.text + "\n"
        heads = []
        pos = 0
        for line in flat.splitlines(keepends=True):
            h = _heading(line)
            if h:
                heads.append((pos, h[0]))
            pos += len(line)
        starts = _window_starts(flat, size, overlap)
        for i, (s, piece) in enumerate(starts):
            page = [p for off, p in marks if off <= s][-1] if marks else 1
            sec = ([h for off, h in heads if off <= s] or [f"p.{page}"])[-1]
            chunks.append(Chunk(i, piece, f"{title} §{sec}\n{piece}", sec, page))
        return chunks
    for sec in split_sections(pages):
        for piece in _window(sec.text, size, overlap):
            chunks.append(Chunk(len(chunks), piece, f"{title} §{sec.label} {sec.title}\n{piece}", sec.label, sec.page))
    return chunks


def _window_starts(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    out, start = [], 0
    text = re.sub(r"[ \t]+", " ", text)
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + size // 2:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append((start, piece))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out
