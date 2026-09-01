"""
File-based storage (spec section 29: "Never modify or overwrite the original
uploaded document" - we always keep original.pdf untouched and store the
analysis layer alongside it, never inside it).
"""
from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from typing import Optional

DATA_DIR = Path(os.environ.get("ANALYZER_DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def new_doc_id() -> str:
    return uuid.uuid4().hex[:12]


def doc_dir(doc_id: str) -> Path:
    d = DATA_DIR / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def original_path(doc_id: str) -> Path:
    return doc_dir(doc_id) / "original.pdf"


def set_status(doc_id: str, status: str, message: str = "") -> None:
    with open(doc_dir(doc_id) / "status.json", "w") as f:
        json.dump({"status": status, "message": message}, f)


def get_status(doc_id: str) -> dict:
    path = doc_dir(doc_id) / "status.json"
    if not path.exists():
        return {"status": "unknown", "message": ""}
    with open(path) as f:
        return json.load(f)


def save_analysis(doc_id: str, analysis_dict: dict) -> None:
    with open(doc_dir(doc_id) / "analysis.json", "w") as f:
        json.dump(analysis_dict, f, indent=2)


def load_analysis(doc_id: str) -> Optional[dict]:
    path = doc_dir(doc_id) / "analysis.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save_filename(doc_id: str, filename: str) -> None:
    with open(doc_dir(doc_id) / "filename.txt", "w") as f:
        f.write(filename)


def load_filename(doc_id: str) -> str:
    path = doc_dir(doc_id) / "filename.txt"
    if path.exists():
        return path.read_text().strip()
    return "agreement.pdf"


def exists(doc_id: str) -> bool:
    return original_path(doc_id).exists()
