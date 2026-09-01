"""
Support utilities for Feature One (Important Terms): locating a phrase's
bounding box on a page for highlighting, plus a seed keyword list that
combines with LLM-driven, document-specific term detection (spec section 32:
"NLP/keyword extraction + LLM classification + document structure + RAG").
"""
from __future__ import annotations
from typing import Optional, List
import re

from .parser import Page
from .schema import BBox

# Seed list from the spec (section 3). This is a STARTING POINT only - the
# analyzer also asks the LLM to find document-specific important terms that
# aren't on this list, so we never rely on the static list alone.
SEED_KEYWORDS: List[str] = [
    "Loan", "Borrower", "Lender", "Interest Rate", "Profit Rate", "Principal",
    "Repayment", "Installment", "Penalty", "Late Payment", "Default", "Collateral",
    "Security", "Guarantee", "Guarantor", "Termination", "Early Settlement",
    "Prepayment", "Renewal", "Automatic Renewal", "Arbitration", "Liability",
    "Indemnity", "Authorization", "Consent", "Debit Authorization", "Direct Debit",
    "Data Sharing", "Credit Check", "Insurance", "Takaful", "Processing Fee",
    "Annual Fee", "Late Fee", "Outstanding Amount", "Due Date", "Notice Period",
    "Payment Date", "Signature", "Initials", "Witness", "Declaration",
    "Acknowledgement", "CNIC",
]

STOPWORDS_NEVER_HIGHLIGHT = {
    "the", "and", "is", "customer", "agreement", "shall", "will", "a", "an", "of", "to",
}


def find_bbox_for_phrase(page: Page, phrase: str, max_words: int = 8) -> Optional[BBox]:
    """Best-effort: locate the bounding box of `phrase` on `page` using the
    page's word list. Matches a short run of consecutive words (case-insensitive,
    punctuation-insensitive). Returns None if not found - callers must handle
    that gracefully rather than guessing a location."""
    if not page.words or not phrase:
        return None

    target_tokens = [t.lower() for t in re.findall(r"[a-z0-9']+", phrase.lower())]
    if not target_tokens:
        return None
    target_tokens = target_tokens[:max_words]

    page_tokens = []  # (lower_text, word_dict)
    for w in page.words:
        clean = re.sub(r"[^a-z0-9']", "", w["text"].lower())
        if clean:
            page_tokens.append((clean, w))

    n = len(target_tokens)
    for i in range(len(page_tokens) - n + 1):
        window = [page_tokens[i + j][0] for j in range(n)]
        if window == target_tokens:
            matched_words = [page_tokens[i + j][1] for j in range(n)]
            x0 = min(w["x0"] for w in matched_words)
            top = min(w["top"] for w in matched_words)
            x1 = max(w["x1"] for w in matched_words)
            bottom = max(w["bottom"] for w in matched_words)
            return BBox(page=page.number, x0=x0, top=top, x1=x1, bottom=bottom,
                        page_width=page.width, page_height=page.height)
    return None
