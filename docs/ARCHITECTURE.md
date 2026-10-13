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
| Recurring assistant | `wechat_agent/goals.py`, `wechat_agent/code_skill_web.py` | Schedule, editable code Skills, shared tools and source validation |
| Shared platform | `wechat_agent/platform_api.py`, `wechat_agent/platform_runtime.py`, `wechat_agent/platform_data_tools.py` | Common raw reads, scoped SQL, retrieval, model service and provenance; Python/native adapters |
| Index scheduling | `wechat_agent/rag_schedule.py` | Recurring transcription → text → semantic preparation |
| Transcription | `wechat_agent/voice_transcribe.py`, functions in `web.py` | Local audio handling, cloud transcription and saved-text reuse |
| UI | `web/index.html`, `web/styles.css`, `web/app.js`, `web/goals.js` | Five workspaces; custom CSS, not a TDesign dependency |
| Evaluation | `scripts/evaluate_retrieval.py`, `eval/` | Actual retrieval on a labeled synthetic fixture |

## Retrieval Boundaries

New Q&A turns use `qa_agent.py`'s tool-first LangGraph loop. The model receives
dialogue history and a frozen clock, then chooses searches, identity discovery,
timeline/context reads or statistics without filling a global slot plan.
`platform_tools.py` shares this interface with code Skills. Optional per-call
person, conversation and date filters apply before recall and ranking; they do
not persist into the next query. Fixed task/data boundaries remain enforced.

`search_qa_search_db` fuses semantic and keyword candidates with equal-weight RRF
and reranks all recalled candidates in API batches, without business
heuristic scoring. Query reformulation belongs to the tool loop. Q&A and Skills
use the same platform runtime, including raw-message reads, custom read-only SQL,
threshold semantic search, index preparation and model transport. Older interrupted
Q&A protocols cannot resume; saved conversations remain readable. See
[Conversational Search](CONVERSATIONAL_SEARCH.md) for protocols and limits.

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

Tasks execute only schema-v3 editable Python packages. The platform no longer
contains a declarative Skill interpreter, preset task-category workflow or a
fallback task agent. Tasks without a valid code package require generation and
isolated testing; saved historical results remain readable.

`code_skill_packages.py` defines the portable contract;
`code_skill_runtime.py` runs the OS-isolated worker;
`platform_api.py` provides generic bounded capabilities for both orchestrators;
`code_skill_api.py` adapts package manifests to that common API;
`code_skill_service.py` validates, publishes and records durable execution;
`code_skill_web.py` binds task state to `platform_runtime.py`, which also serves Q&A.

A Skill owns its SQL, workflow, filtering and model prompts. It may use the shared
native tools or generic semantic/SQL/raw-message APIs. Index-backed operations
prepare stale indexes under the maintenance lock before querying. Raw-message
reads do not require an index. See [Code Skills](CODE_SKILLS.md).

The platform preserves the task time window, read-only sandbox, actual source IDs
and explicit resource limits. Every additional rerank batch/repair counts against
the Skill's model-call budget and records returned token usage. There is no hidden
business-topic scoring or date interpretation in the shared search provider.

Interrupted code execution uses local call receipts and explicit recovery.
Completed API responses can be reused; requests with unknown outcomes require
confirmation before retry because they may already have incurred charges.
Browser changes do not cancel backend execution. Q&A has its own SQLite graph
checkpoints and follows the same no-automatic-paid-replay rule.

## Retrieval Upgrade

Text index version 3 removes injected business keywords. Updating a version-2
index performs a transactional, one-way data migration while preserving message
IDs and source text. Affected semantic mappings are invalidated for re-embedding;
the original messages are not deleted. If an old compacted index no longer stores
embedding inputs, all its vector blocks must be regenerated because unchanged
inputs cannot be verified. The migration itself does not call an API.
No retired retrieval implementation is retained.

Vector batches are committed individually. Explicit `rate_limit_exceeded`
responses use bounded, cancellable backoff and respect `Retry-After`; quota
errors, timeouts and connection resets are not automatically replayed. A later
incremental run reuses committed batches rather than embedding them again.

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
