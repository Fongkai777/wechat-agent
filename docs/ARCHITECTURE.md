# Architecture and Code Map

This is the existing application, not a parallel demo implementation. The public
fixture imports into the same SQLite table format and uses the same message
browser, indexer, query planner, background jobs and model configuration.

| Component | Entry point | Responsibility |
|---|---|---|
| Private import | `wechat_agent/cli.py` | Database decryption, shard discovery and exports |
| Synthetic import | `wechat_agent/demo.py` | Isolated, idempotent import of authored JSON fixtures |
| Chat browser | `wechat_agent/message_pages.py`, `web/sidebar.js` | Cursor pagination, shard merge and sidebar layout |
| Web and retrieval | `wechat_agent/web.py` | HTTP routes, normalization, contact signals, FTS/LIKE, semantic chunks, RRF and answer generation |
| Q&A citation contract | `wechat_agent/qa_answers.py` | Strict answer schema, reference validation and plain-text compatibility |
| Background work | `wechat_agent/jobs.py`, `web/jobs.js` | Job state, progress, cancellation and browser reconnect |
| Recurring assistant | `wechat_agent/goals.py`, `wechat_agent/goal_tools.py` | Schedule, bounded read-only tools, structured output and source validation |
| Index scheduling | `wechat_agent/rag_schedule.py` | Recurring transcription → text → semantic preparation |
| Transcription | `wechat_agent/voice_transcribe.py`, functions in `web.py` | Local audio handling, cloud transcription and saved-text reuse |
| UI | `web/index.html`, `web/styles.css`, `web/app.js`, `web/goals.js` | Five workspaces; custom CSS, not a TDesign dependency |
| Evaluation | `scripts/evaluate_retrieval.py`, `eval/` | Actual retrieval on a labeled synthetic fixture |

## Retrieval Boundaries

`select_qa_context_with_diagnostics` builds a plan and calls `search_qa_search_db`.
Main retrieval remains global; contact-name matches add soft evidence rather
than a hard person filter. Semantic and keyword candidates are fused, locally
ranked, and optionally sent to a chat model for reranking. The answer model sees
selected messages and saved conversational history.

Query planning uses rules, token expansion, contact hints and time signals. It
is **not an LLM query planner**. History does not yet rewrite the retrieval query.

## Answer Provenance

The Q&A model returns paragraphs containing `kind`, `text` and `source_refs`.
Each answer paragraph must cite one or more IDs from this request's ordered
source list; missing-evidence paragraphs may omit references. The server checks
the shape and reference bounds, allows one format-repair attempt, and rejects
refused, truncated or repeatedly invalid responses instead of displaying them
as successful answers. The configured provider must support strict JSON Schema.

The UI resolves each ID against saved retrieval metadata. Chat type/name,
sender, time and original text are not model-generated labels. A contact-index
summary, when supplied, becomes a separate labeled source, not a chat message.
The complete source snapshot and structured answer survive reload and follow-up
turns. Older plain-text conversations remain readable without being rewritten.

Valid IDs do not establish that the cited text supports a claim. A model can
still select the wrong valid source or misinterpret it; semantic review remains
necessary. See the [evaluation guide](../eval/README.md) for reproducible checks.

## Incremental Work

`update_qa_search_db_incremental` compares fingerprints and watermarks, appends
messages, refreshes voice text and invalidates affected chunks.
`build_or_update_qa_semantic_index` embeds pending chunks. Incompatible source,
embedding-dimension or model changes can still require rebuilding.

### Index Storage

New indexes use an external-content FTS5 table backed by `messages.search_text`,
so full-text search does not store a second copy of that text. Updates remove
old tokens before changing the backing row; the same helpers also support older
self-contained FTS tables. Semantic embedding inputs are transient, while the
vector, source text, message mappings and model metadata remain stored. The
legacy `semantic_chunks.search_text` column stays empty for schema compatibility.

For an existing index, stop **all** services/processes using it, then run:

```sh
.venv/bin/python -m wechat_agent.search_storage web_cache/qa_messages.db --offline
```

This migrates and compacts a temporary copy, checks SQLite/FTS integrity and
hashes every retained value before atomically replacing the original. It needs
temporary disk space for the copy and SQLite's working files, does not call an
embedding API, and leaves the original untouched on validation failure. Keep
the server stopped until it finishes, then restart it with the usual settings.

## Task Loop

`goal_agent.py` runs a LangGraph state graph: model decision -> dispatch ->
one read-only tool at a time -> model decision, or reference validation -> finish.
Invalid reference IDs can return to the model for correction. This is an
execution protocol, not a preset business workflow or a top-k result filter.

`goal_checkpoints.py` uses the official SQLite checkpointer, synchronously saved
after each node. Each run has a private file under `web_cache/goal_checkpoints/`.
Only the latest two checkpoints are retained. Completed/cancelled runs, deleted
tasks and runs removed by the 20-run history policy have their checkpoints removed.
These files contain private evidence; they are ignored by Git and local tracing
is disabled. The saved graph contains no API credentials.

