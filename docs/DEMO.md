# A 75-Second End-to-End Demo

**Status:** synthetic import, incremental indexing, hybrid retrieval, real online
Q&A and a tool-calling task have been verified. Their screenshots and API records
are available. A continuous screen recording has not been produced or uploaded.
This is a recording plan, not a claim that a video exists.

## Verified Online Run

- [Current Q&A events and evidence](../eval/results/citation-regression/events.json):
  the real application's handler generated structured conclusions and source IDs;
  code rendered the private/group labels. No authored response was substituted.
- [30-day task result](../eval/results/task/run.json): scanned 40 messages, retrieved
  14 sources, consolidated two internship postings and preparation suggestions.
- [Original attribution failure](../eval/results/live/REVIEW.md): the earlier Q&A
  demo mislabeled private advice as a group source. Its output is retained.
  [Post-fix regression](../eval/results/citation-regression/REPORT.md) correctly
  cites the private chat, but does not establish general semantic correctness.

![Current Q&A with expanded private-chat evidence](images/qa-citations.png)
![Task result](images/task-live.png)

Reproduce online steps against only synthetic data (billable):

```bash
python scripts/run_demo_task.py --root .demo/showcase --mode qa --live-config .demo/models.json --output artifacts/demo-qa
python scripts/run_demo_task.py --root .demo/showcase --mode task --live-config .demo/models.json --output artifacts/demo-task
python -m wechat_agent.demo serve --root .demo/showcase
```

Tasks created by this helper remain paused; it runs once, not on a paid schedule.
For a citation-only regression take, use `--mode qa --root .demo/citation-check`
and `--output artifacts/citation-regression`. Results include `answer_data`, the
complete ordered source list and per-call usage. The checked-in screenshot was
taken after reloading the saved answer and expanding private-chat reference 7;
reference 22 also survived reload. No credentials are copied into the demo root.

## Setup

Follow the README sample commands. Use only `examples/chats.json`, never a real
account. For a fresh take use a new empty root, e.g. `--root .demo/take-2`, on
every demo command; do not erase private data or overwrite a non-demo directory.

Configure Q&A/task models in the demo's model page for online steps. This uses
separate `.demo/models.json` and requires authorized provider usage. Do not record
credentials. Keep the local service running.

## Recording Script

| Time | Show | Narration |
|---|---|---|
| 0–10 s | Import 30 messages; open chat browser | “Useful information is scattered between group chats and private conversations. This example uses fictional data only.” |
| 10–22 s | Build index, append 10 messages, update index | “New messages are indexed incrementally. The second run adds ten messages rather than reprocessing the archive.” |
| 22–38 s | Ask `新加坡有哪些搜索或 RAG 算法实习？列出要求、截止时间和申请方式。` | “I ask a question across conversations. The system retrieves evidence before generating an answer.” |
| 38–50 s | Expand sources, compare group posting and private advice | “The answer can be checked against the original message, sender and time.” |
| 50–65 s | Create `追踪新加坡的搜索与 RAG 实习，列出截止时间和准备建议`, interval 1 day/lookback 30 days; run once | “The same information need becomes a recurring task using read-only tools.” |
| 65–75 s | Show task report and evaluation report | “Suggestions stay linked to evidence. I keep evaluation cases, including retrieval failures.” |

Sample messages are dated September 10–22, 2026. For later recordings advance
fixture dates consistently or select an appropriate lookback window. Show real
completion states. Edit out long waits and label time cuts; do not substitute
authored text for model output.

## Sharing Checklist

- Record only the isolated demo window; hide OS notifications and personal tabs.
- Keep usernames, credentials and private sidebar content out of frame.
- Verify all cited evidence is fictional, frame by frame.
- Export H.264 MP4, 60–90 seconds. Upload to a user-approved Drive folder or
  YouTube account with intended visibility. Unlisted URLs remain shareable.
- After verifying playback, add the actual URL here, in both READMEs and to the
  resume project title. Do not add guessed or placeholder URLs.

Suggested title after upload:

`WeChat Agent | Evidence-backed conversational search and recurring task assistant | GitHub · Demo`
