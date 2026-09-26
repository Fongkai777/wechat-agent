# Third-Party Notices

The root MIT license covers this project's original code and documentation.
It does not replace licenses or notices attached to third-party material.

- Local interface icons retain the Lucide ISC and Feather MIT notices in
  [web/icons/LICENSE.txt](web/icons/LICENSE.txt).
- PyCryptodome, Zstandard and optional voice dependencies retain their own
  licenses. They are installed separately, not bundled into this repository.
- Key extraction and database-format research references are documented in
  [docs/RESEARCH.md](docs/RESEARCH.md). Reference repositories downloaded under
  `work/vendor/` are excluded from the published tree. The optional C scanner
  wrapper downloads no source automatically; users obtain and build upstream
  code separately under its license.

In particular, the inspected `raclen/wechat-suite` reference carries an MIT
license, and `BIBOYANG425/wechat-chat-history-mac` carries Apache-2.0. Preserve
upstream license texts and attribution if redistributing their code. A link or
research citation is not permission to relicense another author's work.

WeChat and Tencent names identify interoperability targets, not endorsement.
Synthetic examples and demo recordings contain fictional conversations; imported
private conversations are not covered by the project's software license.