Interrupted/failed runs with checkpoints expose **Resume** in their card/history.
Recovery is explicit, never automatically launched at service startup. It reuses
the run ID, original task/window, evidence IDs and model profile; editing the task
does not change an old run. Model-profile changes require restoring the profile
or starting a new run. Read-only tools interrupted before a checkpoint may replay
against the currently synced data, within the original time window.

Before a paid model request, a local receipt is marked pending. A received reply
is saved before graph advancement, so it can be reused after a crash. A pending
receipt without a reply requires explicit duplicate-charge confirmation before
retry; remote exactly-once execution cannot be guaranteed. Unknown attempts are
marked separately from confirmed token totals. Results are saved idempotently;
if the graph finishes before history is saved, Resume can save that result without
another model request. Q&A uses a separate bounded graph with the same SQLite
checkpointer and explicit recovery. `qa_recovery.py` stores frozen inputs and nested
call receipts; `/api/qa/resume` shares the conversation lock with new questions.
It updates the original turn instead of replacing conversation history. See
`CONVERSATIONAL_SEARCH.md` for Q&A recovery and data freshness boundaries.

New tasks use schema-v3 Python code packages. `code_skill_packages.py` defines the
portable contract, `code_skill_runtime.py` runs the OS-isolated worker,
`code_skill_api.py` mediates generic capabilities, and `code_skill_service.py`
handles tests, publication checks and durable execution. `code_skill_web.py` binds
those capabilities to existing application services. No v3 task-category workflow
is selected by platform code. On-demand index APIs acquire the maintenance lock,
automatically prepare missing or stale text/vector indexes and report progress.
Preparation time is separate from the isolated program's execution budget; raw
message reads do not trigger indexing. See [Code Skills](CODE_SKILLS.md).

Earlier tasks used `task_skills.py` to generate a versioned declarative plan from the
task description alone. Generation runs as a background job and saves a draft;
it does not publish or execute the task. Users review/edit the JSON before saving.
`goals.py` stores confirmed versions with optimistic concurrency checks. Each run
freezes its original plan, task text and time window for history and recovery.

Version 1 Skills retain the read -> filter -> output graph. Version 2 is an editable
step list: read or semantic_search -> filter -> optional context -> deduplicate -> output.
Supported sources are private chat tails, private messages and all messages;
filters cover sender and literal keywords. `skill_retrieval.py` connects semantic
tasks to the existing Embedding profile and local SQLite vector index. Query
embeddings have durable receipts separate from answer requests. Retrieval hydrates
chunk matches into individual messages and rechecks both time boundaries. Literal
keyword recall is unioned with semantic hits, never used as an implicit AND filter.
There is no top-K cap; the explicit cosine threshold is editable and recall is not
claimed to be exhaustive. Stale or missing indexes fail before requesting embeddings.
Context is restricted to the same conversation, task window, configured message
counts and maximum time gap. Index-backed seeds use index neighbours; raw private
tails can read context without any index. Context sources are marked separately,
deduplicated by message identity and are not listed as independent matches. Model
calls receive numbered evidence; history records counts and embedding/answer usage.
Legacy Skill versions remain executable without silent migration.
Private chat tails are selected per database shard with a bounded last-row query
before sender filtering, rather than sending whole conversations to a model.
Local list output makes no model request and does not cap the number of matches.
Model output receives only matching candidates and must cite retrieved sources.
Plans cannot execute Python, shell commands or arbitrary SQL. Unsupported semantic
criteria require model output rather than hidden task-specific heuristics.

Existing tasks without a confirmed Skill retain the tool-calling graph:
the task model chooses `search_messages`, `list_private_chats` or `read_chat`.
`GoalChatTools` clamps calls to the task's time window and returns source metadata.
The agent validates citations before persisting a successful structured report.
This is a read-only tool loop, not desktop control or automatic reply sending.
All task cards use the same generic execution contract. The user's task text is
passed unchanged; there are no hidden task-category filters or preset criteria
for deciding which replies, opportunities or other findings matter. The shared
contract only governs read-only access, the configured time window, evidence,
uncertainty and the structured result format.

## Model Roles

Transcription, Q&A, tasks, embedding and reranking have separate configuration.
Legacy task settings start from QA settings and become independently saved.
Embedding/reranking can inherit QA credentials when their fields are left blank.

## Tradeoffs

- `web.py` still owns too much. Extracting retrieval and handlers is a planned
  refactor, not a claimed feature.
- SQLite keeps setup small, but vector scoring and task scans need profiling.
- There is no public-service authentication. Bind to loopback, not the internet.
- Browser navigation does not stop jobs, but process shutdown can. The local
  scheduler is not a durable distributed task queue.
