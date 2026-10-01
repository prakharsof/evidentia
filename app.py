from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field
from pypdf import PdfReader

load_dotenv()
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "data" / "evidentia.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "12")) * 1024 * 1024
ALLOWED_SUFFIXES = {".txt", ".md", ".pdf"}

app = FastAPI(title="Evidentia", version="1.0.0", description="Evidence-first research over your documents")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize() -> None:
    with closing(connect()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            page_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            page INTEGER,
            text TEXT NOT NULL,
            UNIQUE(document_id, ordinal)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(
            text, content='chunks', content_rowid='id', tokenize='porter unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunk_search(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunk_search(chunk_search, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunk_search(chunk_search, rowid, text) VALUES ('delete', old.id, old.text);
            INSERT INTO chunk_search(rowid, text) VALUES (new.id, new.text);
        END;
        """)
        db.commit()


@app.on_event("startup")
def startup() -> None:
    initialize()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "answer_mode": "grounded_generation" if os.getenv("OPENAI_API_KEY") else "extractive"}


class AskRequest(BaseModel):
    question: str = Field(min_length=4, max_length=1200)
    top_k: int = Field(default=6, ge=1, le=12)


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_pages(pages: list[tuple[int | None, str]], size: int = 1100, overlap: int = 180) -> list[tuple[int | None, str]]:
    out: list[tuple[int | None, str]] = []
    for page, raw in pages:
        text = clean_text(raw)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
                if boundary > start + size // 2:
                    end = boundary + 1
            piece = text[start:end].strip()
            if piece:
                out.append((page, piece))
            if end == len(text):
                break
            start = max(end - overlap, start + 1)
    return out


def parse_upload(name: str, payload: bytes) -> tuple[list[tuple[int | None, str]], int]:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, "Upload a .pdf, .md, or .txt file.")
    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(payload)
            handle.flush()
            try:
                reader = PdfReader(handle.name)
                pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
            except Exception as exc:
                raise HTTPException(422, "This PDF could not be read. Try a text-based PDF.") from exc
        if not any(text.strip() for _, text in pages):
            raise HTTPException(422, "No selectable text found. Scanned PDFs need OCR before upload.")
        return pages, len(pages)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Text documents must use UTF-8 encoding.") from exc
    return [(None, text)], 0


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    name = Path(file.filename or "document").name[:180]
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    pages, page_count = parse_upload(name, payload)
    chunks = chunk_pages(pages)
    if not chunks:
        raise HTTPException(422, "The document has no extractable text.")
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    try:
        with closing(connect()) as db:
            cursor = db.execute("INSERT INTO documents(name, sha256, page_count) VALUES (?, ?, ?)", (name, digest, page_count))
            document_id = cursor.lastrowid
            db.executemany("INSERT INTO chunks(document_id, ordinal, page, text) VALUES (?, ?, ?, ?)",
                            [(document_id, i, page, text) for i, (page, text) in enumerate(chunks)])
            db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This exact file is already in your library.")
    return {"id": document_id, "name": name, "chunks": len(chunks), "pages": page_count}


@app.get("/api/documents")
def list_documents() -> list[dict[str, Any]]:
    with closing(connect()) as db:
        rows = db.execute("""SELECT d.id, d.name, d.page_count, d.created_at, COUNT(c.id) AS chunks
                             FROM documents d LEFT JOIN chunks c ON c.document_id=d.id
                             GROUP BY d.id ORDER BY d.created_at DESC""").fetchall()
    return [dict(row) for row in rows]


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int) -> dict[str, bool]:
    with closing(connect()) as db:
        cursor = db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        db.commit()
    if not cursor.rowcount:
        raise HTTPException(404, "Document not found.")
    return {"deleted": True}


def search_chunks(question: str, top_k: int) -> list[dict[str, Any]]:
    # FTS5 BM25 is fast, local, and deterministic. Quote terms to keep punctuation from
    # becoming FTS operators; a failed/empty query gracefully returns no evidence.
    terms = re.findall(r"[\w'-]+", question)
    if not terms:
        return []
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:24])
    with closing(connect()) as db:
        rows = db.execute("""SELECT c.id, c.document_id, d.name, c.page, c.text,
                                   bm25(chunk_search) AS score
                            FROM chunk_search JOIN chunks c ON c.id=chunk_search.rowid
                            JOIN documents d ON d.id=c.document_id
                            WHERE chunk_search MATCH ? ORDER BY score LIMIT ?""", (match, top_k * 3)).fetchall()
    # RRF adds a token-coverage signal to FTS ranking; it improves robustness for short queries.
    q_terms = {term.lower() for term in terms if len(term) > 2}
    ranked = []
    for rank, row in enumerate(rows, start=1):
        body_terms = set(re.findall(r"[\w'-]+", row["text"].lower()))
        coverage = len(q_terms & body_terms) / max(len(q_terms), 1)
        score = (1 / (60 + rank)) + 0.03 * coverage
        ranked.append((score, row, coverage))
    ranked.sort(key=lambda item: item[0], reverse=True)
    result = []
    for index, (score, row, coverage) in enumerate(ranked[:top_k], start=1):
        result.append({"source_id": f"S{index}", "chunk_id": row["id"], "document_id": row["document_id"],
                       "name": row["name"], "page": row["page"], "text": row["text"],
                       "retrieval_score": round(score, 4), "term_coverage": round(coverage, 3)})
    return result


def extractive_answer(question: str, sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "I couldn’t find matching evidence in your library. Try a more specific question or add relevant sources."
    lines = ["I found these relevant passages. Review the source excerpts below; this mode does not invent a synthesized answer."]
    for source in sources[:3]:
        excerpt = source["text"].strip().replace("\n", " ")
        if len(excerpt) > 420:
            excerpt = excerpt[:417].rsplit(" ", 1)[0] + "…"
        lines.append(f"**[{source['source_id']}] {source['name']}**\n> {excerpt}")
    return "\n\n".join(lines)


def grounded_answer(question: str, sources: list[dict[str, Any]]) -> tuple[str, str]:
    if not os.getenv("OPENAI_API_KEY") or not sources:
        return extractive_answer(question, sources), "extractive"
    client_args: dict[str, str] = {"api_key": os.environ["OPENAI_API_KEY"]}
    if os.getenv("OPENAI_BASE_URL"):
        client_args["base_url"] = os.environ["OPENAI_BASE_URL"]
    client = OpenAI(**client_args)
    evidence = "\n\n".join(f"[{s['source_id']}] {s['name']} (page {s['page'] or 'n/a'}):\n{s['text']}" for s in sources)
    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            messages=[
                {"role": "system", "content": "Answer only from the provided evidence. Cite every factual claim with one or more exact source labels like [S1]. If evidence is insufficient, say what is missing. Never invent citations or facts. Keep the answer concise."},
                {"role": "user", "content": f"Question: {question}\n\nEvidence:\n{evidence}"},
            ],
        )
        answer = response.choices[0].message.content or "No answer returned."
        allowed = {s["source_id"] for s in sources}
        used = set(re.findall(r"\[(S\d+)\]", answer))
        if used - allowed:
            answer = "The model returned a citation that was not in the retrieved evidence. Please retry or inspect the excerpts."
        return answer, "grounded_generation"
    except Exception:
        return extractive_answer(question, sources), "extractive_fallback"


@app.post("/api/ask")
def ask(payload: AskRequest) -> dict[str, Any]:
    sources = search_chunks(payload.question, payload.top_k)
    answer, mode = grounded_answer(payload.question, sources)
    return {"question": payload.question, "answer": answer, "mode": mode,
            "sources": [{k: source[k] for k in ("source_id", "name", "page", "text", "retrieval_score", "term_coverage")} for source in sources]}
