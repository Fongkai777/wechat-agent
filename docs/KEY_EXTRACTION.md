# Getting `all_keys.json`

The copied `db_storage` folder does not contain plaintext SQLCipher keys. For
macOS WeChat 4.x, existing public implementations extract per-database keys from
the running WeChat process and match them to each database by the first 16 bytes
of the encrypted `.db` file, which SQLCipher uses as the database salt.

## Existing References

- `Thearas/wechat-db-decrypt-macos`: key extraction via `lldb`, focused on macOS
  arm64 WeChat 4.1.
- `BIBOYANG425/wechat-chat-history-mac`: adds a Sequoia/WeChat 4.1 launch-time
  patch and multi-shard export helpers.
- `raclen/wechat-suite/wechat-decrypt`: includes a C key scanner and SQLCipher 4
  decrypt/export tooling.

## Practical Flow

1. Confirm WeChat is your own logged-in account.
2. Extract keys with one of the reference tools. On newer macOS versions this
   should be run from a local Terminal with administrator permission. Sandboxed
   tools often cannot attach to the WeChat process.
3. Save the result as `all_keys.json` in this project root.
4. Run:

```bash
python3 -m wechat_agent decrypt --keys all_keys.json
```

## Project Helper

This project includes a helper wrapper around the C scanner from
`raclen/wechat-suite`, which has already been cloned under `work/vendor/` in this
workspace:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
bash scripts/key_scan_macos.sh
```

Then follow the printed command:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
sudo tools/find_all_keys_macos
```

Run it in the regular macOS Terminal app while WeChat is open and logged in. If
it captures only a few keys, click around in WeChat to trigger lazy database
opens, then run it again:

- open several chats, especially older ones;
- open Contacts and a few contact profiles;
- use the search box to trigger `message_fts.db`;
- open Favorites / Moments if you need those databases.

The goal is for `all_keys.json` to contain at least:

- `message/message_0.db`
- `message/message_1.db`
- `message/message_2.db`
- `contact/contact.db`
- `session/session.db`

For plain chat text export, those five are the most important.

## If `task_for_pid` Fails

The scanner needs permission to read the WeChat process memory. Common fixes:

- Run from the local Terminal app, not from Codex, SSH, cron, or a background
  job.
- When macOS prompts for Developer Tools / debugging permission, approve it.
- Grant Terminal full disk access if file access is blocked.
- Check WeChat signing:

```bash
codesign -dv /Applications/WeChat.app 2>&1 | grep -E "Signature|flags"
```

Some setups work only when WeChat is ad-hoc signed or when SIP/signing state has
been adjusted. Prefer the least invasive path first: local Terminal + sudo +
Developer Tools prompt.

## If The C Scanner Finds 0 Keys

This output means the permission barrier has been crossed, but the pattern-based
scanner did not recover usable keys:

```text
Got task port: ...
Found 0 encrypted DBs
Scan complete: ... 0 unique keys
```

Interpret it in two parts:

- `Got task port` is good. WeChat signing/debug permission is no longer the
  blocker.
- `Found 0 encrypted DBs` means the scanner did not see the live WeChat
  `db_storage` directory it expects under
  `~/Library/Containers/com.tencent.xinWeChat/.../xwechat_files/*/db_storage`.
  Grant Terminal Full Disk Access or use the copied project `db_storage` for
  salt inventory.
- `0 unique keys` means the scanner did not find the literal
  `x'<64hex_key><32hex_salt>'` string in memory. This is a known weak point of
  pattern scanning on newer WeChat 4.1 builds. The `wechat-chat-history-mac`
  troubleshooting notes report this exact failure mode on WeChat 4.1.7, while
  the breakpoint-based `find_key.py` path still works.

For WeChat 4.1.11, treat the C scanner as a quick first try. If it returns zero
keys, and you are on WeChat 4.1.7 with SIP disabled, try the Python LLDB memory
scanner compatible with `wechat-claudecode-mcp`:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
bash scripts/key_scan_python_lldb.sh
```

If that also returns zero keys, try the runtime-object LLDB workflow:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
bash scripts/key_extract_runtime.sh
```

That script attaches to the currently running WeChat process, reads the runtime
`DBEncryptInfo.m_dbEncryptKey` object when available, and writes only the DBs
whose page-1 HMAC verifies with that key.

If the runtime object path fails, switch to the LLDB breakpoint workflow:

```bash
cd "/Users/changfengkai/Desktop/WeChat Agent"
bash scripts/key_capture_lldb.sh
```

This project wrapper runs `scripts/lldb_find_wechat_keys.py`, which waits for
WeChat launch. Start it first, quit WeChat with Cmd+Q, relaunch WeChat, then
click chats/contacts/search so database-open events hit the breakpoint and keys
are written as they appear.

Expected key JSON shape:

```json
{
  "message/message_0.db": {
    "enc_key": "64_lowercase_hex_characters"
  },
  "contact/contact.db": {
    "enc_key": "64_lowercase_hex_characters"
  }
}
```

The tool also accepts a simpler form:

```json
{
  "message/message_0.db": "64_lowercase_hex_characters"
}
```

## Safety Notes

`all_keys.json` can decrypt your local WeChat data. Keep it out of git, cloud
sync, public issue trackers, and chat messages. Delete it after producing the
local export if you do not need to decrypt again.
