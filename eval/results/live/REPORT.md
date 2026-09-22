# Retrieval Evaluation

Mode: **live-config**. Top-k: **8**.

40 wholly fictional messages / 6 chats / 16 authored questions; not a production benchmark

Correct-chat hit: **15/15**. All annotated evidence present: **15/15**.
Incremental import: **10** new messages indexed; initial corpus 30 messages.

API calls recorded: **49**. Provider-reported tokens: **140292** (not a billing estimate).

| Case | Scenario | Correct chat | All evidence | Recall | Answer completeness |
|---|---|---|---|---|---|
| E01 | exact entity | yes | yes | 100% | not reviewed |
| E02 | neighbor evidence | yes | yes | 100% | not reviewed |
| E03 | cross-chat | yes | yes | 100% | not reviewed |
| E04 | negative constraint | yes | yes | 100% | not reviewed |
| E05 | incremental import | yes | yes | 100% | not reviewed |
| E06 | deduplication | yes | yes | 100% | not reviewed |
| E07 | food retrieval | yes | yes | 100% | not reviewed |
| E08 | cross-chat | yes | yes | 100% | not reviewed |
| E09 | updated fact | yes | yes | 100% | not reviewed |
| E10 | correction | yes | yes | 100% | not reviewed |
| E11 | responsibility | yes | yes | 100% | not reviewed |
| E12 | actionable reply | yes | yes | 100% | not reviewed |
| E13 | answered conversation | yes | yes | 100% | not reviewed |
| E14 | unknown value | yes | yes | 100% | not reviewed |
| E15 | multi-turn reference | yes | yes | 100% | not reviewed |
| E16 | no evidence | n/a | n/a | n/a | not reviewed |

## Interpretation

These are retrieval measurements, not LLM answer-quality scores. Offline mode makes no model requests. Live answers and API usage are recorded when enabled; semantic citation support and completeness require a separate review. The table does not claim independent human review.

## Failed Retrieval Cases


## Limits

- Small authored synthetic dataset; not representative of private history.
- Top-k counts retrieved items: a semantic chunk may contain several messages, unlike a local message hit. Compare with a fixed token budget before claiming a quality gain.
- Conversation hit is lenient; all-evidence hit requires every annotated message in top-k.
- Citation range checks do not establish semantic support. Human review is required.
- Multi-turn history is passed to generation, but current retrieval sees the latest question only.
