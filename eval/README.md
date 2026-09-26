# Retrieval and Answer Checks

The repository includes 16 labeled questions and 40 fictional messages for
repeatable retrieval checks. This is a small synthetic fixture, not a production
accuracy benchmark. No private conversations are needed.

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

The [answer quality check](../docs/ANSWER_QUALITY.md) runs the current structured
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
