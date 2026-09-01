import os
import sys
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ----- SAFE WRAPPER: Sab kuch try-except mein -----
try:
    # Sab se pehle env variable check karein
    GROQ_KEY = os.environ.get("GROQ_API_KEY")
    if not GROQ_KEY:
        print("WARNING: GROQ_API_KEY is NOT set in environment")
    else:
        print(f"GROQ_API_KEY found (first 4 chars: {GROQ_KEY[:4]}...)")
    
    # Ab imports karein (agar koi import fail hoga toh catch ho jayega)
    try:
        from pipeline.analyzer import run_full_analysis, answer_question
        from pipeline.export import build_report_pdf
        from pipeline.schema import AgreementAnalysis
        from pipeline.parser import parse_pdf
        import storage
        print("All imports successful")
    except Exception as import_err:
        print(f"Import error: {import_err}")
        raise

    app = FastAPI(title="Agreement Analyzer (Safe Mode)")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root():
        return {
            "status": "ok",
            "message": "Agreement Analyzer is running!",
            "groq_key_set": bool(os.environ.get("GROQ_API_KEY")),
            "python_version": sys.version
        }

    @app.get("/ping")
    async def ping():
        return {
            "status": "ok",
            "groq_key_exists": bool(os.environ.get("GROQ_API_KEY")),
            "env_vars": [k for k in os.environ.keys() if "GROQ" in k or "API" in k]
        }

    # ------ Baaki saare original endpoints yahan daalein (optional) ------
    # NOTE: Agar original endpoints bhi chaahiye toh unhe yahan paste karein
    # (lekin pehle confirm kar lein ki storage, pipeline import ho rahe hain)

except Exception as startup_err:
    # Agar startup mein hi error aaya, toh ek dummy app banayein jo error dikhaye
    print(f"CRITICAL STARTUP ERROR: {startup_err}")
    import traceback
    traceback.print_exc()
    
    app = FastAPI(title="Error Mode")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    
    @app.get("/")
    @app.get("/ping")
    async def error_root():
        return JSONResponse(
            status_code=500,
            content={
                "error": "App failed to start",
                "details": str(startup_err),
                "traceback": traceback.format_exc()
            }
        )