"""
Core PDF -> Markdown conversion engine.

Strategy:
1. Walk each page's text as "spans" (PyMuPDF gives font size, flags, bbox per span).
2. Compute a document-wide body font size (the statistical mode of span sizes).
3. Classify each line as heading (H1-H4), body, list item, or blank, using
   relative font size + bold flag + leading bullet/number patterns.
4. Detect simple grid tables via PyMuPDF's built-in table finder and emit
   them as Markdown pipe tables.
5. If a page yields ~no extractable text, treat it as scanned and OCR it
   with pytesseract, inserting the recognized text as plain paragraphs
   (structure detection is skipped for OCR'd pages since font metadata
   isn't available).
6. Join everything into clean Markdown: single blank line between blocks,
   no trailing whitespace, no triple-plus blank lines.

This does not aim to be "perfect" for arbitrarily complex layouts
(multi-column academic papers, heavy nested tables). It aims to be very
good on typical reports, articles, letters and manuals, and to degrade
gracefully (never crash, never silently drop content) elsewhere.
"""

from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pymupdf as fitz  # PyMuPDF (new import name; "fitz" alias is deprecated)

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


BULLET_RE = re.compile(r"^[•●◦‣\-\*–]\s+")
NUMBERED_RE = re.compile(r"^(\d{1,3}|[a-zA-Z])[\.\)]\s+")
WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass
class ConversionOptions:
    ocr_scanned_pages: bool = True
    ocr_language: str = "eng"
    extract_images: bool = False
    image_dir: Optional[Path] = None
    min_heading_ratio: float = 1.15  # span size / body size to count as a heading


@dataclass
class PageResult:
    page_number: int
    markdown: str
    used_ocr: bool
    warning: Optional[str] = None


@dataclass
class ConversionResult:
    markdown: str
    pages: list[PageResult] = field(default_factory=list)
    had_ocr: bool = False
    warnings: list[str] = field(default_factory=list)


def _body_font_size(doc: fitz.Document, sample_pages: int = 10) -> float:
    """Estimate the document's body text size as the mode of span sizes."""
    sizes: list[float] = []
    for page in doc[: min(sample_pages, len(doc))]:
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        sizes.append(round(span["size"], 1))
    if not sizes:
        return 11.0
    try:
        return statistics.mode(sizes)
    except statistics.StatisticsError:
        return statistics.median(sizes)


def _classify_heading_level(size_ratio: float) -> int:
    """Map a font-size ratio (span / body) to a heading level 1-4, or 0 for body text."""
    if size_ratio >= 1.8:
        return 1
    if size_ratio >= 1.5:
        return 2
    if size_ratio >= 1.3:
        return 3
    if size_ratio >= 1.15:
        return 4
    return 0


