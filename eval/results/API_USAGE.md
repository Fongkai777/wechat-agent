# API Usage Ledger

All calls below sent only the fictional sample and its evaluation questions.
Existing model credentials were read locally, never copied into these artifacts.
These numbers cover this evaluation/demo session, not total account usage.

| Run | Recorded calls | Provider-reported total tokens |
|---|---:|---:|
| Initial preflight, stopped by a local chunk-ID mapping error | 3 | 12,425 |
| Full 16-question hybrid evaluation | 49 | 140,292 |
| First task take, using the helper's initial same-day default | 2 | 6,429 |
| Corrected explicit 30-day task take | 2 | 8,800 |
| Actual UI-handler Q&A demonstration | 4 | 17,285 |
| Structured-citation regression (index, retrieval and Q&A) | 4 | 19,821 |
| **Total** | **64** | **205,052** |

The first task take is retained in `task-today/`; it is not presented as a
30-day run. The helper now sets `range_type=recent_days` explicitly.

| Model | Total tokens |
|---|---:|
| text-embedding-3-small | 20,710 |
| gpt-5-nano (reranking) | 131,802 |
| gpt-5-mini (answers/tasks) | 52,540 |

Raw per-call ledgers live alongside each run as `api_usage.json`; the interrupted
preflight is `live/api_usage.preflight.json`. Usage comes from the provider's
response, not character-count estimates. Reasoning/cache detail is preserved
where provided. This is **not a currency-cost estimate**: different models and
input/output/cache categories have different prices.
