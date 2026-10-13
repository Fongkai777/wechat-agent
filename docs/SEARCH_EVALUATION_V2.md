# Search Validation v2

This is an engineering diagnostic, not a production accuracy claim. All messages,
people, organizations and annotations are fictional. The corpus contains 95
messages and 50 authored questions, including 25 deliberately competing messages
in another group. Five questions have no supporting evidence.

## Scope Regression

The old tool retrieved global top-N candidates and only then filtered the group.
The new path applies conversation IDs/type/title, verified people and time bounds
inside FTS, LIKE, contact supplementation and semantic candidate selection, before
ranking or LIMIT. Semantic chunks must contain a mapped message satisfying all
constraints; raw-message hydration still checks boundaries again.

`tests/test_scoped_retrieval.py` proves that a target message survives top-20
retrieval among 25 other-group distractors, including higher-scoring vectors.
The fix does not raise the result limit. Group sender filtering and the index's
minimum timestamp are also covered.

## Measurement Protocol

- Keyword: raw-question FTS/LIKE with reciprocal-rank fusion, then the same answer generator.
- Hybrid: one production expanded query with embedding, lexical supplementation and reranking.
- Agent: the production native-tool search loop, then structured generation.
- Final answer context cap: 20 raw messages in each mode. Exploration budgets differ;
  report latency and tokens alongside quality, not an equal-budget superiority claim.
- Gold evidence never enters search/planning. The judge receives the fictional
  corpus for correctness, and only cited evidence for citation support.
- The same `gpt-5-mini` answers and judges unless explicitly overridden. This is
  correlated model grading, not independent human review.
- Provider response bodies and credentials are excluded from diagnostics. Token
  usage and error categories are retained. Failed calls have unknown cost.

Reproduction commands and output fields are in [eval/README.md](../eval/README.md).
The original top-8 legacy evaluator is kept separately; do not compare its score
directly with top-20 v2 on a different corpus.

## Offline Findings: October 12, 2026

All 50 cases ran without any API calls. At top-20, 41 of 45 answerable questions
contained all annotated evidence; mean evidence recall was 93.3%. Failures:

- E19: cross-group assessment arrangements, missing one group's source.
- E34: cross-group latency comparison, missing one group's source.
- E49: a correction follow-up without the subject in the latest question.
- E50: “the other group?” without the subject in the latest question.

These are final-context recall figures, not answer accuracy. No-answer cases are
excluded from recall; abstention requires live generation/judging to measure.
The small corpus and generous top-20 budget make many cases easy.

## E08 Bad-Case Analysis

The original evaluator still reproduces E08 at 50% evidence recall: it includes
message 14 (vegan preparation) but omits message 12 (Monday closure). The trace
has 17 candidates, so it is not a missing-index problem. Broad food expansion
and the local ranking place unrelated restaurant questions above the closure
message; the final top-8 loses one half of the answer.
The new keyword and hybrid runs retrieved both sources, but this alone does not
prove a fix: their corpus and final-context budget differ from the legacy run.

The live native Agent exposed a different failure: it interpreted “Monday” in
“Is the restaurant open on Monday?” as a **message timestamp filter**, produced
a future start date and returned a clarification instead of searching. This is a
planning error before recall, not evidence that the index lacks the messages.
This motivated removing the mandatory planner, rather than adding weekday-specific
prompt rules. New turns call tools directly using dialogue history and optional
per-call filters. Semantic interpretation remains fallible and needs evaluation.

## Live Baseline and Targeted Follow-Up

Before the tool-first change, eight paired cases (E08, E17, E22, E24, E35, E45,
E49, E50) completed across all three modes. Seven have annotated evidence.
The first run hit API/network failures; the remaining six cases were rerun.
Failed attempts remain in local reports and cost accounting, not silently discarded.

| Pre-change mode | Mean evidence recall | Expected facts covered | Median answer latency |
|---|---:|---:|---:|
| Keyword | 71.4% | 13/16 | 5.9s |
| Hybrid | 85.7% | 13/16 | 21.4s |
| Planner-first Agent | 28.6% | 7/16 | 13.7s |

The Agent often stopped before retrieving because of premature date/name
clarification, so its short median is not a performance win. The initial judge
also gave a false positive when an answer described a real but wrong event in a
follow-up. The rubric now explicitly checks the target established by the question
and history; old paragraph correctness scores are not treated as reliable accuracy.

After removing mandatory slots, a targeted live run repeated E08 (Monday closure),
E17 (one group only), E24 (corrected weekend event) and E49 (follow-up correction).
All four found every annotated source; the model judge marked all eight expected
facts covered and all eight cited paragraphs supported. One of thirteen total
paragraphs was marked incorrect because the judge conflated the number of sources
included in context with the number cited. Its raw grade is retained, not adjusted.
This four-case development check does **not** establish general accuracy or a fair
full-suite comparison. Median retrieval-plus-answer latency was 59.2s (max 68.0s).

The targeted run reported 118,168 answer/retrieval/judge tokens and an estimated
$0.048961 in model costs, plus $0.000184 for the synthetic index. These are
standard-price estimates, not an invoice. The earlier baseline/retry attempts
had 22 failed calls with unknown usage, so no complete total is claimed for them.
Reproduction: run `scripts/evaluate_search_v2.py --modes agent --cases E08,E17,E24,E49`
with the explicitly authorized `--live-config` and an ignored output directory.

Unit coverage additionally verifies no planner call, non-inherited date/person/group
conditions, identity discovery, task-window enforcement, context boundaries and
v1/v2/v3 checkpoint compatibility. Latest local checks: 370 Python tests including
macOS sandbox tests in UTC, and 130 Node tests. Hosted CI has not yet run these edits.

## Skill Execution Validation

CI now has a `macos-14` job with `TEST_CODE_SKILL_SANDBOX=1`; Linux tests alone do
not validate the macOS sandbox. Local macOS verification ran 33 tests successfully:
host-file reads, writes, network access to a real listening endpoint, process
creation, timeout/interruption, persisted recovery, and completed paid-call receipt
reuse. Models are mocked for recovery tests; no paid request is needed in CI.
The new hosted CI job has not been claimed as passed before it runs on GitHub.

## SQL Materialization Benchmark

`python scripts/benchmark_skill_sql.py` measures five identical aggregate queries
over 100 synthetic chats. Both paths now use the production capability boundary:
one reconstructs the snapshot on every query, the other reuses it. Local single-run
results from October 13, 2026, including first-query setup:

| Messages | Rebuild on each query | Reuse one snapshot |
|---|---:|---:|
| 10,000 | 0.652s | 0.127s |
| 50,000 | 3.508s | 0.634s |
| 100,000 | 6.571s | 1.304s |

All result rows matched. Unlike the earlier prototype, both paths include
authorization and provenance bookkeeping. This is a SQL-only measurement, not
an end-to-end Agent speedup or a historical-version comparison. It does not
measure retained memory, concurrent workloads or recovery latency.

Production now reuses one lazy read-only snapshot for an identical execution
window. Changing the window replaces it; completion/failure closes it. Tests
cover Python/native parity, source-ledger equivalence, warm-query write/file
denial, scope isolation, cross-thread reuse and connection cleanup. Different
tasks never share a connection; snapshots are rebuilt after process restart.

## Remaining Validation

The 50 cases are authored development fixtures, not held-out questions. Add
independent human review and repeated runs before claiming general accuracy.
Skill safety/recovery tests do not measure the semantic quality of generated
task programs. Unknown API costs and incomplete model reviews must remain visible.
