# Agreement Action, Signature & Risk Analysis

An "Analyze My Agreement" module: upload a bank/financing agreement PDF and get
a structured, page-cited review — financial terms, obligations, bank rights,
default/termination conditions, signature & consent fields, **important-term
highlighting**, evidence-backed benefits/concerns, a dynamic "before you sign"
checklist, a grounded Q&A box, and a spoken **Pre-Signing Voice Summary** —
before you ever export a PDF report.

This is a runnable reference implementation of the spec, not a hosted
product. Everything runs on your own machine/server, using your own
Groq API key. Nothing is sent anywhere except your PDF's extracted
text going to the Groq API for analysis.

## What's inside

```
agreement-analyzer/
  backend/
    main.py                # FastAPI app (upload / analyze / ask / voice-summary / export)
    storage.py              # file-based storage, never mutates the original PDF
    pipeline/
      parser.py             # PDF text extraction + OCR fallback + word bounding boxes
      detector.py           # regex-based signature/checkbox/blank-field detection
      terms.py              # important-term seed list + bounding-box lookup for highlighting
      retriever.py           # chunking + TF-IDF evidence retrieval (the "RAG" layer)
      llm.py                # Groq API wrapper, grounding rules
      analyzer.py           # orchestrates retrieval -> per-category LLM calls -> report
      export.py             # renders the analysis as a downloadable PDF
      schema.py             # the structured JSON contract (pydantic)
    requirements.txt
  frontend/
    index.html / style.css / app.js   # the 30/70 "Review Before Signing" workspace (no build step)
  README.md
```

## 1. Prerequisites

- Python 3.10+
- A Groq API key (https://console.groq.com/keys)
- Optional, for OCR of scanned pages:
  - `tesseract-ocr` and `poppler-utils` (Ubuntu/Debian: `sudo apt-get install -y tesseract-ocr poppler-utils`)
  - If these aren't installed, the app still works for text-based PDFs; scanned
    pages will just be flagged instead of OCR'd.

## 2. Install

```bash
cd agreement-analyzer/backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install "httpx<0.28"            # required for compatibility with the current groq SDK
```

## 3. Configure

Open `backend/pipeline/llm.py` and either export `GROQ_API_KEY` as an environment
variable before starting the server, or paste your key directly into the
`_client()` function if you prefer not to deal with environment variables:

```bash
export GROQ_API_KEY=gsk_...                     # macOS/Linux
$env:GROQ_API_KEY="gsk_..."                     # Windows PowerShell
# Optional overrides:
export ANALYZER_MODEL=openai/gpt-oss-120b       # any current Groq-hosted model, see console.groq.com/docs/models
export ANALYZER_DATA_DIR=./data                 # where uploads + analysis JSON are stored
```

## 4. Run

```bash
cd agreement-analyzer/backend
uvicorn main:app --reload --port 8000
# or on Windows without activating the venv:
.\venv\Scripts\uvicorn.exe main:app --reload --port 8000
```

Then open **http://localhost:8000** — the backend serves the frontend directly,
so there's nothing else to start.

## 5. Use it

1. Drop in a PDF agreement.
2. Watch the real, backend-reported processing checklist (important terms,
   financial terms, obligations, signatures, checkboxes, RAG verification,
   benefits, concerns, report, voice summary) — independent categories run
   in parallel, each grounded only in retrieved evidence, never model memory.
3. Browse the **30% document / 70% analysis** workspace:
   - The left ledger strip marks which pages contain signatures, checkboxes,
     or high-attention clauses; click any page or any card to jump the PDF
     viewer there.
   - Important terms are highlighted directly on the PDF page (an overlay —
     your original file is never modified); click a highlight for its
     category, importance, and plain-language meaning.
   - The **Important Terms** tab lets you search and filter by category or
     importance level.
4. Click **"Listen to Analysis"** for a spoken Pre-Signing Voice Summary —
   played through your browser's own text-to-speech engine (no extra API
   key, nothing leaves your machine), with play/pause/stop and speed controls.
5. Use the ask box for "What am I agreeing to?" / "What happens if I sign
   this?" — answers are grounded in retrieved passages and say so plainly
   when the document doesn't clearly answer.
6. Export a standalone PDF report (never modifies your original file).

## Notes on scope and honesty

- **Retrieval** uses local TF-IDF similarity over page/paragraph chunks
  rather than a hosted embeddings model, so the whole thing runs without
  extra API dependencies. It's simpler than a production vector DB, but it
  keeps every answer traceable to a specific page, which is the actual
  requirement.
- **The attention score** (Low/Moderate/High) is a transparent, rule-based
  count of document characteristics (fees present, early settlement terms,
  default clauses, authorizations, termination restrictions, etc.) — not an
  LLM guess and never a legal judgment. The methodology is shown in the UI.
- **Signature/checkbox/important-term detection** is regex/seed-list-first
  (so it reliably finds "Signature: ____", "☐ I authorize...", blank lines,
  etc. across a long PDF), then the LLM explains and classifies each
  detected item using only its surrounding text. The LLM is never asked to
  "notice" fields on its own, and highlighting positions come from real
  word-level PDF coordinates, not guesses.
- **The Pre-Signing Voice Summary** is generated from the already-validated,
  evidence-backed analysis JSON — not a fresh pass over the raw document —
  and is spoken client-side via the browser's Web Speech API, so it degrades
  gracefully (and never breaks the rest of the report) if a browser doesn't
  support speech synthesis.
- This tool explains what a document says. It intentionally never tells a
  user whether to sign, and never states that a clause is "legal" or
  "safe." For high-stakes agreements, it recommends professional review.

