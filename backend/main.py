from __future__ import annotations
"""
Banking Agreement Intelligence Platform - Agreement Action, Signature &
Risk Analysis module (backend).

Run with:
    uvicorn main:app --reload --port 8000

See ../README.md for full setup instructions.
"""
import os
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import storage
from pipeline.parser import parse_pdf
from pipeline.analyzer import run_full_analysis, answer_question, _generate_voice_summary
from pipeline.export import build_report_pdf
from pipeline.schema import AgreementAnalysis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agreement_analyzer.main")

# ------------- Create FastAPI app -------------
app = FastAPI(title="Agreement Action, Signature & Risk Analysis")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this for production deployments
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------- Utility models -------------
class AskRequest(BaseModel):
    question: str


# ------------- Debug route (MOVED HERE) -------------
@app.get("/ping")
def ping():
    return {
        "status": "ok",
        "env_keys": [k for k in os.environ.keys() if "GROQ" in k or "API" in k]
    }


@app.get("/")
def read_root():
    return {"message": "Agreement Analyzer API is running!"}


# ------------- Pipeline background task -------------
def _run_pipeline(doc_id: str):
    try:
        storage.set_status(doc_id, "processing", "Document uploaded")
        doc = parse_pdf(str(storage.original_path(doc_id)))

        def on_progress(stage: str):
            storage.set_status(doc_id, "processing", stage)

        analysis: AgreementAnalysis = run_full_analysis(doc, on_progress=on_progress)

        storage.save_analysis(doc_id, analysis.model_dump())
        storage.set_status(doc_id, "done", "Analysis complete.")
    except Exception as exc:
        logger.exception("Analysis failed for %s", doc_id)
        storage.set_status(doc_id, "error", str(exc))


# ------------- API Endpoints -------------
@app.post("/api/upload")
async def upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported in this build.")

    doc_id = storage.new_doc_id()
    target = storage.original_path(doc_id)
    with open(target, "wb") as out:
        shutil.copyfileobj(file.file, out)

    storage.save_filename(doc_id, file.filename)
    storage.set_status(doc_id, "queued", "Waiting to start analysis...")
    background_tasks.add_task(_run_pipeline, doc_id)

    return {"doc_id": doc_id, "filename": file.filename}


@app.get("/api/status/{doc_id}")
async def status(doc_id: str):
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")
    return storage.get_status(doc_id)


@app.get("/api/analysis/{doc_id}")
async def get_analysis(doc_id: str):
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")
    data = storage.load_analysis(doc_id)
    if data is None:
        raise HTTPException(202, "Analysis not ready yet.")
    return JSONResponse(data)


@app.get("/api/document/{doc_id}")
async def get_document(doc_id: str):
    path = storage.original_path(doc_id)
    if not path.exists():
        raise HTTPException(404, "Document not found.")
    return FileResponse(str(path), media_type="application/pdf", filename=storage.load_filename(doc_id))


@app.post("/api/ask/{doc_id}")
async def ask(doc_id: str, req: AskRequest):
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")
    doc = parse_pdf(str(storage.original_path(doc_id)), allow_ocr=False)
    try:
        result = answer_question(doc, req.question)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    return result


# ---------- VOICE SUMMARY ENDPOINTS (with caching and language support) ----------
def _get_cached_voice(doc_id: str, lang: str) -> Optional[str]:
    """Read cached voice summary from disk to avoid regenerating every time."""
    path = storage.doc_dir(doc_id) / f"voice_{lang}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _cache_voice(doc_id: str, lang: str, text: str):
    path = storage.doc_dir(doc_id) / f"voice_{lang}.txt"
    path.write_text(text, encoding="utf-8")


@app.get("/api/voice-summary/{doc_id}")
async def get_voice_summary(doc_id: str, lang: str = "en"):
    """Get the pre-signing voice summary in English (en) or Urdu (ur)."""
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")

    # Check cache first
    cached = _get_cached_voice(doc_id, lang)
    if cached:
        return {"voice_summary": cached}

    # Load analysis
    data = storage.load_analysis(doc_id)
    if data is None:
        raise HTTPException(202, "Analysis not ready yet.")

    analysis = AgreementAnalysis(**data)
    try:
        summary = _generate_voice_summary(analysis, language=lang)
        if not summary:
            raise HTTPException(500, "Could not generate voice summary.")
        _cache_voice(doc_id, lang, summary)
        return {"voice_summary": summary}
    except Exception as exc:
        raise HTTPException(500, f"Voice generation failed: {exc}")


@app.post("/api/voice-summary/{doc_id}")
async def regenerate_voice_summary(doc_id: str, lang: str = "en"):
    """Force regenerate the voice summary for a specific language."""
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")

    data = storage.load_analysis(doc_id)
    if data is None:
        raise HTTPException(202, "Analysis not ready yet.")

    analysis = AgreementAnalysis(**data)
    try:
        summary = _generate_voice_summary(analysis, language=lang)
        if not summary:
            raise HTTPException(500, "Could not generate voice summary.")
        _cache_voice(doc_id, lang, summary)
        return {"voice_summary": summary}
    except Exception as exc:
        raise HTTPException(500, f"Voice generation failed: {exc}")


# ---------- EXPORT PDF ----------
@app.get("/api/export/{doc_id}")
async def export(doc_id: str):
    if not storage.exists(doc_id):
        raise HTTPException(404, "Document not found.")
    data = storage.load_analysis(doc_id)
    if data is None:
        raise HTTPException(202, "Analysis not ready yet.")
    analysis = AgreementAnalysis(**data)
    pdf_bytes = build_report_pdf(analysis, storage.load_filename(doc_id))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="agreement-review-{doc_id}.pdf"'},
    )


# Serve the frontend as static files so the whole app can run from one process.
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")