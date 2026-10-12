# Retrieval and Answer Checks

The current diagnostic set has **50 authored questions and 95 fictional messages**.
It covers multi-group interference, namesakes, corrections, multi-turn references,
cross-chat evidence and missing answers. It is not a production accuracy benchmark.
The original 16-question / 40-message fixture remains for backwards comparisons.
No private conversations are needed.

## Current Comparison

```bash
# No cloud calls. All 50 questions, keyword retrieval only.
python scripts/evaluate_search_v2.py --output artifacts/search-v2

# Paid opt-in: keyword + answer, hybrid + answer, production native-tool Agent.
python scripts/evaluate_search_v2.py --live-config .demo/showcase/models.json \
  --modes keyword,hybrid,agent --output artifacts/search-v2-live
```

Use `--cases E08,E17,E22,E24,E35,E45,E49,E50` for the diagnostic subset or
`--judge-model` for another judge supported by the configured provider.
All modes use the same final raw-message cap (default 20). Agent exploration can
consume more messages/calls; this is not an equal-token-budget comparison.
Gold evidence is used **only for scoring**, never query construction or routing.

The report records final-context recall, structured answer correctness, expected
fact completeness, citation support, end-to-end latency, tokens and estimated
costs (indexing, answering and judging separately). Failed calls and unreviewed
answers stay visible. Unknown usage/cost is not treated as zero. Offline runs
do not claim answer-quality scores. API diagnostics record error classes and
HTTP status only, never provider response bodies or credentials.

See [the current findings and limitations](../docs/SEARCH_EVALUATION_V2.md).
Manual-review fields are intentionally blank until a person reviews them.

## Offline Run

From the repository root, after installing the project requirements:

```bash
python scripts/evaluate_retrieval.py --output artifacts/eval
```

The script imports 30 messages, appends 10 and runs the application's actual
incremental indexer and local retrieval path. Embedding, LLM reranking and answer
generation are disabled. The output contains `REPORT.md`, per-question evidence
and API usage metadata in the ignored `artifacts/` directory.

## What Is Measured

- Whether a retrieved item belongs to an annotated conversation.
- Whether annotated evidence messages appear in the selected context.
- Whether incremental import/indexing adds only the new records.

The no-evidence question is excluded from positive retrieval-hit denominators.
Valid source IDs do not prove that a generated claim is supported by its source.
Answer completeness and citation support require separate semantic review.

## Current Answer Quality

The [legacy answer quality check](../docs/ANSWER_QUALITY.md) runs the structured
Q&A handler and grades correctness, completeness and citation support. It also
records end-to-end latency and estimated API cost. Its small synthetic results
are explicitly model-graded, not a production accuracy benchmark.

```bash
python scripts/evaluate_answers.py --live-config .demo/showcase/models.json
```

This is a paid opt-in run. Use `--cases E03,E04,E08,E10,E12,E13,E14,E16` to
reproduce the documented subset; omitting it runs all 16 cases.

## Optional Cloud Run

```bash
python scripts/evaluate_retrieval.py --live-config .demo/showcase/models.json --output artifacts/live-eval
```

This explicitly enables paid calls using the supplied configuration and synthetic
data only. Enabled embedding/reranking stages are exercised. Its answer generation
uses a legacy plain-text prompt, not the current UI's structured-citation path.

To exercise the current Q&A handler or tool-calling task with a separately
configured demo profile:

```bash
python scripts/run_demo_task.py --root .demo/showcase --mode qa --live-config .demo/showcase/models.json --output artifacts/demo-qa
python scripts/run_demo_task.py --root .demo/showcase --mode task --live-config .demo/showcase/models.json --output artifacts/demo-task
```

See [the demo guide](../docs/DEMO.md) for sample setup. Review credentials and
provider costs before opting into cloud calls. Generated task cards stay paused;
the helper executes once, not on a paid schedule.

## Repository Policy

Keep test cases, annotations, scripts and synthetic fixtures under version control.
Do not commit generated provider responses, usage logs, checkpoints or personal
model settings. Results belong in `artifacts/`; legacy `eval/results/` is also
ignored. Older results remain in Git history, not the current publication tree.
