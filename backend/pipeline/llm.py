"""

Thin wrapper around the Groq API (OpenAI-compatible chat completions).
"""
from __future__ import annotations
import json
import os
import re
import time
import threading
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()  # Yeh backend folder mein existing .env file ko dhundh kar load karega

from groq import Groq
from groq import APIStatusError

# ✅ FIX 1: SAHI MODEL NAME (Groq supported models)
DEFAULT_MODEL = os.environ.get("ANALYZER_MODEL", "llama-3.1-70b-versatile")

_CALL_LOCK = threading.Lock()
_MIN_GAP_SECONDS = float(os.environ.get("ANALYZER_MIN_CALL_GAP", "6.0"))
_last_call_at = 0.0
MAX_RETRIES = 8

SYSTEM_GROUNDING_RULES = """You are the analysis engine inside an "Agreement Understanding & Review Assistant".
You help a customer understand a bank/financial agreement before they sign it. You are NOT a lawyer
and you never give legal advice, never say a clause is "illegal" or "safe", and never tell the
person whether they should sign.

Hard rules:
- Only use the EVIDENCE passages provided below. Never use outside knowledge of "typical" bank agreements.
- Every factual claim must be traceable to a specific page number from the evidence.
- If the evidence does not clearly support a conclusion, say so explicitly (e.g. "the available text
  does not clearly establish whether this is mandatory or optional") instead of guessing.
- Never invent a page number, section number, fee amount, or consequence that is not present in the evidence.
- Output must be valid JSON only - no markdown fences, no commentary before or after the JSON.
"""

# ✅ FIX 2: KEY HARDCODE HATAYA - AB ENV VAR SE LEGA
def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set!")
    return Groq(api_key=api_key)
def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise

def _wait_seconds_from_error(exc: Exception) -> float:
    match = re.search(r"try again in ([\d.]+)s", str(exc))
    if match:
        return float(match.group(1)) + 0.5
    return 3.0

def _throttled_create(client: Groq, **kwargs):
    global _last_call_at
    last_exc = None
    for attempt in range(MAX_RETRIES):
        with _CALL_LOCK:
            wait = _MIN_GAP_SECONDS - (time.time() - _last_call_at)
            if wait > 0:
                time.sleep(wait)
            try:
                response = client.chat.completions.create(**kwargs)
                _last_call_at = time.time()
                return response
            except APIStatusError as exc:
                _last_call_at = time.time()
                if exc.status_code == 429:
                    last_exc = exc
                else:
                    raise
        time.sleep(_wait_seconds_from_error(last_exc))
    raise last_exc

def call_json(prompt: str, max_tokens: int = 3000, model: Optional[str] = None) -> Any:
    client = _client()
    response = _throttled_create(
        client,
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_GROUNDING_RULES + "\nRespond with a single JSON object or array only - no other text."},
            {"role": "user", "content": prompt},
        ],
    )
    text = response.choices[0].message.content or ""
    return _extract_json(text)

def call_text(prompt: str, system: Optional[str] = None, max_tokens: int = 1200, model: Optional[str] = None) -> str:
    client = _client()
    response = _throttled_create(
        client,
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system or SYSTEM_GROUNDING_RULES},
            {"role": "user", "content": prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()