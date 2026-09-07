# Existing Implementations

This project was scoped against three current public references.

## Thearas/wechat-db-decrypt-macos

Repository: <https://github.com/Thearas/wechat-db-decrypt-macos>

Best for:

- macOS arm64 WeChat 4.1 key extraction experiments.
- The original `lldb`-based `find_key.py` workflow used by the companion guide.

Notes:

- The public repository currently exposes very little code in the browsable tree.
- It is still the upstream named by the companion project for the hard key capture
  step.

## BIBOYANG425/wechat-chat-history-mac

Repository: <https://github.com/BIBOYANG425/wechat-chat-history-mac>

Best for:

- macOS Sequoia / WeChat 4.1+ workflow notes.
- A patch that attaches before WeChat opens databases at startup.
- Multi-shard export: one chat can appear in `message_0.db`,
  `message_1.db`, `message_2.db`, and later shards.
- Helper scripts for chat index and `@chatroom` lookup.

Implementation details used here:

- Table name convention: `Msg_` + `md5(username)`.
- `Name2Id` is per shard and maps `real_sender_id` to `user_name`.
- `session/session.db` can map known usernames to table hashes.
- Export must merge across all `message_*.db` shards chronologically.

## raclen/wechat-suite

Repository: <https://github.com/raclen/wechat-suite>

Best for:

- SQLCipher 4 decryption parameters and page-level implementation.
- A C key scanner that searches the WeChat process for key/salt patterns.
- Broader export tooling for messages, media, contacts, and summaries.

Implementation details used here:

- macOS WeChat 4.x uses SQLCipher 4 style pages:
  `page_size=4096`, `reserve=80`, `HMAC-SHA512`, `PBKDF2-HMAC-SHA512`.
- The first 16 bytes of each encrypted `.db` are the salt.
- The 32-byte key captured from memory is used directly as the page encryption
  key; the HMAC key is derived from `salt ^ 0x3a` with 2 PBKDF2 rounds.
- Page 1 is special: the first 16 bytes are salt, and decrypted output must
  restore the SQLite header.

## Current Decision

We implement our own small CLI instead of vendoring a whole repository:

- `inspect`: verify database inventory and salts.
- `decrypt`: consume `all_keys.json` and write decrypted SQLite files.
- `index`: build an all-chat index from decrypted shards.
- `export`: export one chat to JSON/TXT/CSV.

Key extraction remains a separate, explicit step because it requires attaching to
the running WeChat process and depends on macOS privacy/signing state.

