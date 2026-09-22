# Publication and Privacy

## Scope

The current tree and local/remote-tracking branch history were scanned for provider-key formats,
private-key headers, local home paths, private WeChat IDs and data paths. The
current publication files contain no matches. This heuristic is not a guarantee
that arbitrary personal information can be detected.

- Private databases, keys and transcripts were not tracked in the audited repo.
  They were **not deleted locally**.
- User-specific path examples were generalized; a machine-specific launch-agent
  plist was removed from the publication tree.
- Regression-test nicknames and room identifiers copied from bug reports were
  replaced with explicit synthetic fixtures before publication.
- `.demo/`, credentials, databases, recordings, caches and generated artifacts
  are ignored. CI checks publication files.
- The sample has 40 wholly fictional messages. Screenshots use `demo_` identities,
  `example.com` URLs and empty model credentials.

## Historical Limitation

Older commits contain home-directory paths in prior README/extraction examples
and a launch-agent plist. The scan did **not** flag provider credentials or
database files in reachable history. This release changes the current tree,
not published Git history. Removing those paths requires a coordinated history
rewrite and force-push. Do not claim a fully scrubbed history from `.gitignore`.

Editor checkpoint refs are local recovery data, not release branches. The history
scan excludes them; publish only `main`, never a mirror push of all refs.

## Recheck

```bash
python scripts/privacy_check.py
python scripts/privacy_check.py --history
git diff --cached --stat
```

Manually review staged files and screenshots. The history check intentionally
exits nonzero while historical paths remain. If a credential is found, rotate
it first: deleting a commit is not sufficient remediation.

## Runtime

Only import authorized data. Cloud transcription sends audio; embedding sends
text chunks; reranking/Q&A/tasks send relevant evidence and prompts. Keep the
service on loopback. Never publish the private app, API or cache as a demo site.
