# Retrieval Evaluation

Mode: **offline-local-only**. Top-k: **8**.

40 wholly fictional messages / 6 chats / 16 authored questions; not a production benchmark

Correct-chat hit: **15/15**. All annotated evidence present: **14/15**.
Incremental import: **10** new messages indexed; initial corpus 30 messages.

| Case | Scenario | Correct chat | All evidence | Recall | Answer completeness |
|---|---|---|---|---|---|
| E01 | exact entity | yes | yes | 100% | not reviewed |
| E02 | neighbor evidence | yes | yes | 100% | not reviewed |
| E03 | cross-chat | yes | yes | 100% | not reviewed |
| E04 | negative constraint | yes | yes | 100% | not reviewed |
| E05 | incremental import | yes | yes | 100% | not reviewed |
| E06 | deduplication | yes | yes | 100% | not reviewed |
| E07 | food retrieval | yes | yes | 100% | not reviewed |
| E08 | cross-chat | yes | no | 50% | not reviewed |
| E09 | updated fact | yes | yes | 100% | not reviewed |
| E10 | correction | yes | yes | 100% | not reviewed |
| E11 | responsibility | yes | yes | 100% | not reviewed |
| E12 | actionable reply | yes | yes | 100% | not reviewed |
| E13 | answered conversation | yes | yes | 100% | not reviewed |
| E14 | unknown value | yes | yes | 100% | not reviewed |
| E15 | multi-turn reference | yes | yes | 100% | not reviewed |
| E16 | no evidence | n/a | n/a | n/a | not reviewed |

## Interpretation

These are retrieval measurements, not LLM answer-quality scores. Offline mode makes no model requests. Answers, citation support and completeness remain unevaluated until a live run and human review.

## Failed Retrieval Cases

- **E08** (青叶食堂可以吃纯素吗？周一开门吗？): missing annotated messages [12].

## Limits

- Small authored synthetic dataset; not representative of private history.
- Conversation hit is lenient; all-evidence hit requires every annotated message in top-k.
- Citation range checks do not establish semantic support. Human review is required.
- Multi-turn history is passed to generation, but current retrieval sees the latest question only.
