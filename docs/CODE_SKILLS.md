# Portable Task Code Skills

Schema v3 replaces fixed task-operation lists for newly generated Skills. Existing
v1/v2 Skills and tool-calling tasks still work until explicitly replaced.

## Lifecycle

1. Enter a task, interval and lookback window.
2. The configured task model first assesses each requirement against the platform
   capability catalog. Partial/unsupported tasks show their gaps without replacing
   an existing draft or generating a reduced-scope task. For supported tasks, the
   model receives the public contract and generates complete source files, without
   private chats, database paths or API keys. The assessment is advisory, not a
   proof of feasibility, data completeness or answer correctness.
3. The platform checks the package, runs its tests, then runs empty and nonempty
   synthetic integrations with simulated model responses. Generation may make two
   repair requests. Failed drafts are not automatically published.
4. Review/edit the files, test and explicitly save. A passing validation receipt
   binds the exact package and task text. Editing either invalidates that receipt.
5. Scheduled/manual runs reuse the saved package and current task time window.
   Business rules live in the source package, not platform task-category branches.

The file picker shows `SKILL.md`, `manifest.json`, `workflow.py`, `test_skill.py`,
`PLATFORM_API.md`, `CAPABILITIES.json` (for newly generated packages) and any additional generated modules. Export/import uses one JSON
file containing every source file. An imported package is only a draft until tested
and saved. Never include credentials or real chats in exported packages.

## Platform Boundary

`run(api)` is the entry point. The generic APIs cover chats, raw messages, read-only
SQL over time-scoped index snapshots, semantic matches, neighbouring messages,
verified citations, model requests, task-local state and short progress logs.
Calls accept positional or keyword arguments. The complete contract is defined in
`wechat_agent/code_skill_packages.py` and included in generated packages.

The package owns SQL, filtering, fusion, prompts, batching, interpretation and
output assembly. The platform enforces the task window, declared capabilities,
source identity and explicit resource budgets. Index-backed operations automatically
prepare fresh indexes on first use: SQL updates the text index; semantic search also
updates the vector index using the configured Embedding service. Existing indexes
are reused, with incremental updates where possible and initial/replacement builds
when required by the existing index format. Progress and embedding usage are reported
in the task. Concurrent maintenance is awaited with cancellation support; preparation
has a separate 30-minute budget and does not consume the Skill's code execution time.
Update errors stop the task rather than silently searching stale data. Saved packages
do not need regeneration for this platform behavior. Raw message access does not
require an embedding index. Text indexes
can omit unrendered messages; raw message reads retain placeholders.

`api.messages_batch(chat_ids, latest=1)` batches raw reads for up to 100 conversations
per call, preserving the same physical ordering and ambiguity flags as `api.messages`.
It reduces sandbox RPC overhead without adding task-specific filtering or involving a
model/index. Each requested ID is returned, including empty conversations; reader failures
abort the call. Editable examples and synthetic regression tests live in `examples/skills/`.
These examples are not automatically applied to saved tasks.

The result is structured JSON: summary, items with source references, and an optional
notice. Citation identifiers must refer to messages actually obtained through the
data APIs. This checks source identity, not whether a model interpreted it correctly.

## Execution and Recovery

### Shared Tools and Multi-Turn Models

`platform_tools.py` owns the same function schemas, argument checks and execution
dispatch used by Q&A and Skills. `platform_api.py` and `platform_runtime.py` provide
the common data/model implementation. `platform_data_tools.py` exposes raw reads,
scoped SQL, threshold semantic search and configurable context as paged native tools;
the existing indexed tools still use `ConversationTools` underneath.
A Skill can inspect `api.tools()` and call `api.tool(name, arguments)`.
The task time window is fixed and background expansion is unavailable. Tool IDs,
sender IDs and evidence IDs must come from actual returned data. Tool pages expose
coverage/pagination rather than implying exhaustive retrieval.

No slot extraction or fixed intent router runs before a code Skill's tools.
Each call can supply independent nullable `filters` (chat type/title and message
timestamps); omitted filters in existing packages remain supported. `find_people`
discovers identity candidates when needed, without selecting or persisting a person
scope. The platform always enforces the task window, including context expansion.
Generated packages receive this contract. Saved user code is not silently rewritten.

`api.llm(messages, tools=['list_chats', 'timeline', 'finish'])` enables native
Tool Calling and returns `{message, finish_reason}`. The package appends the
assistant message, executes the requested tool, appends a matching `tool_call_id`
result and submits the next model request. It owns the loop and stopping policy;
the platform enforces permissions, message pairing and budgets. `api.llm` without
`tools` keeps its existing text/JSON return contract. Raw reads and custom SQL are
also available to Q&A through the same platform API. Task-private state remains
owned by its execution context, not a global mutable store exposed to the model.
`api.query(..., since=None, until=None)` can narrow the snapshot before loading it.
Native SQL results preserve actual rows as messages; aggregates/expressions are
explicitly labeled computation summaries, never passed off as original messages.

Shared `search` uses the configured hybrid recall/reranking pipeline and reserves
one embedding and the first rerank call from the package budgets; each additional
rerank batch or response-repair call consumes another model call. It requires
`index.query`, `embedding`, and `llm`. Both query-embedding and rerank token usage
are recorded. Provider failures without usage remain unknown billing amounts.
The lower-level `api.semantic_search` remains available for custom threshold-based
recall. Existing packages are not rewritten or automatically switched to tool loops.

### Checkpoints

Each run has a frozen package, task window and model-profile signature. LangGraph
stores the run checkpoint; a local receipt journal records API observations and
model responses. Recovery replays deterministic code using those receipts. A changed
call sequence fails instead of silently issuing different requests. Requests with
unknown outcomes require confirmation before retrying potentially billable calls.

Task state changes are staged and committed with a completed result only. Failed,
cancelled and validation runs do not change task state. Browser navigation does not
own the execution process; generation and validation also run as background jobs.

## Security and Limits

The first code-package runtime supports **macOS only** and uses `sandbox-exec`
with deny-by-default rules. If unavailable, execution fails closed. Generated code
cannot read arbitrary host files, write files, open network connections or fork.
It can read its package and the installed Python standard library. File metadata
access is permitted for Python startup. No credentials are present in its environment.
The trusted parent mediates data access and sends model requests using configured
credentials. Declaring `llm` permits submitting retrieved data to that provider.

The runtime restricts CPU, wall time, file descriptors, output size, call counts and
checks resident memory periodically. Memory monitoring is not a hard kernel memory
quota. SQL runs against an in-memory task-window snapshot with an authorizer and
query timeout; it cannot access the original database connection or attach files.
Large results fail explicitly rather than silently truncating. This is a local
single-user boundary, not a multi-tenant hostile-code hosting service. `sandbox-exec`
is a platform-specific dependency; Linux/Windows execution is not yet implemented.

Generated tests and simulated responses validate mechanics, not task quality.
Review prompts, recall thresholds, model budgets and coverage notices before
enabling a task. Exported `PLATFORM_API.md` is documentation; editing it does not
change platform permissions. The platform does not execute OS commands or send
WeChat replies on behalf of a Skill.

Capability checks run before generation, not before every scheduled execution.
Missing APIs, permissions, invalid arguments and execution budgets still fail
closed at runtime. A capability check does not add new connectors or automatically
grant a package permissions, and editing `CAPABILITIES.json` does not add abilities.
