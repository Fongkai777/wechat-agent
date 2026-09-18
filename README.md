# WeChat Agent

[English](README.md) | [中文说明](README.zh-CN.md)

Turn your own macOS WeChat chat history into a searchable personal knowledge
base. Browse conversations, ask questions across chats, and schedule read-only
tasks that surface relevant information with links back to the evidence.

The application runs locally. **Local storage does not mean offline AI:** cloud
features send relevant messages, embedding inputs, or voice audio to the
configured provider and may incur API charges.

## Features

| Tab | Purpose |
| --- | --- |
| Chat Content | Browse private/group chats, preview supported media, and sync new messages. |
| Chat Q&A | Ask multi-turn questions with retrieved evidence; save, rename, and delete conversations. |
| Task Assistant | Set a task, execution interval, and lookback window; inspect results, citations, and history. |
| RAG Configuration | Prepare and incrementally update indexes, schedule updates, and inspect retrieval diagnostics. |
| Model Configuration | Configure separate Q&A, transcription, embedding, and reranking profiles. |

Chat browsing uses cursor pagination: 100 messages per page and a bounded display
window of about 1,000 messages. Older messages remain accessible. The sidebar is
resizable; long previews are truncated visually, not in storage.

## Quick Start

Requires Python 3.9+, your own macOS WeChat 4.x databases, and matching database
keys. Key extraction depends on the WeChat/macOS version and permissions; this
is not a universal one-click backup decoder.

From the repository root, create the environment used by the startup scripts:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Place a database copy and your extracted `all_keys.json` in the project root.
Keep the sibling media directory if you need local media:

```text
db_storage/
  message/message_0.db
  contact/contact.db
  session/session.db
  ...
msg/                    # optional local media
all_keys.json           # secret; never commit
```

Inspect, decrypt, and start:

```bash
python -m wechat_agent inspect
python -m wechat_agent decrypt --keys all_keys.json
bash scripts/start_web.sh
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). The startup script defaults
to records since January 1, 2023. This filters visible/indexed data; it does not
delete or rewrite original WeChat databases. Decryption applies available
encrypted SQLite WAL data to the decrypted copies, including recent records not
yet checkpointed into the main database.

To use the live database directory instead of a static copy:

```bash
WECHAT_AGENT_DB_STORAGE="/path/to/db_storage" bash scripts/start_web.sh
```

Alternatively, save `{"db_storage": "/path/to/db_storage"}` in the ignored file
`web_cache/source.json`. Command-line arguments and the environment variable
take precedence. If macOS blocks the container directory, launch from Terminal
and grant that application Full Disk Access.

The service syncs on startup and checks for changes every 60 seconds. The sidebar
sync button checks immediately. Only changed databases are decrypted again.
A copied folder cannot receive new WeChat messages by itself. Sync does not
automatically call models or rebuild semantic indexes; configure RAG updates
separately. Automatic sync is deferred while relevant background work uses the
snapshots.

Restart the existing default-port service with:

```bash
bash scripts/restart_web.command
```

The restart script targets port 8787. Optional startup overrides are
`WECHAT_AGENT_HOST`, `WECHAT_AGENT_PORT`, and `WECHAT_AGENT_SINCE`. Keep the
default loopback host unless you have separately secured access.

## Two Retrieval Paths

**Chat Q&A** uses a custom Python/SQLite retrieval pipeline:

```text
Question + conversation history
  -> query planning / rewriting
  -> embedding retrieval + SQLite lexical retrieval + soft contact matches
  -> reciprocal rank fusion (RRF) + optional LLM reranking
  -> selected context -> answer with evidence
```

Contact matches are recall/ranking signals, not a hard route excluding other
chats. Reranking currently asks a chat-completion model to judge candidates; it
is not a dedicated OpenAI rerank endpoint. Diagnostics expose the plan, recall
paths, ranking, and selected context. The debug fragment count affects only that
debug request; Q&A has its own retrieval-count setting.

**Scheduled tasks** use a different path:

```text
Task + rolling time window
  -> Q&A model selects read-only tools
  -> search_messages / list_private_chats / read_chat
  -> synced decrypted snapshots + saved voice transcripts
  -> structured result -> citation validation -> saved execution history