def _escape_md_inline(text: str) -> str:
    """Escape characters that would otherwise be misread as Markdown syntax,
    without touching characters we deliberately emit ourselves later."""
    # Escape backslashes first, then the common inline-syntax triggers.
    text = text.replace("\\", "\\\\")
    for ch in ("*", "_", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def _line_text_and_style(line: dict) -> tuple[str, float, bool]:
    """Return (plain_text, max_span_size, is_bold) for a PyMuPDF text line."""
    parts = []
    max_size = 0.0
    bold = False
    for span in line.get("spans", []):
        text = span.get("text", "")
        if not text:
            continue
        parts.append(text)
        max_size = max(max_size, span.get("size", 0.0))
        if span.get("flags", 0) & 2**4:  # bold flag bit
            bold = True
        font_name = span.get("font", "").lower()
        if "bold" in font_name:
            bold = True
    full = WHITESPACE_RE.sub(" ", "".join(parts)).strip()
    return full, max_size, bold


def _page_has_table(page: fitz.Page) -> list:
    try:
        finder = page.find_tables()
        return list(finder.tables)
    except Exception:
        return []


def _table_to_markdown(table) -> str:
    try:
        rows = table.extract()
    except Exception:
        return ""
    if not rows:
        return ""
    cleaned = [[(c or "").strip().replace("\n", " ") for c in row] for row in rows]
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]

    lines = []
    header, *body = cleaned
    lines.append("| " + " | ".join(_escape_md_inline(c) for c in header) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in body:
        lines.append("| " + " | ".join(_escape_md_inline(c) for c in row) + " |")
    return "\n".join(lines)


def _convert_text_page(page: fitz.Page, body_size: float, opts: ConversionOptions) -> str:
    data = page.get_text("dict")
    tables = _page_has_table(page)
    table_bboxes = [fitz.Rect(t.bbox) for t in tables]
    table_md_by_index = {id(t): _table_to_markdown(t) for t in tables}
    emitted_tables: set[int] = set()

    blocks_out: list[str] = []
    in_list = False

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        block_rect = fitz.Rect(block["bbox"])

        # If this block sits inside a detected table region, emit the table
        # once (on first overlap) and skip raw text from it thereafter.
        overlapping_table = None
        for t, bbox in zip(tables, table_bboxes):
            if bbox.intersects(block_rect):
                overlapping_table = t
                break
        if overlapping_table is not None:
            key = id(overlapping_table)
            if key not in emitted_tables:
                md = table_md_by_index[key]
                if md:
                    blocks_out.append(md)
                emitted_tables.add(key)
            in_list = False
            continue

        for line in block.get("lines", []):
            text, size, bold = _line_text_and_style(line)
            if not text:
                continue

            ratio = size / body_size if body_size else 1.0
            level = _classify_heading_level(ratio)

            if level:
                blocks_out.append(f"{'#' * level} {_escape_md_inline(text)}")
                in_list = False
                continue

            bullet_match = BULLET_RE.match(text)
            numbered_match = NUMBERED_RE.match(text)
            if bullet_match:
                content = text[bullet_match.end():].strip()
                blocks_out.append(f"- {_escape_md_inline(content)}")
                in_list = True
                continue
            if numbered_match:
                marker = numbered_match.group(1)
                content = text[numbered_match.end():].strip()
                blocks_out.append(f"{marker}. {_escape_md_inline(content)}")
                in_list = True
                continue

            escaped = _escape_md_inline(text)
            if bold:
                escaped = f"**{escaped}**"
            blocks_out.append(escaped)
            in_list = False

    # Emit any remaining tables not touched by a text block loop (rare).
    for t in tables:
        if id(t) not in emitted_tables:
            md = table_md_by_index[id(t)]
            if md:
                blocks_out.append(md)

    return "\n\n".join(blocks_out)


def _page_text_density(page: fitz.Page) -> int:
    return len(page.get_text("text").strip())


def _ocr_page(page: fitz.Page, opts: ConversionOptions) -> str:
    if not OCR_AVAILABLE:
        return "*[OCR unavailable: pytesseract/Pillow not installed — page skipped]*"
    pix = page.get_pixmap(dpi=300)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    try:
        text = pytesseract.image_to_string(img, lang=opts.ocr_language)
    except Exception as exc:  # pytesseract not installed on the OS, etc.
        return f"*[OCR failed: {exc}]*"
    text = text.strip()
    if not text:
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n\n".join(_escape_md_inline(p) for p in paragraphs)


def convert_pdf(
    path: Path,
    opts: Optional[ConversionOptions] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ConversionResult:
    """Convert a single PDF file to Markdown.

    progress_cb(current_page, total_pages) is called after each page, if given.
    """
    opts = opts or ConversionOptions()
    doc = fitz.open(str(path))
    body_size = _body_font_size(doc)

    page_results: list[PageResult] = []
    warnings: list[str] = []
    had_ocr = False
    total = len(doc)

    for i, page in enumerate(doc):
        page_num = i + 1
        density = _page_text_density(page)

        used_ocr = False
        if density < 10 and opts.ocr_scanned_pages:
            md = _ocr_page(page, opts)
            used_ocr = True
            had_ocr = True
            if not md:
                warnings.append(f"Page {page_num}: no text found (image may be blank or unreadable).")
        elif density < 10:
            md = ""
            warnings.append(f"Page {page_num}: appears to be scanned/image-only; OCR was disabled.")
        else:
            md = _convert_text_page(page, body_size, opts)

        page_results.append(PageResult(page_number=page_num, markdown=md, used_ocr=used_ocr))
        if progress_cb:
            progress_cb(page_num, total)

    doc.close()

    full_md = "\n\n".join(p.markdown for p in page_results if p.markdown.strip())
    full_md = re.sub(r"\n{3,}", "\n\n", full_md).strip() + "\n"

    return ConversionResult(markdown=full_md, pages=page_results, had_ocr=had_ocr, warnings=warnings)


def convert_pdf_to_file(
    src: Path,
    dst: Path,
    opts: Optional[ConversionOptions] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ConversionResult:
    result = convert_pdf(src, opts, progress_cb)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(result.markdown, encoding="utf-8")
    return result
