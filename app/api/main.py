"""FastAPI application implementing the fixed API contract (section 6 of the participant guide)."""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import db, loader
from app.api.schemas import AskRequest, AskResponse
from app.bootstrap import bootstrap
from app.config import settings
from app.service import get_service
from ingestion.service import DocMeta

log = logging.getLogger("uniassist")
STUDENT_ID = re.compile(r"^[Ss]\d{4}$")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("AUTO_BOOTSTRAP", "1") != "0":
        log.info("bootstrap: %s", bootstrap(settings))
    else:
        settings.ensure_dirs()
        db.ensure_schema()
    yield


app = FastAPI(title="uniAssist - University Student Services Assistant", version="2.0.0", lifespan=lifespan,
              description="Evidence-first assistant: cited document answers, deterministic tools, auditable.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def safe_error(_: Request, exc: Exception):
    log.exception("unhandled error")  # details stay in server logs, never in the response
    return JSONResponse(status_code=500, content={"detail": "The request could not be completed safely."})


@app.post("/ask", response_model=AskResponse, summary="Ask a question")
def ask(body: AskRequest, x_student_id: str | None = Header(default=None)):
    sid = (x_student_id or "").strip() or None
    if sid and not STUDENT_ID.match(sid):
        raise HTTPException(400, "X-Student-Id must look like S1001")
    try:
        date.fromisoformat(body.as_of_date) if body.as_of_date else None
    except ValueError:
        raise HTTPException(422, "as_of_date must be a real date (YYYY-MM-DD)")
    return get_service().ask(body.question, sid, body.as_of_date)


@app.post("/ingest", summary="Add a document while running")
async def ingest(request: Request):
    """Multipart: `file` plus Source Register fields (Annex B) as a JSON `metadata` field or as form fields."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart field 'file' is required")
    raw = form.get("metadata")
    try:
        if raw:
            meta = json.loads(raw) if isinstance(raw, str) else json.loads((await raw.read()).decode())
        else:
            meta = {k: v for k, v in form.items() if k != "file" and isinstance(v, str)}
        doc = DocMeta(**meta)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        detail = exc.errors(include_url=False, include_context=False) if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail)
    data = await upload.read()
    try:
        return get_service().ingest(data, upload.filename or f"{doc.doc_id}.pdf", doc)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/health")
def health():
    return get_service().health()


@app.get("/audit/{trace_id}")
def audit(trace_id: str):
    rec = db.load_audit(trace_id)
    if not rec:
        raise HTTPException(404, "No audit record for that trace_id")
    return rec


@app.get("/sources", summary="Source Register")
def sources():
    from app import registry

    return registry.list_sources()


@app.post("/admin/students", summary="Load test students (CSV files in the Annex C schema)")
async def load_students(request: Request):
    """Multipart with any of: students, courses, attendance, results (CSV files); optional `strict=true`."""
    form = await request.form()
    tables = {}
    for name in loader.ORDER:
        f = form.get(name)
        if f is not None and hasattr(f, "read"):
            tables[name] = loader.read_csv_bytes(await f.read())
    if not tables:
        raise HTTPException(422, "attach at least one of: students, courses, attendance, results")
    return loader.load_tables(tables, strict=str(form.get("strict", "")).lower() in {"1", "true", "yes"})
