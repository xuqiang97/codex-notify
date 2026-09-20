# V1 validation record

Implementation validation performed on 2026-09-20. This records software checks;
it is not a claim that all V1 device acceptance criteria have been met.

## Executed locally

Environment: Apple Silicon (arm64), macOS 26.6.2.

| Check | Result |
| --- | --- |
| Python 3.10.19: `python3.10 -m unittest discover -v` | 47 tests passed |
| Python 3.13.7: `python3 -m unittest discover -v` | 47 tests passed |
| Both interpreters: `-m compileall -q notify.py providers tests scripts` | Passed |
| Python 3.10 AST syntax compatibility | Passed |
| README Windows/macOS TOML examples parsed with `tomllib` | Both valid |
| `.env`, `.env.local`, `.env.backup`, `.env~`, Python cache Git-ignore checks | Passed; `.env.example` remains eligible for tracking |
| `git diff --check` | Passed |

Tests cover event parsing/filtering, malformed/deep JSON, missing fields, config
precedence, literal dotenv parsing (BOM/CRLF/quotes), malformed config, hostname
fallback, POSIX/Windows/UNC paths, Unicode/whitespace/truncation, control-character
handling, recognizable sensitive content, omitted input/IDs/unknown fields,
UTF-8 ntfy JSON requests, optional bearer auth, response closure, HTTP/network
errors, redirect refusal, body size, total timeout and daemon process exit.

There is a mocked end-to-end event-to-HTTP test, plus real subprocess tests for
CLI behavior from another working directory and a script path with spaces and
Unicode. HTTP is mocked and in-process sockets/DNS are blocked. No automated test
publishes an ntfy notification; the manual smoke script is not part of discovery.

The Windows/macOS × Python 3.10/3.13 GitHub Actions matrix is provided. Its result
must be checked on GitHub after publishing; local macOS results do not imply a
Windows runner result.

## Security review scope

- No actual topic, token or local `.env` was created for this implementation.
- Configuration examples contain an explicit unusable placeholder; test data
  contains deliberately fake topic/token fixtures and never publishes them.
- Reviewed all 16 tracked/index files and scanned 24 history/index blobs for
  credential patterns, high-entropy strings and topic/token assignments. No real
  topic/token was found. The sole private-key-pattern hit was the literal test
  marker `-----BEGIN PRIVATE KEY-----`, without any key data. Assignment hits
  were safe templates, specification placeholders or deliberately fake fixtures.
  No private `.env` file is tracked. Repeat this review before future publication.
- HTTPS-only endpoints, TLS verification, disabled redirects, bounded requests,
  safe diagnostics and omission of raw events are covered in code/tests.
- Excerpt filtering is heuristic. Unknown credentials or confidential prose are
  not reliably identifiable. Set `CODEX_NOTIFY_SUMMARY_MAX=0` for sensitive work;
  non-sensitive device/project labels are still required. See README boundaries.

## Follow-up: named tasks and iPhone delivery (2026-09-20)

The user confirmed receiving both a manual notification and a notification after
the requested real Codex completion test on iPhone. This is user-reported device
receipt; exact latency/count, lock-screen timing and all network conditions have
not been independently recorded. Android and the two-Windows-machine checks
remain pending. The original 47-test Windows/macOS × Python 3.10/3.13 CI matrix
[passed](https://github.com/xuqiang97/codex-notify/actions/runs/35500220916).

Project/task title changes add 17 offline checks (64 total), including opt-in
validation, exact ID matching, latest rename, corrupt/missing/unreadable index,
bounded-tail reading, privacy before truncation, disabled lookup without disk
access, and summary-independent named notifications. Index tests use temporary
fake metadata; real local task titles and private topics are never test fixtures.

## Original real-environment acceptance checklist

No personal topic or phone subscription was available to the initial implementation
run, and that initial run did not modify user-level Codex config. Subsequent local
setup and user-reported iPhone receipt are recorded above. Execute the remaining
checks with private local
configuration; do not attach topics, tokens or confidential notification content
to the evidence.

| Environment | Required evidence | Status |
| --- | --- | --- |
| Windows machine A → ntfy → Xiaomi/Android | Manual smoke; real Codex completed turn; exactly one notification | Pending |
| Windows machine B → same personal topic | Distinct device alias; no source changes | Pending |
| macOS → ntfy → iPhone | Manual smoke; real Codex completed turn; exactly one notification | Pending |
| Two users with different topics | No cross-user receipt | Pending |
| Xiaomi/HyperOS | Foreground, background, locked immediately/after several minutes; Wi-Fi/cellular | Pending |
| iPhone | Foreground/background/lock-screen receipt; Unicode, Focus/permissions; Wi-Fi/cellular | Pending |
| Both desktops | GUI/terminal Codex launch, real Python paths and inherited env, real TLS/proxy/network outage behavior | Pending |
| Hosted ntfy | Real HTTPS acceptance and phone receipt; HTTP success alone is insufficient | Pending |

Record OS, Python, Codex and mobile app versions, test time, observed delivery
latency/count, and pass/fail for each row when tested. V1's complete end-to-end
acceptance remains pending until these checks are performed.
