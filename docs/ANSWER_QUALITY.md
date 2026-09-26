# Answer Quality Checks

This is a small, reproducible synthetic smoke check, not a production accuracy
claim. It complements the [retrieval checks](../eval/README.md) by running the
current UI Q&A handler and examining generated answers, not just retrieved IDs.

## Method

- Import all 40 fictional messages and build the local full-text index.
- Run the real structured-answer handler, including retrieval, citation validation
  and any answer repair. Select up to 30 context messages. Embedding and API
  reranking are disabled for this check.
- Ask a model to check each answer paragraph against the full fictional corpus
  for correctness, and each annotated expected fact for completeness.
- Check citation support only against the sources cited by that paragraph.
  Valid citation IDs alone do not establish support. Speaker and group/private
  attribution must also agree.
- Exclude uncited limitation/abstention paragraphs from the citation-support
  denominator, while still grading their factual correctness.
- Reject incomplete judge output. Report failed generation and unreviewed answers
  separately; empty denominators are not reported as perfect scores.

## Recorded Run

On September 26, 2026, both answer generation and judging used `gpt-5-mini`.
Eight cases were selected from the 16-case fixture before running the check:

| Cases | Coverage |
| --- | --- |
| E03, E04 | Cross-conversation advice and constrained job search |
| E08 | Combining private and group evidence |
| E10 | A newer correction superseding an earlier arrangement |
| E12, E13 | Pending follow-up versus an already-sent reply |
| E14, E16 | Missing details and a completely absent entity |

| Measure | Observed result |
| --- | --- |
| Generation / review completion | 8/8, with no failed or unreviewed cases |
| Model-graded paragraph correctness | 18/18 |
| Model-graded expected-fact completeness | 18/18 |
| Model-graded cited-paragraph support | 16/16 |
| End-to-end answer latency | Median 8.89s; maximum 32.94s |
| Answer and judge API calls | 16 |
| Reported tokens, including judging | 66,446 |
| Estimated answer + judge cost | USD 0.052523 |

Latency measures the UI handler's retrieval-through-completion wall time,
including retries, but excludes the judge. It is not time to first token.
Cost uses [standard model list prices](https://developers.openai.com/api/docs/models/gpt-5-mini)
checked on September 26, 2026, including cached-input pricing. It is an estimate,
not an invoice, and excludes the separately recorded demo video.

## Limits and Next Checks

All checked items passed the automatic rubric in this run. That does **not**
mean the application is universally accurate: the corpus is tiny, cases are
known development fixtures, and using the same model for answering and judging
introduces correlated bias. No human-labeled gold benchmark or multi-run
confidence interval is claimed.

The run does not cover all 16 questions, production-scale hybrid retrieval,
long multi-turn conversations, or scheduled-task answer quality. A factually
supported answer can still contain unnecessary advice or stale information;
the rubric is not a full assessment of relevance, brevity or usefulness.

Next checks should add held-out questions, independent human citation review,
multiple runs, time-sensitive queries, and live hybrid-retrieval comparisons.
Keep failures and their sources for diagnosis rather than reporting only an
aggregate score.

## Reproduce

After installing the project requirements, supply an explicit local model
configuration. This command makes paid API calls using fictional data only:

```bash
python scripts/evaluate_answers.py \
  --live-config .demo/showcase/models.json \
  --cases E03,E04,E08,E10,E12,E13,E14,E16
```

Omit `--cases` to run all 16. Use `--judge-model` for a different model supported
by the same configured endpoint. No key is copied into the temporary fixture.
Detailed answers, per-item reasons and API usage stay in the ignored
`artifacts/answer-quality/` directory. Unknown model prices or missing usage are
reported as unknown costs, not zero.
