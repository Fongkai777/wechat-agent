# Structured Citation Regression

Date: 2026-09-22. Reviewed by Codex, not an independent human evaluator.
This is one targeted, real API regression on wholly fictional chats, not a
production accuracy claim or a new run of the 16-question benchmark.

## Failure and Change

The [earlier demo](../demo-qa/events.json) retrieved the correct evidence but
attributed private resume advice to a group chat. Its original output and
[review](../live/REVIEW.md) are retained.

The current handler asks for JSON paragraphs with conclusions and source IDs.
Code validates those IDs and renders source labels from the saved retrieval
metadata. It also removes the former 12-source persistence cap. Invalid output
gets at most one format-repair attempt; repeated failures, refusals and truncated
answers are not stored as successful answers. Existing plain-text history is
still readable.

## Observed Result

Question: `新加坡有哪些搜索或 RAG 算法实习？列出要求、截止时间和申请方式。`

The actual handler retrieved 39 items from 40 fictional messages in six chats.
The answer contains three paragraphs:

| Conclusion | Source IDs | Retrieved origin | Review |
|---|---|---|---|
| Starbridge search internship, requirements, September 25 deadline and application URL | 1, 2, 5 | Group: AI 实习交流 · 示例 | Matches the supplied posting |
| Farsail RAG internship, requirements, September 30 deadline and application URL | 3, 4, 6 | Group: AI 实习交流 · 示例 | Matches the supplied posting |
| Add reproducible Demo and evaluation links to the resume | 7, 22 | Private chat: 林晓 · 示例 | Correctly separated from group postings |

Both roles' minimum durations are also present. All source IDs are in range.
The response generated successfully on the first attempt; no repair call was
needed. The browser reloaded the persisted answer and expanded reference 7.
Reference 22 retained its private-chat metadata. Desktop and 390px-wide layouts
were checked. Original text remains expandable and all rendered text is escaped.

![Actual saved answer and expanded private source](../../../docs/images/qa-citations.png)

Raw [events and complete evidence](events.json) and [per-call usage](api_usage.json)
are included. Every retrieved chat ID was checked against the synthetic fixture.
No private messages or credentials are present in these artifacts.

## Tests and Usage

- 208 Python tests passed, including 14 new citation-contract and persistence tests.
- 113 frontend tests passed with `node --test tests/test_*.cjs`, including seven
  new citation-rendering tests. The earlier broader `tests/*.cjs` run also
  executed two existing preview-builder scripts (115 entries total).
- Cases cover invalid/out-of-range/bool references, missing citations, model-supplied
  metadata, refusal/truncation, bounded repair, network errors, reference 13,
  follow-up persistence, pre-saved question deduplication, legacy history and HTML escaping.
- Four API calls used 19,821 provider-reported tokens: index embeddings 4,748;
  query embeddings 114; reranking 8,630; answer generation 6,329.
- The handler reported 57.6 seconds for retrieval and answer generation, excluding
  initial index construction. This is total processing time, not first-token latency.

## Reproduce

Use a separate demo model configuration with authorized API credentials; the
following command makes billable calls using only the fictional fixture:

```bash
python scripts/run_demo_task.py --mode qa --root .demo/citation-check \
  --live-config .demo/models.json --output artifacts/citation-regression
python -m wechat_agent.demo serve --root .demo/citation-check
```

Open the saved conversation in the Q&A tab. If 8787 is occupied, stop the existing
server explicitly before starting the demo; no alternate port is opened.

## Limits

A valid source ID is not proof of semantic support. The model can still choose a
wrong but valid source, misread the text, or include mistaken attribution in its
conclusion. This change makes provenance explicit and verifiable; it does not
guarantee factual correctness. One passing run does not establish a failure rate.
