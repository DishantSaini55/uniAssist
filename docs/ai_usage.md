# AI-usage disclosure

AI coding assistants are allowed and encouraged by the guide; this page says which parts they produced and how that
output was verified.

## Assistants used

| Assistant | Used for |
|---|---|
| Claude Code (model: Claude Sonnet 5.5) | The completion pass in this repository: API contract (`/ask`, `/ingest`, `/audit`, `/health`, `/sources`, `/admin/students`), LangGraph pipeline, Annex A precedence engine, document parsing/chunking/OCR, rule registry and rule extraction, deterministic tools and what-if simulations, safety guards, evaluation harness and question set, test suite, synthetic-data generator and validators, frontend update, Docker files, documentation. |
| An earlier AI assistant (not recorded in the repo) | The original skeleton the pass started from (basic tools, first React dashboard, first synthetic database). `TEAM TO FILL IN` the tool/model if known. |

## How the output was verified

* **Executed, not just read.** Every module was run: 88 tests (`pytest`: 87 unit/API-contract/live-ingestion/authorisation/data-kit tests, plus `pytest -m slow` evaluation gate), the evaluation harness
  with the real embedding models, an end-to-end `uvicorn` + `curl` session, and a production build of the frontend. The Docker image build was started but not completed (slow network), so it is unverified.
* **Bugs the verification caught** (and that were fixed rather than papered over): a regex that silently lost its word
  boundary; sentences broken by PDF line wraps hiding thresholds from the extractor; session names sorted
  alphabetically instead of chronologically; precedence only comparing passages that happened to retrieve well; an
  over-eager "someone else's data" refusal that also blocked policy questions; an abstention rule that rejected a
  correct answer because the question said "least" instead of "minimum".
* **Held-out questions.** 20 paraphrased questions were written after the system worked, in two rounds, to measure
  generalisation. First-run results before any fix: round 1 = 60 %, round 2 = 80 % (see `evaluation/README.md`).
  Failures were then analysed; real bugs were fixed and the sets re-run, so those numbers are no longer held-out.
* **Thresholds are read from data.** `tests/test_tools.py::test_rule_thresholds_are_not_hard_coded` edits the rule
  registry and checks the decision flips; `tests/test_api.py::test_live_ingestion_changes_answers_without_restart`
  ingests a new circular while the API is running and checks answers change.
* **The LLM never decides anything authoritative.** Numbers in an LLM explanation are checked against the evidence
  it was given (`llm/client.py::numbers_grounded`); tool plans are validated against the tool registry; any failure
  falls back to deterministic text. This path was tested with a stubbed model (no Ollama was installed on the
  development machine, so **the live Ollama path has not been exercised end to end** - see README limitations).

## Things a team member must be able to explain

Each member should be able to explain the parts they own (see `docs/team_contribution.md`). The riskiest to
explain are the ones with the most logic: `rag/precedence.py` (Annex A), `rag/claims.py` (what counts as a comparable
claim), `tools/rules.py` (how the registry is read) and `graph/pipeline.py` (why a fixed graph).
