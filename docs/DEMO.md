# Watch or Run the Demo

[Project overview](../README.md) | [中文项目介绍](../README.zh-CN.md)

## Watch the 77-Second Walkthrough

[![Watch the real-application recording](images/demo-poster.jpg)](demo/wechat-agent-demo.mp4)

[Play or download the MP4](demo/wechat-agent-demo.mp4).
English explanation captions, Chinese interface, no audio; 1440 × 976, H.264.

| Time | Scene |
|---|---|
| 00:00–00:10 | Browse group and private chats |
| 00:10–00:20 | Ask one question across conversations |
| 00:20–00:34 | Expand original group/private evidence |
| 00:34–00:47 | Create a daily task with a 30-day lookback and run it |
| 00:47–00:58 | Inspect the real task result and follow-up suggestions |
| 00:58–01:07 | Import ten new fictional messages, taking the corpus from 30 to 40 |
| 01:07–01:17 | Click incremental update and inspect the ten inserted messages |

Recorded on September 26, 2026 using real UI interactions and live
`gpt-5-mini` Q&A/task calls. No model answer was authored or substituted. Waiting
time between submission and model completion is omitted and labeled. The task
is scheduled daily and triggered manually for this take; the video does not
pretend that a day elapses during recording.

This is a historical recording, not a recording of the latest Skill editor.
Current tasks use editable Python Skill packages that must pass isolated tests
before execution. Follow the current walkthrough below for that workflow.

This take uses local lexical retrieval and a full-text index, with embedding
and reranking disabled. The sample append is performed by the recording
helper, not a claimed production import button. The helper's append endpoint
exists only in its temporary demo server, not the normal application.

## Try It Yourself

This optional path runs the same application with 343 authored messages across
12 conversations in `examples/showcase.json`. It needs neither a WeChat installation nor database
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
python -m wechat_agent.demo init --root .demo/story
python -m wechat_agent.demo index --root .demo/story
```

The initial import contains 314 messages over approximately two weeks: six
group conversations and six private conversations. Two later phases add 21 and
8 messages. Each command must use the same root. The authored scenes are not
derived from private messages, and gold answers are never imported as chat text.

The demo refuses to overwrite a nonempty directory without its demo marker.
Use a new empty directory for a fresh run; never remove private data to reset it.

## Open the Application

```bash
python -m wechat_agent.demo serve --root .demo/story
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). If that port is occupied,
the command exits instead of taking over the running service or opening another
port. Stop it with Ctrl+C. The demo checks its own sample database every two
seconds; both automatic and manual sync only refresh this synthetic workspace,
never the real WeChat directory.

Browse the twelve conversations, inspect the index, and try retrieval debugging.
The demo includes no credentials. Browsing and text indexing work offline;
hybrid search requires Embedding and Rerank configuration. Chat Q&A and task
generation also need model services: configure their independent profiles in the
demo's **Model Settings** page. This can incur provider charges; only sample
messages should be used. Never record or publish the credential fields.

All demo settings and generated history stay beneath the selected root
(model settings here are `.demo/story/models.json`).
No private source directory or existing model configuration is used as fallback.

## Suggested Walkthrough

1. Import and index the initial sample, then browse a private and a group conversation.
2. Ask which search/RAG internships were shared in Singapore; follow up about
   application requirements in the same Q&A conversation.
3. Expand the original sources to distinguish a group posting from private
   interview advice. Inspect the tool trace to see how the model retrieved evidence.
4. Create a task to collect internship deadlines and preparation suggestions,
   with a daily schedule and a lookback covering the fictional messages.
5. Generate its Skill, inspect the saved workflow/code and permissions, run the
   isolated tests, and save the passing package. On supported macOS, manually
   execute it and inspect the result and cited messages. Do not bypass validation.
6. Append `updates` and incrementally update the index. Ask about the corrected
   deadline and the newly shared opportunity; inspect both private and group sources.
7. Run a task checking unanswered private messages. Append `replies`, update the
   index and rerun it: the last message in the conversation with 余舟 is now yours.
   The raw last-sender rule should remove that candidate; whether other messages
   actually need a reply still depends on task code and context.

For this sequence, append messages **after** the first Q&A/task demonstration,
not during initial setup. In another terminal while the demo server is running:

```bash
python -m wechat_agent.demo append --root .demo/story --phase updates
# Inspect the new messages, then use the RAG tab to update indexes.
python -m wechat_agent.demo append --root .demo/story --phase replies
```

