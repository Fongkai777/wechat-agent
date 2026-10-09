# Portable Task Code Skills

Schema v3 replaces fixed task-operation lists for newly generated Skills. Existing
v1/v2 Skills and tool-calling tasks still work until explicitly replaced.

## Lifecycle

1. Enter a task, interval and lookback window.
2. The configured task model receives the task and public platform contract, not
   private chats, database paths or API keys. It generates complete source files.
3. The platform checks the package, runs its tests, then runs empty and nonempty
   synthetic integrations with simulated model responses. Generation may make two
   repair requests. Failed drafts are not automatically published.
4. Review/edit the files, test and explicitly save. A passing validation receipt
   binds the exact package and task text. Editing either invalidates that receipt.
5. Scheduled/manual runs reuse the saved package and current task time window.
   Business rules live in the source package, not platform task-category branches.

The file picker shows `SKILL.md`, `manifest.json`, `workflow.py`, `test_skill.py`,
`PLATFORM_API.md` and any additional generated modules. Export/import uses one JSON
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

The result is structured JSON: summary, items with source references, and an optional
notice. Citation identifiers must refer to messages actually obtained through the
data APIs. This checks source identity, not whether a model interpreted it correctly.

## Execution and Recovery

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
