# Evaluation: method, results, and how far to trust them

Run: `python scripts/evaluate.py` (all four configurations, ~1-2 min) or `python evaluation/run_eval.py --configs A`.
Outputs: `report.md` (tables) and `results.json` (every question, answer, check). `tests/test_semantic_eval.py`
(marked `slow`) fails the build if configuration A drops below the quality bar.

## What is measured (section 7 of the guide)

| Metric | Definition |
|---|---|
| Answer correctness | `answer_type` equals expected **and** every expected substring is in the answer **and** no forbidden substring is (numbers and dates matched literally); multi-step questions must also state assumptions |
| Citation accuracy | over questions with an expected source: the response cites the expected `(doc_id, section)` with a page **and** the stored text of that section contains the evidence phrase |
| Abstention accuracy | `not_found` exactly when expected: unanswerable questions abstain, answerable ones do not |
| Tool-result correctness | the expected tool ran and its output fields equal the expected values (dotted paths into the JSON output); refused/clarification questions must call no tool |
| Retrieval hit rate | the expected document appears in the raw top-k chunks, before precedence is applied (read from the audit record) |
| Latency / cost | p50 and p95 wall time of the in-process pipeline; LLM calls and tokens per question from the audit record |

No LLM judge or human rubric is used: every check is a deterministic comparison, so a run is reproducible.
`LLM_MODE=mock`: explanations are deterministic templates, so the numbers isolate retrieval, precedence, routing and
tools. They do **not** measure the quality of Ollama-written explanations (those are validated for grounded numbers at
run time, but a rubric over real model output has not been run).

## Question set (59)

| Group | Count | Notes |
|---|---|---|
| Policy facts / procedure | 13 + 3 | incl. table lookup (grade points), scanned-page OCR (hostel fee), as-of dates before/at/after circulars |
| Version / conflict / safety | 4 + 1 | FAQ vs circular vs regulation; two ECE notices that cannot be resolved (`conflict_flagged`); a document that contains an embedded instruction |
| Not answerable | 7 | incl. a question that names a course the student does not have |
| Personal data (tools) | 7 | |
| Personal eligibility (tools) | 13 | exact thresholds (75 %, 80 %, CGPA 6.50), one class below, detained, absent, already cleared, outside the batch scope |
| Multi-step / what-if | 4 | supplementary -> placement (two outcomes), extra classes -> exam, hypothetical CGPA |
| Other-student / no identity / clarification | 5 + 1 | |

Sets: **Q01-Q39** were written alongside the system (they, and the data, shaped it). **H01-H10** and **H11-H20** are
paraphrases written afterwards in two rounds to estimate generalisation (different wording, abbreviations, word order).

## Results (synthetic fixture corpus, 59 questions)

See `report.md` for the generated tables. Summary of the default configuration **A** (bge-small, section chunks, k=5):
answer correctness 98.3 %, citation accuracy 96.3 %, abstention 100 %, tool results 100 %, retrieval hit rate 100 %.

| Config | Answer correctness | Citations | Abstention | Retrieval hit | Note |
|---|---|---|---|---|---|
| **A** bge-small, section chunks, k=5 | **98.3 %** | **96.3 %** | **100 %** | 100 % | chosen |
| B all-MiniLM-L6-v2, section, k=5 | 89.8 % | 77.8 % | 94.9 % | 100 % | weaker on paraphrases and on ranking the clause that carries the number |
| C bge-small, fixed 400-char chunks, k=5 | 74.6 % | 3.7 % | 98.3 % | 100 % | retrieval still finds the right document, but chunk labels do not align to clauses, so citations point at the wrong section and rule extraction loses clause numbers |
| D bge-small, section chunks, k=2 | 96.6 % | 96.3 % | 100 % | 86.4 % | k=2 misses multi-clause procedures and the second source needed for conflicts |

Decision: **A**. The final choice rests on these numbers: same retrieval hit rate as B but higher correctness/citation
accuracy; chunking by clause is the single biggest lever (A vs C); k=5 vs k=2 costs ~nothing in latency and buys 14
points of retrieval hit rate.

## How much to trust the numbers (read this)

1. **The first-run scores on questions the system had not seen were much lower**, which is the honest generalisation
   estimate: paraphrase round 1 scored **60 %** and round 2 **80 %** on their first run with configuration A.
   Analysing the failures found real defects, fixed afterwards: a personal-eligibility pattern firing on "how many classes
   must I attend to be allowed...", conflicts between irrelevant low-ranked passages being reported, a precedence window
   that dropped the overriding circular, "percent" / "okay" counted as unmatched terms, "all students" refusing policy
   questions, abstaining when a question says "least" instead of "minimum". Re-running after the fixes gives the
   98.3 % above. Because the same people fixed and re-ran, **those two sets are no longer held out**; only their
   first-run numbers are an unbiased estimate. Round 2 (written after round 1 had been fixed) is the better of the two
   (80 % first run).
2. **Small corpus, synthetic documents, one author.** 9 short fixtures; phrasing in real regulations will be harder
   (long clauses, cross-references, tables of percentages). Expect lower numbers on real documents; add real-document
   questions to `questions.json` and re-run before relying on any figure.
3. **Thresholds are tuned on this corpus** (`MIN_SIMILARITY`, `STRONG_SIMILARITY`, `MIN_TERM_COVERAGE`): there is a
   0.58 vs 0.67 gap in best-passage similarity between the most "answerable-looking" unanswerable question and the
   least similar answerable one. That margin is thin.
4. Known remaining failure: H12 ("What happens to a student who is below the attendance requirement?") retrieves the
   supplementary-exam clause instead of the detention clause (7.3); dense retrieval confuses them. Reported, not hidden.
5. Latency is wall time of the in-process pipeline on a laptop CPU, mock LLM. With Ollama (7-8B) expect seconds per
   answered question (1 planning call + 1 explanation call, ~0.5-2.5 k tokens).