Repeating a named phase is idempotent; `replies` cannot be imported before
`updates`. Without `--phase`, append imports the next unfinished phase. After
both phases it is a no-op. Import and phase completion are one transaction.
The Q&A model chooses tools over multiple turns; a
task's saved program chooses its steps. Both use the same platform data and
model APIs. See [execution flows (Chinese)](EXECUTION_FLOWS.zh-CN.md) and
[editable Skill packages](CODE_SKILLS.md). Configure the required retrieval
services and prepare the semantic index before demonstrating hybrid search.

## Clock and Reproducibility

On first initialization, the demo anchors all message timestamps to the current
time. Date references in message text (application deadlines, meetings and shop
closure dates) shift with the same anchor. New phases are also before this
anchor, so today's messages do not accidentally fall in the future.

For a reproducible recording, choose an explicit timezone-aware clock:

```bash
python -m wechat_agent.demo init --root .demo/fixed-story --anchor 2026-10-13T18:00:00+08:00
```

The rendered corpus and clock are saved in the demo directory. Reinitializing,
appending or restarting never shifts existing messages. For another date, use
a new directory. An older demo can naturally age out of a one-day task window;
create a fresh workspace for a later presentation, rather than rewriting history.
Do not substitute an authored answer for a model response.

The old 30+10-message corpus remains frozen at `eval/corpus_v1.json`. Evaluation
scripts explicitly select it; `--dataset evaluation` reproduces it for browsing.
Existing old demo directories retain that dataset. README screenshots and the
September video use the old corpus and have not been rerecorded with this story.

## Record a New Take

The recording automation below and `scripts/run_demo_task.py --mode task`
target the older task flow used for the September video. They have not been
updated or revalidated for the current Skill generation/test/save workflow.
Use a manual screen recording of the current walkthrough above for a new take;
do not treat these historical helpers as a current end-to-end task test.

The optional recording helpers require Node.js with Playwright, Chrome/Chromium,
and FFmpeg with H.264 and drawtext support. They currently target macOS font and
Chrome paths; `CHROME_EXECUTABLE`, `FFMPEG` and `NODE_PATH` can override executable
and package locations. They are not needed to run the application.

```bash
python scripts/record_demo.py --root .demo/new-recording --live-config .demo/quick/models.json --output artifacts/demo-video
```

Use a fresh root. The explicitly selected model configuration must already have
Q&A and task credentials. This command makes paid calls using only fictional
data, keeps credentials in memory, records usage, and closes its temporary
server/browser at the end. It does not restart or use a private server on 8787.
Raw frames, responses and usage metadata remain in ignored artifact directories;
only the reviewed final video and poster belong in the public repository.

## Automated Installation Check

```bash
python scripts/smoke_test.py
```

This creates temporary demo data, verifies incremental indexing, starts a
short-lived loopback server on an OS-assigned port, checks the page, assets and
workspace APIs, then closes the server and removes its data. It does not call
a model or interfere with an existing service on 8787.

## Test the New Conversations

`eval/showcase_cases.json` contains seven diagnostic questions against a fixed
clock: cross-chat interview preparation, a rescheduled meeting, vegetarian lunch,
unknown salary, a follow-up about a corrected deadline, duplicate job forwards,
and an attendance requirement added in the last phase. Gold facts are kept outside
the conversations and are never passed to the answering model. The follow-up
uses the preceding answer actually generated in the same test run.

```bash
python -m unittest tests.test_showcase
python scripts/evaluate_showcase.py --live-config .demo/story/models.json --judge
```

The first command is offline. The second explicitly makes paid API calls using
the selected model configuration and the shared production Q&A runtime. It builds
an isolated temporary workspace, applies each update incrementally, and saves
answers, sources, traces, latency and token usage in `artifacts/showcase/`.
`--judge` adds a model review; these grades are not independent human validation.
No original chat histories, running services or task configurations are modified.
Offline tests also verify that an unnamed deadline correction expands to the
correct private-chat context and that a replied conversation leaves the pending
candidate list through local SQL without a model call. The pending-candidate
rule does not determine whether a human socially expects a response.

To isolate context presentation from retrieval variability, replay a saved
case with its originally selected anchors and actual conversation history:

```bash
python scripts/replay_showcase_context.py --live-config .demo/story/models.json \
  --input artifacts/showcase/results.json --output artifacts/context-replay --case S05
```

This regenerates only the fictional corpus, expands local context, and makes
paid answer/review requests. It does not rerun embedding, ranking or the Agent's
tool decisions and must not be reported as a full end-to-end evaluation.
