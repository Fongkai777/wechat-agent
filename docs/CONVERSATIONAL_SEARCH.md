# Conversational Search

The web Q&A path uses a bounded, single-agent LangGraph pipeline:

```text
User question + previous user turns + verified conversation state
  -> structured query plan
  -> validate identity and time constraints
  -> select a read-only query tool
  -> expand surrounding messages
  -> at most one supplementary search
  -> structured answer with server-owned citations
```

## Modules

- `qa_planning.py`: structured plan generation, user-turn provenance, contact alias
  resolution, ambiguity handling and relative-time normalization.
- `qa_tools.py`: indexed timeline queries, hybrid search, original-message
  hydration, surrounding context, SQL statistics and unreplied-chat candidates.
- `qa_agent.py`: orchestration, limits, progress and execution trace.
- `qa_answers.py`: answer schema and reference validation.
- `web.py`: HTTP/background-job adapter and existing retrieval providers.

The LangGraph 0.6 series retains compatibility with this project's Python 3.9
runtime. No LangSmith upload is enabled; graph execution explicitly disables
tracing even when the environment contains tracing settings.

## Constraints And Evidence

The model extracts a plan, not SQL. Names must be grounded in a user turn or the
server's previously validated state, then resolve to a unique contact ID. A place
or topic does not become a person filter simply because it appears in a nickname.
Ambiguous references produce a clarification rather than a global fallback.

Each request freezes the local clock with its UTC offset. Common Chinese relative
dates are calculated locally; a new `today` overrides prior dates. Less common
time expressions use model-proposed ISO dates tied to an exact user phrase and
validated for ordering. Complex temporal expressions remain an evaluation target.

Hybrid retrieval enforces verified person/time filters before candidate ranking.
Semantic chunks are hydrated into original message rows and filtered again: a
chunk containing several people or crossing midnight is not itself proof that
the requested person said all of its text. Surrounding messages keep their actual
sender. Historical background is separately labeled and cannot substitute for
an empty current window.

Statistics use SQL aggregation, not sampled retrieval results. Unreplied-chat
queries check the last sender in the requested window; this is only a candidate
signal, not proof that a reply is required. All results describe the local index,
not messages that have not yet been synchronized/indexed.

## State And Limits

Each completed stage atomically saves the query plan, tool trace, selected source
snapshots and verified conversation state in the existing local conversation
store. Errors and explicit cancellation preserve the latest trace. Browser
disconnects do not cancel backend jobs. A server restart still marks unfinished
runs interrupted; automatic paid replay/resume is deliberately not implemented.

There are at most five tool executions and one optional review decision. The
configured context limit is clamped to 10-200 message rows. Answer input is capped
at 60,000 message characters, with an individual-message cap of 4,000 characters.
Truncation and partial coverage are recorded, and the answer includes a limitation.
The 240-second budget is checked between stages; an in-flight network request has
its own timeout. Planning/review token counts are labeled separately and do not
claim to include answer, embedding or reranking costs.

## Current Boundary

This change replaces web Q&A orchestration, not the database ingestion or scheduled
task engine. Existing incremental message/vector indexes remain in SQLite; no
Qdrant migration, source-data deletion or new persistent service port is involved.
The task engine now uses a separate LangGraph in `goal_agent.py`, with local
SQLite checkpoints and explicit recovery (see `ARCHITECTURE.md`). Q&A uses the
same bounded SQLite checkpoint primitive, with its own planning/retrieval graph.
Interrupted answers expose **Continue answer**. Recovery reuses the original
question, history, people snapshot, request time and context limit. Completed
tool results and nested API responses (including answer repairs, query embeddings
and reranking) are cached locally before graph advancement. Unknown model requests
require explicit confirmation because remote execution may already have incurred
charges. Neither browser reconnection nor service startup submits a new request.

Q&A checkpoints live in private `web_cache/qa_checkpoints/` files. Only two graph
checkpoints are retained per turn; completed/stopped/deleted turns are cleaned up.
Resuming an older turn updates that assistant message in place, preserving later
messages and the conversation title. Model-profile or account/source changes block
recovery. Unfinished read-only tools can still read newly synced rows within the
original time range; this is not a historical snapshot of the entire database.
A vector-store migration is outside this implementation.

Tests use synthetic messages to cover follow-up pronouns, day boundaries, identity
switches, duplicate names, global topic searches, empty windows, chunk clipping,
surrounding speakers, statistics, pagination, process-level recovery, nested paid
call receipts, unknown-charge confirmation and preservation of later turns.
