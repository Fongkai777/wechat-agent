# WeChat Agent

[English](README.md) | [中文说明](README.zh-CN.md)

**Turn scattered conversations into searchable knowledge and recurring insights.**

WeChat Agent is a personal assistant for your own macOS WeChat history. Find
information across private and group chats, revisit conversations with context,
and set up recurring tasks to keep track of what matters.

## What You Can Do

### Search and Revisit Conversations

Browse chats in a familiar interface with contact names, message previews, and
supported media. Sync recent messages, explore older history, and turn local
voice messages into saved, searchable transcripts.

### Ask Questions Across Chats

Ask questions such as "What restaurants have friends recommended recently?" or
"What internship opportunities were shared in the groups?" Continue with
follow-up questions and inspect the retrieved messages behind an answer.
Conversations are saved for later review.

### Track Information With Recurring Tasks

Describe what you want checked, choose an execution interval and lookback window,
and review the results alongside each task. For example:

- Check for private conversations that may need a reply and suggest a draft.
- Track newly shared internship opportunities.
- Collect recent food and restaurant recommendations.

Tasks can run on a schedule or on demand. Results include source messages and
execution history. The assistant only reads chat data; it does not send replies.

### Manage Models and Retrieval

Configure Q&A, transcription, embedding, and reranking models in one place.
Prepare transcripts and indexes with one action, schedule incremental updates,
and inspect retrieval plans and evidence in the RAG configuration tab.

## Technical Overview

The application uses Python, SQLite, and an HTML/CSS/JavaScript frontend.
Two complementary paths support interactive questions and recurring tasks.

### RAG for Chat Q&A

```text
Question + conversation history
  -> query planning
  -> semantic + keyword retrieval + contact signals
  -> RRF fusion + optional LLM reranking
  -> contextual answer with message references
```

- **Query understanding:** Extract person, time, and topic signals to guide retrieval.
- **Soft routing:** Contact matches help retrieve and rank evidence without restricting the main search to those contacts.
- **Hybrid retrieval:** Combine embedding-based semantic matches with SQLite keyword matches, fuse results with reciprocal rank fusion (RRF), and optionally rerank with an LLM.
- **Visible evidence:** Inspect the retrieval plan, candidate results, and context used for the answer.

### Tool Calling for Tasks

```text
Task + time window -> model selects read-only tools
  -> search messages / inspect private chats / read context
  -> structured result + citation validation -> saved history
```

Tasks currently search synced chat snapshots and saved transcripts directly,
rather than using the Q&A vector index. The model chooses tools for each task
instead of following a separate fixed workflow for every use case.

### Incremental Data Preparation

Voice transcription, full-text indexing, and semantic indexing run in sequence.
Saved transcripts are reused; new messages and changed transcripts update the
affected index entries. Background execution continues across browser navigation
while the local service remains running.

## Getting Started

Requires Python 3.9+, your own macOS WeChat 4.x `db_storage/` directory, and a
matching `all_keys.json`. See the [key extraction guide](docs/KEY_EXTRACTION.md)
for prerequisites and version-dependent limitations.

From the repository root, with those local files in place:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m wechat_agent decrypt --keys all_keys.json
bash scripts/start_web.sh
```

Open [localhost:8787](http://127.0.0.1:8787), configure your models, then prepare
retrieval in the RAG tab. To sync new messages continuously, use an accessible
live database source rather than a static copy.

See the [usage guide](docs/USAGE.md) for source configuration, media support,
scheduling behavior, exports, and troubleshooting notes.

## Privacy and Limits

Use only data you are authorized to access. Storage is local, but cloud AI
features send relevant text or audio to your configured provider and may incur
charges. Never commit keys, decrypted databases, or private caches.

Available history and media depend on locally synced data and compatible keys.
Answers and task results may miss information; inspect the evidence before
acting. Scheduled work requires the local service to stay running and the
computer awake.

## Open-Source References

The project builds on existing WeChat extraction and parsing research:

- [wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos): macOS key extraction and decryption reference.
- [wechat-chat-history-mac](https://github.com/BIBOYANG425/wechat-chat-history-mac): compatibility, database structure, and multi-shard export reference.
- [wechat-suite](https://github.com/raclen/wechat-suite): C key scanner used by the extraction helper, plus decryption/export references.

See the [key extraction guide](docs/KEY_EXTRACTION.md) for helper details.
Consult upstream licenses before redistributing their code. This project is not
affiliated with or endorsed by Tencent/WeChat.
