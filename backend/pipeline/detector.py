"""
Deterministic field detection.

The LLM is good at explaining a clause; it is not the right tool for
reliably finding every "Signature: ______" line in a 40-page PDF. So we
run cheap, transparent regex heuristics first to build a candidate list
of signature lines, checkboxes, and blank fields, each tagged with the
page it came from. The analyzer then asks the LLM to explain each
candidate using the retrieved evidence around it - the LLM never has to
"notice" the field on its own.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
import re

from .parser import ParsedDocument

SIGNATURE_LABELS = [
    "customer signature", "applicant signature", "borrower signature",
    "guarantor signature", "witness signature", "authorized signatory",
    "account holder signature", "co-borrower signature", "nominee signature",
    "signature of", "signature:", "sign here", "authorised signatory",
]

INITIAL_LABELS = ["initial:", "initials:", "customer initials", "initial here"]

DATE_LABELS = ["date:", "dated:", "date of signing"]

CHECKBOX_MARKERS = ["☐", "☑", "□", "[ ]", "[x]", "[X]"]
CHECKBOX_PHRASES = [
    "i agree", "i authorize", "i authorise", "i consent", "i confirm",
    "i accept", "i acknowledge", "i hereby", "yes", "no",
]

BLANK_LINE_PATTERN = re.compile(r"_{3,}|\.{4,}")


@dataclass
class Candidate:
    kind: str  # "signature" | "initial" | "date" | "checkbox" | "blank"
    page: int
    line: str
    context: str
    appears_blank: bool = False


def _context_window(lines: List[str], idx: int, span: int = 2) -> str:
    start = max(0, idx - span)
    end = min(len(lines), idx + span + 1)
    return "\n".join(lines[start:end]).strip()


def detect_candidates(doc: ParsedDocument) -> List[Candidate]:
    candidates: List[Candidate] = []
    for page in doc.pages:
        lines = page.text.splitlines()
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            lower = line.lower()
            ctx = _context_window(lines, idx)

            if any(label in lower for label in SIGNATURE_LABELS):
                candidates.append(Candidate(
                    kind="signature", page=page.number, line=line, context=ctx,
                    appears_blank=bool(BLANK_LINE_PATTERN.search(line)) or lower.strip().endswith((":", "signature")),
                ))
                continue

            if any(label in lower for label in INITIAL_LABELS):
                candidates.append(Candidate(
                    kind="initial", page=page.number, line=line, context=ctx,
                    appears_blank=bool(BLANK_LINE_PATTERN.search(line)),
                ))
                continue

            if any(marker in line for marker in CHECKBOX_MARKERS) or (
                lower.startswith(tuple(CHECKBOX_PHRASES)) and len(line) < 200
            ):
                candidates.append(Candidate(kind="checkbox", page=page.number, line=line, context=ctx))
                continue

            if any(label in lower for label in DATE_LABELS):
                candidates.append(Candidate(
                    kind="date", page=page.number, line=line, context=ctx,
                    appears_blank=bool(BLANK_LINE_PATTERN.search(line)),
                ))
                continue

            if BLANK_LINE_PATTERN.search(line) and len(line) < 160:
                candidates.append(Candidate(kind="blank", page=page.number, line=line, context=ctx, appears_blank=True))

    return candidates


def summarize_candidates(candidates: List[Candidate], max_per_kind: int = 40) -> str:
    """Render a compact, page-tagged list for inclusion in an LLM prompt."""
    by_kind: dict[str, List[Candidate]] = {}
    for c in candidates:
        by_kind.setdefault(c.kind, []).append(c)

    out = []
    for kind, items in by_kind.items():
        out.append(f"### {kind.upper()} candidates ({len(items)} found)")
        for c in items[:max_per_kind]:
            blank_tag = " [APPEARS BLANK]" if c.appears_blank else ""
            out.append(f"- page {c.page}{blank_tag}: \"{c.line}\"")
    return "\n".join(out)
