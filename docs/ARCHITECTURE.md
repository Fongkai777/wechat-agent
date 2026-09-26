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

## Task Loop

The task model chooses `search_messages`, `list_private_chats` or `read_chat`.
`GoalChatTools` clamps calls to the task's time window and returns source metadata.
The agent validates citations before persisting a successful structured report.
This is a read-only tool loop, not desktop control or automatic reply sending.

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
