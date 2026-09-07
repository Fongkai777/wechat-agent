# WeChat Agent

[中文说明](README.zh-CN.md)

Local tools for inspecting, decrypting, and exporting your own macOS WeChat 4.x
chat databases.

This project expects a copied WeChat `db_storage` folder in the project root:

```text
db_storage/
  message/message_0.db
  contact/contact.db
  session/session.db
  ...
```

The copied databases are SQLCipher encrypted. You still need a key file extracted
from your own running WeChat process before messages can be exported.

## Quick Start

Inspect the copied databases:

```bash
python3 -m wechat_agent inspect
```

After you have an `all_keys.json` file, decrypt the databases:

```bash
python3 -m wechat_agent decrypt --keys all_keys.json
```

The decrypt step also patches encrypted SQLite `*.db-wal` files into the
decrypted copies, which is needed for recently downloaded voice/media rows that
WeChat has not checkpointed into the main `.db` file yet.

Start the local chat browser:

```bash
python3 -m wechat_agent.web
```

By default it reads:

```text
db_storage/
```

Pass `--db-storage` if you want to read the original WeChat container path
directly. Run it from Terminal.app if macOS Full Disk Access blocks that path.
Then open `http://127.0.0.1:8787`.

You can also keep the path out of shell history by setting an environment
variable in your local shell profile:

```bash
WECHAT_AGENT_DB_STORAGE="/path/to/db_storage" bash scripts/start_web.sh
```

To show only records from 2023 onward and use a managed port:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
bash scripts/start_web.sh
```

Then open `http://127.0.0.1:8787`.

Choose another port with:

```bash
WECHAT_AGENT_PORT=8899 bash scripts/start_web.sh
```

The web UI also accepts the same options directly:

```bash
.venv/bin/python -m wechat_agent.web --since 2023-01-01 --port 8899
```

The date filter only affects what the local browser displays. It does not delete
or rewrite the original WeChat databases.

### Media Notes

The chat browser reads media from the sibling `msg/` folder next to
`db_storage`.

- Videos are displayed when the matching local `.mp4` or thumbnail exists.
- Images are displayed when the `.dat` file already contains browser-readable
  JPEG/PNG/WebP data.
- Inline WeChat emoji codes such as `[Sob]` are kept as text for later analysis.
- Sticker messages (`type=47`) are parsed from their XML. The browser first
  tries local sticker cache files, then falls back to the CDN preview URL in the
  message XML when available.
- WeChat V2 encrypted image `.dat` files need an extra `image_aes_key` and
  optional `image_xor_key`. Pass them with `WECHAT_AGENT_IMAGE_AES_KEY` and
  `WECHAT_AGENT_IMAGE_XOR_KEY`, or put them in `config.json`.
- Voice messages are indexed from `message/media_*.db`. Playback and
  transcription require the local audio blob plus a SILK decoder and
  `openai-whisper`; run `bash scripts/install_voice_deps.sh` to install the
  optional voice dependencies. If a voice message exists only as XML metadata,
  the browser will show that the local audio data is missing.

Build an index of chats from decrypted databases:

```bash
python3 -m wechat_agent index
```

Export one chat by username or `@chatroom` id:

```bash
python3 -m wechat_agent export --chat 1234567890@chatroom --name "My Group"
```

Outputs are written under `decrypted/` and `exports/`.

## Dependencies

```bash
python3 -m pip install -r requirements.txt
```

`pycryptodome` is required for decryption. `zstandard` is optional but useful for
decoding compressed message bodies.

## Key Extraction

See [docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md). The short version: macOS
WeChat 4.x stores the database key in the running client process, not in
`db_storage`. Existing projects extract per-database keys from your own logged-in
WeChat process and write `all_keys.json`, then this project can decrypt and
export.

## References

- <https://github.com/Thearas/wechat-db-decrypt-macos>
- <https://github.com/BIBOYANG425/wechat-chat-history-mac>
- <https://github.com/raclen/wechat-suite>
