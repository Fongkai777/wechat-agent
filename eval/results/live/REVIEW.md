# Online Answer Review

Reviewed against `results.json`, the retrieved items and `examples/chats.json`.
Reviewer: **Codex**. This is a qualitative assistant review, not an independent
human evaluation or a second blinded model judge. The `manual_review` fields in
the raw report remain null for an independent reviewer to fill.

## Per-Question Findings

"Supported" below includes textual attribution by sender/time/chat, not only
numeric citations. "Complete" refers to the question's annotated facts, not to
every possible fact in the entire archive.

| Case | Context attribution | Completeness | Observation |
|---|---|---|---|
| E01 | Supported | Complete | Singapore and all three skills match message 1. |
| E02 | Supported | Complete | Deadline and application URL match message 2. |
| E03 | Supported | Complete, extra scope | Includes failure case, demo and fusion advice; also includes general evaluation advice and advice about another role. A more focused answer would distinguish these. |
| E04 | Supported | Complete | Lists the two eligible roles; does not recommend product design or Beijing full-time work. |
| E05 | Supported | Complete | September 30 and at least four months; numeric references 1/2 contain message 32. |
| E06 | Supported | Partial wording | Correctly identifies one role, but "appeared twice" is ambiguous: there is an original posting, a deadline follow-up and one explicit repost. State "one initial post + one repost" rather than counting all mentions as two. |
| E07 | Supported | Complete | Dish, average price and location are present; distinguishes the newer lunch deal. |
| E08 | Supported | Complete | Combines no-egg vegan advice from private chat with Monday closure from the group. |
| E09 | Supported | Complete | 16 SGD and Tuesday-Friday match the new message. |
| E10 | Supported | Complete | Selects Saturday 10am and identifies the cancelled Friday plan. |
| E11 | Supported | Complete | Assigns import/evaluation/frontend to the correct people. |
| E12 | Supported | Complete | Identifies the pending review request; does not invent a report filename. |
| E13 | Supported | Complete | Cites the outgoing confirmation, not merely the other person's final message. |
| E14 | Supported | Complete | Does not invent salary; states it was not published. |
| E15 | Supported | Complete on this sample | Resolves the follow-up during generation. Retrieval still did not rewrite the query; this small-corpus pass does not validate general multi-turn retrieval. |
| E16 | Supported | Appropriate abstention | Says the requested restaurant/price was not found; does not substitute another restaurant as the answer. |

All four answers with numeric citations used in-range, relevant references in
this run. Twelve answers used textual provenance instead. Numeric range checks
alone therefore would not measure most of these answers' attribution quality.

## Additional End-to-End Demo: A Real Failure

The [separate UI Q&A run](../demo-qa/events.json) used the application's configured
context limit, yielding **39 items**, versus 8 in this benchmark. Its main role
facts are correct, but it attributes private-chat advice from September 14 and
September 22 to `AI 实习交流`, when the actual source is `林晓 · 示例`.

The raw evidence and screenshots are retained. This is **wrong source
attribution despite retrieving the right text**. Do not report all generated
answers as fully grounded or infer that more context always helps. One example
does not establish a causal link between context length and the mistake.

The [30-day task run](../task/run.json) used one search tool call, scanned 40
messages and returned 14 matching sources. It merged two roles and gave
preparation suggestions with valid source IDs. It did not verify external job
pages, send messages, or establish exhaustive semantic coverage.

## Follow-Up Experiments

1. Require structured evidence IDs in Q&A and render chat/time/sender directly
   from retrieved metadata, reducing free-form source-name generation.
2. Compare 8/16/32-item contexts under a fixed input-token budget; do not silently
   alter the production configuration for a benchmark.
3. Add independently authored questions and paraphrases, especially repost
   counting and ambiguous follow-ups. Keep this set as development data.
4. Measure first-response latency separately. This non-streamed run's total
   question latency was median **28.20s**, max **58.20s**, including retrieval,
   reranking and generation, not time to first token.
