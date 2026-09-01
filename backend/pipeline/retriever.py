"""
Chunking + Evidence Retrieval (spec section 31: "Every important analysis
result must be traceable back to retrieved document evidence").

We keep retrieval dependency-light and fully local: TF-IDF + cosine
similarity over page/paragraph-level chunks. This avoids pulling down
a large embedding model just to run the demo, while still giving the
LLM only grounded, cited passages to work from instead of letting it
answer from memory.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .parser import ParsedDocument


@dataclass
class Chunk:
    id: int
    page: int
    text: str


def build_chunks(doc: ParsedDocument, max_chars: int = 900) -> List[Chunk]:
    """Split each page into paragraph-sized chunks, preserving page numbers."""
    chunks: List[Chunk] = []
    cid = 0
    for page in doc.pages:
        raw_paragraphs = re.split(r"\n\s*\n", page.text) or [page.text]
        buffer = ""
        for para in raw_paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(buffer) + len(para) > max_chars and buffer:
                chunks.append(Chunk(id=cid, page=page.number, text=buffer.strip()))
                cid += 1
                buffer = ""
            buffer += ("\n" if buffer else "") + para
        if buffer.strip():
            chunks.append(Chunk(id=cid, page=page.number, text=buffer.strip()))
            cid += 1
    return chunks


class EvidenceIndex:
    """A tiny local search index over the document's chunks."""

    def __init__(self, chunks: List[Chunk]):
        self.chunks = chunks
        self._texts = [c.text for c in chunks] or [""]
        self._vectorizer = TfidfVectorizer(stop_words="english", max_features=20000)
        self._matrix = self._vectorizer.fit_transform(self._texts)

    def search(self, query: str, top_k: int = 6) -> List[Chunk]:
        if not self.chunks:
            return []
        query_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self._matrix).flatten()
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = [self.chunks[i] for i in ranked[:top_k] if scores[i] > 0]
        # Fall back to the first few chunks if nothing scored (very short/odd documents)
        return results if results else self.chunks[:top_k]

    def search_many(self, queries: List[str], top_k_each: int = 4) -> List[Chunk]:
        seen = set()
        merged: List[Chunk] = []
        for q in queries:
            for c in self.search(q, top_k=top_k_each):
                if c.id not in seen:
                    seen.add(c.id)
                    merged.append(c)
        return merged

    def format_for_prompt(self, chunks: List[Chunk], max_chunks: int = 18) -> str:
        chunks = chunks[:max_chunks]
        return "\n\n".join(f"[Evidence page={c.page} id={c.id}]\n{c.text}" for c in chunks)
