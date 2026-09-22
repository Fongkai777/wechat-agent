# A Small, Auditable Evaluation

This is a **16-question synthetic benchmark**, not evidence of production accuracy.
The 40 messages in [the fixture](../examples/chats.json) are wholly fictional.
None were copied or paraphrased from private chats. The initial import has 30
messages; a second import adds 10 and exercises the actual incremental indexer.

## Run Without Credentials

```bash
python scripts/evaluate_retrieval.py
```

This invokes the application's real query planner, SQLite FTS/LIKE retrieval,
contact signals and local/RRF ranking. Embedding and LLM reranking are disabled.
See [measured results](results/local/REPORT.md) and [per-question evidence](results/local/results.json).
No cloud request is made. Results are not a measurement of the full hybrid pipeline.

## Optional Live Evaluation

```bash
python scripts/evaluate_retrieval.py \
  --live-config .demo/models.json --output artifacts/live-eval
```

Explicitly opting in uses the specified model configuration, sends **synthetic
messages only**, and incurs provider charges. It never indexes the private
database. Enabled embedding and reranking stages are exercised as configured.
The answer model is called once per case. Review `usage` before running larger sets.
Do not commit raw provider error dumps or a configuration containing credentials.

The [completed live run](results/live/REPORT.md) used 49 API calls and 140,292
provider-reported tokens, including index/query embeddings and reranking. All
15 answerable cases retrieved their annotated evidence. See the
[qualitative answer review](results/live/REVIEW.md) for counting ambiguity and a
separate end-to-end source-attribution failure. The review is by Codex; independent
human review remains open.

`api_usage.json` records every completed call's model, stage, duration and usage
without request bodies or keys. `checkpoint.json` preserves completed questions
if a later request fails. The script does not automatically resume or retry a
failed run. For the total including setup and demo calls, see
[the usage ledger](results/API_USAGE.md).

## What the Checks Mean

| Check | Method | Limitation |
|---|---|---|
| Correct conversation | At least one annotated chat appears in top 8 | Lenient; not enough to answer the question |
| Evidence recall | Retrieved annotated message IDs / all annotated IDs | Depends on the human-authored labels |
| All evidence found | Every annotated message is present in top 8 | Measures coverage, not order or relevance of extra hits |
| Citation validity | Live answers' numeric references point into supplied context | Not proof that a citation supports the claim |
| Correct context cited | Human compares each claim and source | `null` until reviewed |
| Answer completeness | Human checks `facts` and uncertainty in each case | `null` until reviewed; no fabricated score |

The no-evidence question is excluded from positive retrieval-hit denominators.
It tests abstention only after live generation and review. Do not treat a low
retrieval score as a measured abstention result.

For semantic results, source message IDs are resolved through the chunk-to-message
mapping. Top-k counts items, so an 8-chunk context can contain more messages than
8 individual local hits. Use a fixed token budget for a controlled comparison.

## Review Rubric

For each answer fill `manual_review` in a copied results file:

- `correct_context_cited`: true only when every factual claim is supported;
  mark false for an unrelated or invented reference.
- `answer_complete`: true only when all applicable `facts` are addressed;
  missing information must be stated as unknown, not guessed.
- `notes`: describe missing evidence, entity/time confusion, bad ordering,
  duplicated opportunities, unsupported inference, or incomplete follow-up.

E03/E08 exercise cross-chat context; E04 exclusions; E05 incremental import;
E06 duplicate postings; E10 corrections; E12/E13 reply context; E14 unknown
salary; E15 an ambiguous follow-up; E16 an absent fact. E15 is intentionally
not rewritten by the harness: the current application's retrieval uses the
latest question, while conversation history is passed only to generation.

## Next Experiments

Keep the same questions and labels for a local-only / embedding / hybrid /
hybrid-plus-rerank ablation. Expand to independently authored questions, report
token usage and latency by stage, and add time-bound multi-turn query rewriting.
Do not tune on all 16 questions and then call the same set a held-out evaluation.
