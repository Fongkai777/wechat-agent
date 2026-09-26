# Run with Fictional Data

[Project overview](../README.md) | [中文项目介绍](../README.zh-CN.md)

This optional path runs the same application with the 40 authored messages in
`examples/chats.json`. It needs neither a WeChat installation nor database
keys. It is useful for exploring the UI or recording a demonstration without
exposing private conversations. It does not test real-client key extraction.

## Prepare the Environment

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Import and Index

```bash
python -m wechat_agent.demo init --root .demo/showcase
python -m wechat_agent.demo index --root .demo/showcase
python -m wechat_agent.demo append --root .demo/showcase
python -m wechat_agent.demo index --root .demo/showcase
```

The first import contains 30 messages. The append adds 10, and the second index
update processes the additions. Repeating the append or index does not duplicate
messages. Each command must use the same root.

The demo refuses to overwrite a nonempty directory without its demo marker.
Use a new empty directory for a fresh run; never remove private data to reset it.

## Open the Application

```bash
python -m wechat_agent.demo serve --root .demo/showcase
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). If that port is occupied,
the command exits instead of taking over the running service or opening another
port. Stop it with Ctrl+C. Automatic syncing from real WeChat is disabled.

Browse the six conversations, inspect the index, and try retrieval debugging.
The local demo starts with embedding and reranking disabled. Chat Q&A and task
generation need a model service: configure their independent profiles in the
demo's **Model Settings** page. This can incur provider charges; only sample
messages should be used. Never record or publish the credential fields.

All demo settings and generated history stay beneath the selected root
(model settings here are `.demo/showcase/models.json`).
No private source directory or existing model configuration is used as fallback.

## Suggested Walkthrough

1. Import the sample and open a conversation.
2. Append the new messages and update the index.
3. Ask which search/RAG internships were shared in Singapore.
4. Expand sources to compare a group posting with private interview advice.
5. Create a task to collect internship deadlines and preparation suggestions.

The fixture dates are September 10-22, 2026. Task lookback windows use the
current time; a later run may correctly find no messages. For a recording,
adjust the fictional fixture dates consistently to the current demonstration
period. Do not substitute an authored answer for a model response.

Screenshots in the README use this fixture. No hosted video is currently
provided.

## Automated Installation Check

```bash
python scripts/smoke_test.py
```

This creates temporary demo data, verifies incremental indexing, starts a
short-lived loopback server on an OS-assigned port, checks the page, assets and
workspace APIs, then closes the server and removes its data. It does not call
a model or interfere with an existing service on 8787.
