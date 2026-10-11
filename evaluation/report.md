# Evaluation report

Questions: 59 (`evaluation/questions.json`). Method: exact/substring match against expected answers, section-level citation check against the stored chunk text, literal tool-output comparison. LLM mode: `mock` (deterministic templates), so the numbers isolate retrieval, precedence, tools and routing. Corpus: synthetic fixtures (`corpus/fixtures`).

## Configuration comparison

| Config | Description | Answer correctness | Citation accuracy | Abstention accuracy | Tool-result correctness | Retrieval hit rate | p50 / p95 latency (ms) |
|---|---|---|---|---|---|---|---|
| A (default) | bge-small, section chunks, k=5 | 98.3% (59) | 96.3% (27) | 100.0% (59) | 100.0% (29) | 100.0% (22) | 61.2 / 108.8 |
| B | all-MiniLM-L6-v2, section chunks, k=5 | 89.8% (59) | 77.8% (27) | 94.9% (59) | 100.0% (29) | 100.0% (22) | 38.0 / 121.0 |
| C | bge-small, fixed 400-char chunks, k=5 | 74.6% (59) | 3.7% (27) | 98.3% (59) | 86.2% (29) | 100.0% (22) | 35.3 / 106.0 |
| D | bge-small, section chunks, k=2 | 96.6% (59) | 96.3% (27) | 100.0% (59) | 100.0% (29) | 86.4% (22) | 33.0 / 94.4 |

Answer correctness by question set (see evaluation/README.md for how these sets were built):

| Config | Original 39 (written alongside the system) | Paraphrase round 1 (10) | Paraphrase round 2 (10) |
|---|---|---|---|
| A | 100.0% | 100.0% | 90.0% |
| B | 97.4% | 80.0% | 70.0% |
| C | 79.5% | 60.0% | 70.0% |
| D | 97.4% | 100.0% | 90.0% |

LLM calls and tokens per question: 0 / 0 in mock mode (see README for the Ollama cost profile).

## Config A: bge-small, section chunks, k=5

Ingest time 32.2 s. Correct by category: authorisation 5/5, clarification 1/1, multi_step 4/4, not_answerable 7/7, personal_data 7/7, personal_eligibility 13/13, policy_fact 13/14, procedure 3/3, safety 1/1, version_conflict 4/4

| id | question | expected | actual | note |
|---|---|---|---|---|
| H12 | What happens to a student who is below the attendance requirement? | retrieved_fact | retrieved_fact | contains |

## Config B: all-MiniLM-L6-v2, section chunks, k=5

Ingest time 8.5 s. Correct by category: authorisation 5/5, clarification 1/1, multi_step 4/4, not_answerable 7/7, personal_data 7/7, personal_eligibility 13/13, policy_fact 10/14, procedure 3/3, safety 1/1, version_conflict 2/4

| id | question | expected | actual | note |
|---|---|---|---|---|
| Q08 | Is 65% attendance enough to sit the exams? | retrieved_fact | retrieved_fact | contains |
| H02 | min % of marks to clear a subject? | retrieved_fact | not_found | type; contains |
| H04 | is attendance of 70 percent okay for exams | retrieved_fact | retrieved_fact | contains |
| H11 | What is the least attendance needed to take end sem papers? | retrieved_fact | not_found | type; contains |
| H12 | What happens to a student who is below the attendance requirement? | retrieved_fact | retrieved_fact | contains |
| H13 | Can a medical case get the attendance shortfall condoned? | retrieved_fact | not_found | type; contains |

## Config C: bge-small, fixed 400-char chunks, k=5

Ingest time 2.1 s. Correct by category: authorisation 5/5, clarification 1/1, multi_step 4/4, not_answerable 6/7, personal_data 7/7, personal_eligibility 9/13, policy_fact 7/14, procedure 2/3, safety 1/1, version_conflict 2/4

