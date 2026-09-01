"""
Document Parser + OCR (spec section 2: "Document Processing -> OCR if necessary").

Extracts per-page text with pdfplumber. If a page yields almost no text
(a strong signal it's a scanned image), we fall back to OCR via
pytesseract + pdf2image, when those optional dependencies are installed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import logging

import pdfplumber

logger = logging.getLogger("agreement_analyzer.parser")

MIN_CHARS_BEFORE_OCR = 20  # a page with fewer real characters is treated as "likely scanned"


@dataclass
class Page:
    number: int  # 1-indexed
    text: str
    used_ocr: bool = False
    words: list = None  # list of dicts: {text, x0, top, x1, bottom} in PDF point space
    width: float = 0.0
    height: float = 0.0


@dataclass
class ParsedDocument:
    pages: List[Page] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def full_text(self) -> str:
        return "\n".join(f"[PAGE {p.number}]\n{p.text}" for p in self.pages)


def _try_ocr_page(pdf_path: str, page_number: int) -> str:
    """Best-effort OCR for a single page. Returns '' if OCR isn't available."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning("OCR skipped for page %s: pytesseract/pdf2image not installed", page_number)
        return ""

    try:
        images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number, dpi=300)
        if not images:
            return ""
        return pytesseract.image_to_string(images[0])
    except Exception as exc:  # poppler/tesseract binaries may be missing
        logger.warning("OCR failed for page %s: %s", page_number, exc)
        return ""


def parse_pdf(pdf_path: str, allow_ocr: bool = True) -> ParsedDocument:
    doc = ParsedDocument()
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            used_ocr = False
            if allow_ocr and len(text.strip()) < MIN_CHARS_BEFORE_OCR:
                ocr_text = _try_ocr_page(pdf_path, i)
                if len(ocr_text.strip()) > len(text.strip()):
                    text = ocr_text
                    used_ocr = True

            words = []
            if not used_ocr:
                try:
                    for w in page.extract_words():
                        words.append({
                            "text": w["text"], "x0": w["x0"], "top": w["top"],
                            "x1": w["x1"], "bottom": w["bottom"],
                        })
                except Exception as exc:  # word-level extraction is best-effort
                    logger.warning("Word extraction failed for page %s: %s", i, exc)

            doc.pages.append(Page(
                number=i, text=text, used_ocr=used_ocr, words=words,
                width=float(page.width), height=float(page.height),
            ))
    return doc
