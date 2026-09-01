import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "FastAPI is running perfectly on Vercel!",
        "groq_key_exists": bool(os.environ.get("GROQ_API_KEY"))
    }

@app.get("/ping")
def ping():
    return {"status": "alive"}