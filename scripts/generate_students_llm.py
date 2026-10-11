"""LLM-assisted generation of synthetic student *identities* (Annex C `students` table, minus derived columns).

Why only identities: names, programmes, batches and plausible CGPAs are what an LLM is good at. Exact edge cases
(attendance at the threshold, marks one below the pass mark, ...) and cross-table consistency are injected by
scripts/generate_students.py, because an LLM cannot be relied on to hit exact numbers.

Pipeline: prompt (below, verbatim) -> local model via Ollama -> JSON -> Pydantic validation -> retry with the
validation error fed back (max 3 attempts) -> CSV for generate_students.py --seed-csv.

    python scripts/generate_students_llm.py --count 36 --out data/llm_generated/students_seed.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

PROMPT = """You are generating SYNTHETIC test data for a university student-services assistant.
Return ONLY a JSON object: {{"students": [ ... ]}} with exactly {count} objects, each with these keys:
  "student_id":       string, "S" followed by 4 digits, unique, sequential from S{start}
  "full_name":        string, a plausible Indian full name; invented, not a real person
  "programme":        one of {programmes}
  "batch_year":       integer, one of {batches}
  "current_semester": integer 1-10, consistent with batch_year (as of October 2026, 2023 -> 7, 2024 -> 5)
  "cgpa":             number 0.00-10.00 with two decimals, realistic distribution (mostly 6.5-9.0)
Rules: no real people, no real roll numbers, spread students evenly over programmes and batches,
no duplicate names, do not use ids S9000-S9999. Output JSON only, no prose."""


class Student(BaseModel):
    student_id: str = Field(pattern=r"^S\d{4}$")
    full_name: str = Field(min_length=3)
    programme: str
    batch_year: int
    current_semester: int = Field(ge=1, le=10)
    cgpa: float = Field(ge=0, le=10)

    @field_validator("student_id")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        if 9000 <= int(v[1:]) <= 9999:
            raise ValueError("S9000-S9999 are reserved for judges")
        return v


class Batch(BaseModel):
    students: list[Student]


def validate(raw: str, count: int, programmes: list[str], batches: list[int]) -> list[Student]:
    data = Batch.model_validate_json(raw)
    if len(data.students) != count:
        raise ValueError(f"expected {count} students, got {len(data.students)}")
    ids = [s.student_id for s in data.students]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate student_id")
    if len({s.full_name for s in data.students}) != len(ids):
        raise ValueError("duplicate full_name")
    for s in data.students:
        if s.programme not in programmes or s.batch_year not in batches:
            raise ValueError(f"{s.student_id}: programme/batch outside the allowed values")
    return data.students


def call_ollama(prompt: str, model: str, base_url: str, temperature: float) -> str:
    import requests

    r = requests.post(base_url.rstrip("/") + "/chat/completions", timeout=300, json={
        "model": model, "temperature": temperature, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=36)
    ap.add_argument("--start", type=int, default=1001)
    ap.add_argument("--programmes", default="B.Tech CSE,B.Tech ECE")
    ap.add_argument("--batches", default="2023,2024")
    ap.add_argument("--model", default=os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b"))
    ap.add_argument("--base-url", default=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"))
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--out", default="data/llm_generated/students_seed.csv")
    a = ap.parse_args()
    programmes, batches = a.programmes.split(","), [int(x) for x in a.batches.split(",")]
    prompt = PROMPT.format(count=a.count, start=a.start, programmes=json.dumps(programmes), batches=batches)
    feedback = ""
    for attempt in range(1, 4):
        raw = call_ollama(prompt + feedback, a.model, a.base_url, a.temperature)
        try:
            students = validate(raw, a.count, programmes, batches)
            break
        except (ValidationError, ValueError) as exc:
            print(f"attempt {attempt} rejected: {str(exc)[:300]}", file=sys.stderr)
            feedback = f"\n\nYour previous answer was rejected: {str(exc)[:400]}\nFix it and return the full JSON again."
    else:
        print("giving up after 3 attempts", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(Student.model_fields))
        w.writeheader()
        w.writerows(s.model_dump() for s in students)
    print(f"wrote {len(students)} students to {out} (model {a.model}, attempt {attempt}); "
          f"now run scripts/generate_students.py --seed-csv {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