| id | question | expected | actual | note |
|---|---|---|---|---|
| Q01 | What is the minimum attendance required to appear for end-semester exa | retrieved_fact | retrieved_fact | contains; forbidden |
| Q02 | What is the minimum attendance required to appear for end-semester exa | retrieved_fact | retrieved_fact | citation |
| Q03 | What is the minimum attendance required to appear for end-semester exa | retrieved_fact | retrieved_fact | contains |
| Q04 | How many credits and what CGPA do I need to be awarded the B.Tech degr | retrieved_fact | retrieved_fact | contains |
| Q05 | What percentage of marks is needed to pass a course? | retrieved_fact | retrieved_fact | citation |
| Q06 | What are the grade points for grade A? | retrieved_fact | retrieved_fact | citation |
| Q07 | How do I apply for the supplementary exam? | retrieved_fact | retrieved_fact | contains |
| Q08 | Is 65% attendance enough to sit the exams? | retrieved_fact | retrieved_fact | contains |
| Q09 | What is the re-evaluation deadline? | conflict_flagged | conflict_flagged | citation |
| Q10 | What is the re-evaluation deadline? | conflict_flagged | conflict_flagged | citation |
| Q11 | What is the library fine for late books? | retrieved_fact | retrieved_fact | citation |
| Q12 | Within how many days must the hostel fee be paid after allotment? | retrieved_fact | retrieved_fact | citation |
| Q16 | What is the tuition fee for the M.Tech programme? | not_found | retrieved_fact | type; contains |
| Q21 | Am I eligible to appear in the exam for Data Structures? | calculated | calculated | contains |
| Q22 | Am I eligible to appear in the exam for Data Structures? | calculated | calculated | citation |
| Q24 | Am I eligible to sit the Operating Systems exam? | calculated | calculated | contains |
| Q25 | Am I eligible for the supplementary exam in Mathematics? | calculated | calculated | citation |
| Q28 | Am I eligible for campus placement? | calculated | calculated | citation |
| Q31 | I failed Data Structures. If I pass the supplementary, will I be eligi | calculated | calculated | citation |
| H01 | how many classes must i attend to be allowed in the semester exam | retrieved_fact | retrieved_fact | contains |
| H02 | min % of marks to clear a subject? | retrieved_fact | retrieved_fact | contains |
| H03 | what's the process for getting the supplementary exam form | retrieved_fact | retrieved_fact | citation |
| H04 | is attendance of 70 percent okay for exams | retrieved_fact | retrieved_fact | contains |
| H05 | When do I have to pay the hostel fee? | retrieved_fact | retrieved_fact | citation |
| H06 | What's the cutoff CGPA for sitting in campus placements? | retrieved_fact | retrieved_fact | citation |
| H07 | am i allowed to write the OS exam? | calculated | calculated | contains |
| H11 | What is the least attendance needed to take end sem papers? | retrieved_fact | retrieved_fact | contains |
| H12 | What happens to a student who is below the attendance requirement? | retrieved_fact | retrieved_fact | contains |
| H13 | Can a medical case get the attendance shortfall condoned? | retrieved_fact | retrieved_fact | citation |
| H14 | Which office do I submit the supplementary form to? | retrieved_fact | retrieved_fact | citation |
| H17 | will I be allowed to sit the DS exam? | calculated | calculated | contains |

## Config D: bge-small, section chunks, k=2

Ingest time 2.2 s. Correct by category: authorisation 5/5, clarification 1/1, multi_step 4/4, not_answerable 7/7, personal_data 7/7, personal_eligibility 13/13, policy_fact 13/14, procedure 2/3, safety 1/1, version_conflict 4/4

| id | question | expected | actual | note |
|---|---|---|---|---|
| Q07 | How do I apply for the supplementary exam? | retrieved_fact | retrieved_fact | contains |
| H12 | What happens to a student who is below the attendance requirement? | retrieved_fact | retrieved_fact | contains |
