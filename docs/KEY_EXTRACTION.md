# Getting `all_keys.json`

Database keys decrypt your own local WeChat databases. They are not model API
keys, and copying `db_storage/` does not provide plaintext keys. The included
helpers attempt to read keys from your logged-in WeChat process and match them
against encrypted database files. Compatibility depends on the client and OS
version; extraction is not guaranteed.

Complete installation and data-copy instructions are in the
[English README](../README.md#installation-and-configuration) and
[中文 README](../README.zh-CN.md#部署与配置).

## Before You Start

- Use only your own account or data you are authorized to access.
- Install Apple's Command Line Tools with `xcode-select --install`.
- Quit WeChat before copying the whole account `db_storage/` directory into
  this repository, retaining any existing WAL/SHM files.
- Reopen WeChat and log in to that same account.
- Run the commands below from the repository root in your local macOS Terminal.

Keep original databases untouched. The included wrappers expect the copied
`db_storage/` in the project root and write `all_keys.json` there.

## Included LLDB Scanner

Run:

```bash
bash scripts/key_scan_python_lldb.sh
```

This wrapper requests administrator permission and runs the included
`scripts/wechat_mcp_keygen_compat.py` with the Command Line Tools Python and
LLDB bindings. It does not require installing a separate
`wechat-claudecode-mcp` package into Conda or the project virtual environment.

The scanner inventories the copied databases, searches the running process
for candidate keys, and validates them against database page data. If few keys
are found, open the relevant chats, Contacts, search or Favorites in WeChat,
then retry. This can load databases that were not previously open; it does not
mean every contact has a separate key.

A created JSON file is not enough: check the number of matched database keys.
The databases present vary by account. Prioritize all relevant
`message/message_*.db` shards, `contact/contact.db` and `session/session.db`,
rather than expecting a fixed number of shards.

## Validate and Decrypt

With the project's virtual environment active:

```bash
python -m wechat_agent doctor --keys all_keys.json
python -m wechat_agent decrypt --keys all_keys.json
```

Review skipped and failed databases. Decrypted copies are written to
`decrypted/`; do not replace WeChat's original files with these copies.
Keys must match the selected account and database files.

The accepted JSON shapes include:

```json
{
  "message/message_0.db": {
    "enc_key": "<64 hexadecimal characters>"
  }
}
```

or:

```json
{
  "message/message_0.db": "<64 hexadecimal characters>"
}
```

These are placeholders, not usable keys. Do not paste real keys into issues
or chat messages.

## Troubleshooting

### Permission to Attach Is Denied

`task_for_pid failed` or an LLDB attach failure means process access was
denied. Use your local Terminal and approve legitimate debugging permission
prompts if macOS presents them. Full Disk Access may be needed to read the
WeChat container, but file access does not itself grant process-debugging
permission.

Some client/OS combinations remain incompatible with attachment. Do not treat
disabling SIP or re-signing WeChat as a routine installation requirement.
Those changes affect system security or application integrity. If extraction
cannot work with your current protections, stop and assess a compatible
approach before changing them.

### LLDB Import Errors

Errors such as `No module named '_lldb'` can indicate mismatched Python and
LLDB bindings. Use the included wrapper instead of importing system LLDB from
Conda or an arbitrary Python version. The wrapper expects:

```text
/Library/Developer/CommandLineTools/usr/bin/python3
```

If that interpreter or its matching LLDB installation is missing, repair the
Command Line Tools installation. Installing the application package into
another interpreter does not fix a binary-binding mismatch.

### No Databases or Keys Found

- No encrypted database candidates: check that the copied `db_storage/`
  exists in the project root and contains encrypted `.db` files.
- Database candidates but no matching keys: confirm the same account is logged
  in, open relevant chats, and retry. The client's memory representation may
  not match the scanner's expected pattern.
- Some missing keys: the database may not be loaded, or the copied file may
  not match the current account/database generation.

Repeatedly rerunning a scanner cannot guarantee compatibility.

## Alternative Helpers

These are fallback implementations, not required installation steps. They
also depend on client internals and process-access permissions.

### Runtime Object

```bash
bash scripts/key_extract_runtime.sh
```

Attempts to read the runtime `DBEncryptInfo.m_dbEncryptKey` object when
available and validates recovered keys against copied databases.

### Launch-Time Breakpoints

```bash
bash scripts/key_capture_lldb.sh
```

Follow the script's launch prompt: quit WeChat with Cmd+Q, relaunch it, then
open relevant chats and pages. The included `lldb_find_wechat_keys.py` attempts
to capture database-open events. Press Ctrl+C when finished.

### Optional Third-Party C Scanner

The reference source is **not included in a fresh clone**. Review the
[upstream repository](https://github.com/raclen/wechat-suite) and its license
before downloading and running its code:

```bash
mkdir -p work/vendor
git clone --depth 1 https://github.com/raclen/wechat-suite.git work/vendor/wechat-suite
bash scripts/key_scan_macos.sh
sudo tools/find_all_keys_macos
```

This wrapper expects the upstream file
`wechat-decrypt/find_all_keys_macos.c`; upstream changes may require adaptation.
Use the validation and `python -m wechat_agent decrypt` commands above after
extraction. The C scanner's discovery logic may inspect the live account
directory rather than the project's copied directory.

## Protect Your Keys

Keep `all_keys.json`, decrypted databases and application caches out of Git,
cloud sharing and public bug reports. Ongoing synchronization needs the
matching keys, so retain them securely while using that feature. If you only
need a one-off export, remove unneeded sensitive copies after confirming the
export.

For API credentials used by voice transcription, Q&A and tasks, see the
README's model configuration section; those credentials are unrelated to
database extraction.