```

Task tools currently use keyword search and direct conversation reads, not the
Q&A vector index or contact index. They return all matching records within the
configured window, without the former 30-result, 1,000-message scan, or
40-message context caps. This does not guarantee semantic completeness: chosen
keywords, local data availability, and provider context/output limits still
matter. Large results increase latency and cost; they are not silently trimmed
to make a model request fit.

## Tasks and Background Work

- Set an interval of every N hours/days and a lookback of the last N days/weeks/months. Supported lookbacks are 1-90 days, 1-12 weeks, or 1-3 calendar months.
- The first scheduled task runs after one interval. Manual execution uses the same rolling window; for an enabled task it resets the next run from the manual start time. A paused task can run manually without re-enabling its schedule.
- Task results use JSON Schema structured output. The Q&A model must support both tool calling and structured output. Replies are suggestions only; no WeChat messages are sent.
- Citations are registered across tool calls. Invalid references receive up to two correction attempts; unresolved references fail the run rather than being saved as a success.
- History includes evidence, tool steps, and reported token usage. A failed request without usage data is not treated as zero-cost. Task model reads have a 300-second timeout; timeouts do not automatically retry the request.
- Q&A, transcription, indexing, retrieval debugging, and tasks run on the server. Switching tabs, refreshing, or closing the browser does not cancel them. Stopping may need to wait for an in-flight API request to return.
- The service must remain running and the computer awake. A service restart interrupts unfinished work; missed task cycles are not replayed one by one.

## Index Preparation

Set the profiles and credentials in **Model Configuration**, then run
**Prepare Retrieval** in **RAG Configuration**:

```text
Voice transcription -> incremental full-text index -> incremental semantic index
```

Existing transcripts are skipped by default. New messages are added incrementally;
changed transcripts update affected text entries and semantic chunks rather than
forcing a complete rebuild. Account, date-range, or incompatible schema changes
can still require rebuilding. The CLI `index` command below creates a chat
inventory, not the web application's full-text/semantic indexes.

Preparation can run every N hours/days. The next scheduled update is calculated
after the scheduled run finishes; manual preparation does not reset that schedule.
Status reflects the latest full preparation, manual or scheduled, so a newer
success replaces an older failure. A blocking stage failure stops preparation;
already saved transcripts and vectors are retained.

## Media Support and Limits

- Images/videos require matching local files. V2 encrypted images additionally need `image_aes_key` and, when applicable, `image_xor_key`, supplied through local `config.json` or `WECHAT_AGENT_IMAGE_AES_KEY` / `WECHAT_AGENT_IMAGE_XOR_KEY`.
- Voice transcription requires local audio and a SILK decoder. For cloud transcription, install `pilk` with `python -m pip install 'pilk>=0.2'` and configure the transcription model. Local Whisper is optional; `scripts/install_voice_deps.sh` installs both `pilk` and `openai-whisper`.
- Missing audio cannot be recovered from XML metadata alone. Missing or expired media references cannot be guaranteed to render, including media in forwarded conversations.
- Stickers prefer local files and can fall back to message-provided CDN previews, which may expire or reject access. Inline codes such as `[Sob]` and `[Whimper]` remain text.
- Link shares, forwarded records, calls, and supported system messages are parsed for display; compatibility depends on the format and WeChat version.

## CLI Export and Storage

```bash
python -m wechat_agent index
python -m wechat_agent export --chat 1234567890@chatroom --name "My Group"
```

Decrypted copies are stored in `decrypted/`; exports in `exports/`. Runtime
configuration, Q&A history, transcripts, task history (`goals.sqlite3`), and RAG
scheduling state (`rag_schedule.sqlite3`) are stored under `web_cache/`.

## Privacy and Safety

Use only data you are authorized to access. Database keys, decrypted data,
model credentials, transcripts, and media are sensitive. `.gitignore` excludes
standard local data paths, but review staged files before publishing.

Cloud Q&A, tasks, embedding, reranking, and transcription send relevant data to
configured providers. CDN media previews also make external requests. Review
provider retention policies and obtain any necessary consent before using others'
messages. This project is not affiliated with or endorsed by Tencent/WeChat.

## Key Extraction and Credits

See [docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md) for helpers and troubleshooting.
A copied `db_storage` directory does not contain a ready-to-use plaintext
database key. Extraction may require sensitive OS permissions or signing
changes; understand their impact before changing them.

The project integrates local application code and open-source references; it
does not claim the underlying WeChat reverse engineering was built from scratch:

| Reference | Role in this project |
| --- | --- |
| [Thearas/wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos) | Reference for macOS LLDB key extraction and database decryption. |
| [BIBOYANG425/wechat-chat-history-mac](https://github.com/BIBOYANG425/wechat-chat-history-mac) | Reference for compatibility, database structure, and multi-shard export. |
| [raclen/wechat-suite](https://github.com/raclen/wechat-suite) | Source of the C scanner built by `scripts/key_scan_macos.sh`; reference decryption/export tooling. |

The repository also includes a Python LLDB scanner compatible with the
`wechat-claudecode-mcp` workflow. Reference repositories under `work/vendor/`
are local-only and not included in Git. Consult each upstream license before
redistributing its code.

Application code lives in `wechat_agent/` (decryption/parsing, web service,
retrieval, and task execution) and `web/` (HTML/CSS/JavaScript interface).
