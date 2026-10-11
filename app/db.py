"""SQLite access: Annex C schema (+ additive columns) and the audit store."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import settings

ANNEX_C_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
 student_id TEXT PRIMARY KEY CHECK(student_id GLOB 'S[0-9][0-9][0-9][0-9]'),
 full_name TEXT NOT NULL,
 programme TEXT NOT NULL,
 batch_year INTEGER NOT NULL,
 current_semester INTEGER NOT NULL CHECK(current_semester BETWEEN 1 AND 10),
 cgpa REAL NOT NULL CHECK(cgpa BETWEEN 0 AND 10),
 active_backlogs INTEGER NOT NULL DEFAULT 0 CHECK(active_backlogs >= 0)
);
CREATE TABLE IF NOT EXISTS courses (
 course_code TEXT PRIMARY KEY,
 course_name TEXT NOT NULL,
 programme TEXT NOT NULL,
 semester INTEGER NOT NULL,
 credits INTEGER NOT NULL CHECK(credits > 0)
);
CREATE TABLE IF NOT EXISTS attendance (
 student_id TEXT NOT NULL,
 course_code TEXT NOT NULL,
 classes_held INTEGER NOT NULL CHECK(classes_held > 0),
 classes_attended INTEGER NOT NULL CHECK(classes_attended >= 0),
 PRIMARY KEY(student_id, course_code),
 CHECK(classes_attended <= classes_held),
 FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE,
 FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS results (
 student_id TEXT NOT NULL,
 course_code TEXT NOT NULL,
 exam_session TEXT NOT NULL,
 exam_type TEXT NOT NULL CHECK(exam_type IN ('REGULAR','SUPPLEMENTARY')),
 internal_marks INTEGER NOT NULL CHECK(internal_marks >= 0),
 external_marks INTEGER NOT NULL CHECK(external_marks >= 0),
 total_marks INTEGER NOT NULL CHECK(total_marks = internal_marks + external_marks),
 max_marks INTEGER NOT NULL CHECK(max_marks > 0),
 result TEXT NOT NULL CHECK(result IN ('PASS','FAIL','ABSENT','DETAINED')),
 PRIMARY KEY(student_id, course_code, exam_session, exam_type),
 FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE,
 FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rule_registry (
 rule_id TEXT PRIMARY KEY,
 description TEXT NOT NULL,
 parameter TEXT NOT NULL,
 operator TEXT NOT NULL,
 value TEXT NOT NULL,
 scope_programmes TEXT NOT NULL DEFAULT 'ALL',
 scope_batches TEXT NOT NULL DEFAULT 'ALL',
 effective_from TEXT NOT NULL,
 effective_to TEXT,
 source_doc_id TEXT NOT NULL,
 source_section TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_register (
 doc_id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 issuer TEXT NOT NULL,
 authority_level INTEGER NOT NULL CHECK(authority_level BETWEEN 1 AND 5),
 doc_type TEXT NOT NULL,
 version TEXT NOT NULL,
 effective_from TEXT NOT NULL,
 effective_to TEXT,
 supersedes TEXT,
 scope_programmes TEXT NOT NULL DEFAULT 'ALL',
 scope_batches TEXT NOT NULL DEFAULT 'ALL',
 provenance TEXT NOT NULL DEFAULT '',
 retrieved_on TEXT NOT NULL DEFAULT '',
 synthetic TEXT NOT NULL DEFAULT 'N' CHECK(synthetic IN ('Y','N'))
);
"""

# Columns the assistant adds on top of Annex C. Existing databases are migrated in place;
# judges' CSV loaders keep working because no required column is renamed or removed.
EXTRA_COLUMNS = {
    "source_register": {
        "content_hash": "TEXT",
        "filename": "TEXT",
        "ingested_at": "TEXT",
        "chunks_indexed": "INTEGER DEFAULT 0",
        "pages": "INTEGER DEFAULT 0",
        "ocr_pages": "INTEGER DEFAULT 0",
        "flags": "TEXT DEFAULT '[]'",
    },
    "rule_registry": {
        "origin": "TEXT DEFAULT 'seed'",
        "created_at": "TEXT",
    },
}

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
 trace_id TEXT PRIMARY KEY,
 timestamp TEXT NOT NULL,
 student_id TEXT,
 question_category TEXT,
 answer_type TEXT,
 record_json TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(path: Path | None = None) -> None:
    path = path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(ANNEX_C_SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # Databases created before effective_to existed on rule_registry.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(rule_registry)")}
        if "effective_to" not in have:
            conn.execute("ALTER TABLE rule_registry ADD COLUMN effective_to TEXT")
        conn.commit()
    finally:
        conn.close()


def rows(sql: str, params: tuple | list = (), path: Path | None = None) -> list[dict[str, Any]]:
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def one(sql: str, params: tuple | list = (), path: Path | None = None) -> dict[str, Any] | None:
    r = rows(sql, params, path)
    return r[0] if r else None


# ---------------------------------------------------------------- audit store
def _audit_conn() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.audit_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(AUDIT_SCHEMA)
    return conn


def save_audit(record: dict[str, Any]) -> None:
    conn = _audit_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO audit VALUES (?,?,?,?,?,?)",
            (record["trace_id"], record["timestamp"], record.get("student_id"),
             record.get("question_category"), record.get("answer_type"), json.dumps(record, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def load_audit(trace_id: str) -> dict[str, Any] | None:
    conn = _audit_conn()
    try:
        r = conn.execute("SELECT record_json FROM audit WHERE trace_id=?", (trace_id,)).fetchone()
        return json.loads(r["record_json"]) if r else None
    finally:
        conn.close()
