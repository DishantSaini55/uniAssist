# Prompts (verbatim)

## 1. Student identity generation (`scripts/generate_students_llm.py`)

Model: local Ollama model named by `--model` / `LOCAL_LLM_MODEL` (default `llama3.1:8b`), temperature `0.7`, JSON mode,
up to 3 attempts. After a failed validation the error is appended to the prompt (`Your previous answer was rejected: ...`).
The text below is the exact template in the script (`{count}`, `{start}`, `{programmes}`, `{batches}` are filled in):

```
You are generating SYNTHETIC test data for a university student-services assistant.
Return ONLY a JSON object: {"students": [ ... ]} with exactly {count} objects, each with these keys:
  "student_id":       string, "S" followed by 4 digits, unique, sequential from S{start}
  "full_name":        string, a plausible Indian full name; invented, not a real person
  "programme":        one of {programmes}
  "batch_year":       integer, one of {batches}
  "current_semester": integer 1-10, consistent with batch_year (as of October 2026, 2023 -> 7, 2024 -> 5)
  "cgpa":             number 0.00-10.00 with two decimals, realistic distribution (mostly 6.5-9.0)
Rules: no real people, no real roll numbers, spread students evenly over programmes and batches,
no duplicate names, do not use ids S9000-S9999. Output JSON only, no prose.
```

Validation applied to the reply (`validate()` in the script): Pydantic types and ranges, exact count, unique ids and
names, programme/batch from the allowed lists, reserved ids rejected.

## 2. Provenance of the shipped seed

`data/seed/students_seed.csv` was exported from the repository's original `database/university.db` (an AI-assisted
dataset). The prompt and model used for that original draft are **not recorded**. Team members who ran it should
paste the exact prompt and model name here before submission:

```
TEAM TO FILL IN: prompt, model name and version, temperature, number of calls
```

Everything else in the dataset (attendance, results, backlogs, extra courses, edge-case students) is produced by
`scripts/generate_students.py` without an LLM.
