# V1 Implementation and Maintenance

This document describes the implemented sender and the boundaries to preserve
during maintenance. Read [AGENTS.md](../AGENTS.md) first; it is the implementation
contract. [DECISIONS.md](DECISIONS.md) records accepted design choices and their
history. [README](../README.md) owns setup instructions and configuration examples;
[VALIDATION.md](VALIDATION.md) owns dated test results and device acceptance.

The current Windows-to-Xiaomi installation is accepted and in maintenance.
Other devices and untested conditions retain their separate acceptance checks.
No new provider, service, workflow engine or preview feature is needed to use
the accepted installation.

## 1. Files and responsibilities

```text
codex-notify/
├── notify.py                 # CLI, config, scope, metadata and dispatch
├── task_metadata.py          # Optional bounded task-title lookup
├── providers/
│   ├── __init__.py           # Notification and error types
│   └── ntfy.py               # Provider config, HTTPS request and deadline
├── scripts/smoke_test.py     # Explicit manual publish, never an offline test
├── tests/                   # Offline unittest suite
└── .env.example             # Safe template; actual .env is private
```

The code uses Python 3.10+ and the standard library. The shared Windows/macOS
implementation does not require an OS shell, GUI API or third-party package.
Keep event/content logic separate from provider transport; optional title lookup
does not introduce a database or a task-history service.

## 2. Invocation and event flow

Codex invokes the user-level `notify` command with exactly one event JSON argument.
`main()` parses a JSON object, then checks `type == "agent-turn-complete"` before
loading configuration. Unsupported events return cleanly without publishing.
Unknown fields are tolerated; missing optional metadata uses safe fallbacks.

For a supported event, the sender:

1. Loads local configuration.
2. Applies optional project scope using the event's `cwd`.
3. Builds device/project/optional task metadata with privacy checks.
4. Dispatches the `Notification` through `send_notification(config, notification)`.
5. Returns the defined exit code, with sanitized diagnostics for expected errors.

`last-assistant-message` and `input-messages` are never used to build, name or
classify notifications. No reply preview, summary builder, generic reply fallback
or re-enable setting remains (Decision 017). Legacy `CODEX_NOTIFY_SUMMARY_MAX`
values are ignored; the surrounding dotenv syntax must still be valid.

## 3. Configuration and project scope

Precedence is **sender process environment > `.env` beside `notify.py` > defaults**.
An explicitly empty environment value overrides the file. Missing `.env` permits
environment-only configuration; malformed syntax remains a local error. Never
read an unrelated working project's `.env` or expose topic/token values in errors.

The parser reads literal `KEY=value` lines, matching quotes, blank lines and
full-line comments, with UTF-8 BOM/CRLF support. It does not execute, interpolate
or unescape values. The last duplicate key wins. README and `.env.example` define
the supported sender settings; do not overwrite existing private configuration
with the template during an update.

`CODEX_NOTIFY_PROJECT_ROOTS=[]` leaves scope unrestricted. A nonempty array allows
only a valid absolute event `cwd` equal to or beneath a root. Missing/invalid/
outside cwd skips silently; process cwd never bypasses an explicit scope. Relative
roots and parent traversal are rejected. Compare path components with Windows or
POSIX semantics regardless of the host OS.

Scope is lexical, not a security boundary or an internal-task classifier. It does
not resolve symlinks or normalize Windows extended paths (`\\?\D:\...`) to ordinary
drive paths. Mixed extended/ordinary forms may be skipped. Projects and worktrees
outside configured roots are skipped too. Keep scope unrestricted unless the user
deliberately wants these directory restrictions; background tasks may also notify.

## 4. Metadata and notification construction

| Field | Source and behavior |
| --- | --- |
| Device | Configured alias, otherwise hostname; safe `unknown-device` fallback; at most 64 characters |
| Project | Basename of usable absolute event cwd, otherwise process cwd, then `unknown-project`; at most 64 characters |
| Task | Optional exact-thread lookup described below; missing/private name is omitted; at most 80 characters |
| Status | Fixed neutral `本轮已结束`; does not infer success, failure or approval requirements |

Check the complete metadata label before display truncation. The shared check
recognizes configured topic/token values and obvious credential, code, URL and
path markers, including quoted credential keys. Normalize whitespace and remove
unsafe control characters. A rejected device/project label uses its generic
fallback; a rejected task name is omitted without suppressing the notification.
These heuristics cannot identify arbitrary confidential prose or unknown secrets.
Device/project labels must be non-sensitive; task-title disclosure remains opt-in.

The title contains the available project and optional task name, followed by
`本轮已结束`; if neither is available, it starts with `Codex`. The body contains
Chinese labels for device, project, optional task and status. Only the computer
tag is sent. There are no replies, prompts, full paths, raw events or thread/turn
IDs in the outgoing display content. See README for the exact examples.

### Optional task names

`CODEX_NOTIFY_TASK_TITLE=1` enables a best-effort lookup in the existing local
`session_index.jsonl`; default `0` avoids the lookup entirely. `task_metadata.py`
validates the event's UUID-shaped `thread-id` and reads at most the final 1 MiB of
the index under process-environment `CODEX_HOME` (default `~/.codex`). It searches
backward for the latest matching ID and uses that record's nonempty `thread_name`.
An empty latest title does not resurrect an older title.

This index is an internal detail, not a stable public API. Missing, unreadable,
changed or out-of-tail metadata must fall back without blocking delivery. Stale
index data can show an older task name. Do not modify the index, read transcripts
or databases, or extract a task title from prompts. The title itself may contain
sensitive text, so opting in permits its disclosure subject to the limited checks.

## 5. ntfy transport and failure handling

`providers/ntfy.py` validates the topic, optional bearer token, server and timeout.
The server must be an HTTPS origin with optional port and no credentials, non-root
path, query or fragment. The default is `https://ntfy.sh`. Certificate verification
stays enabled and redirects are rejected, including same-host redirects.

The provider POSTs UTF-8 JSON to the server root with `topic`, `title`, `message`,
normal `priority: 3` and `tags: ["computer"]`. Optional authentication uses the
Authorization header. The complete JSON envelope is bounded to 4,096 bytes.
Responses are closed without reading their bodies; non-2xx status is a failure.

There is one attempt per eligible invocation and no retry. The configured timeout
(default 5 seconds, greater than 0 and at most 30) bounds both the socket wait and
the caller's total delivery wait. A daemon worker prevents a stuck resolver or
slow response headers from holding the CLI process open indefinitely; it is not
a persistent background service. Process startup/scheduling is outside that wait.

| Outcome | Exit | Diagnostic |
| --- | --- | --- |
| Publish completes | `0` | None |
| Unsupported event or explicit scope skip | `0` | None |
| Expected provider/network failure or timeout | `0` | Short sanitized stderr message |
| Missing/malformed input or invalid/missing required configuration | `2` | Short sanitized stderr message |

Do not echo raw events, config values, HTTP response bodies or underlying network
exception text. Provider failures must not turn a completed Codex turn into an
apparent task failure. Exit `0` alone is not proof of receipt; timeout leaves
delivery unknown. There is no deduplication store or exactly-once guarantee.

## 6. Regression and publication checks

For runtime changes, run the full offline suite and compilation checks documented
in README. Keep coverage for:

- Event parsing, malformed/deep JSON, unsupported events and missing optional fields.
- Environment precedence, dotenv syntax, invalid configuration and hostname fallback.
- Windows/POSIX/UNC project names, optional scope, component boundaries and cwd fallback.
- Unicode, whitespace, truncation and privacy checks before truncation.
- Task-name opt-in, exact matching, latest rename, bounded index reads and safe fallbacks.
- No reply/input content in requests or diagnostics, including with obsolete settings.
- JSON request formation, auth, TLS/network errors, redirect refusal, size and deadlines.
- CLI invocation from another working directory and paths containing spaces/Unicode.

Automated tests use fake configuration and mock transport; in-process sockets/DNS
are blocked. Never run the real smoke script as part of test discovery. GitHub
Actions runs the suite on Windows/macOS with Python 3.10 and 3.13. Verify the
workflow for the actual published revision rather than citing an older green run.

Inspect the diff and staged files before publishing. Keep `.env`, tokens, topics
and personal paths out of Git. Documentation-only changes need example/link and
diff checks; they do not require another manual phone test. Keep detailed results
in VALIDATION rather than duplicating test counts here.

## 7. Real-device acceptance and maintenance

Follow README for the manual smoke test and user-level Codex hook setup. Use the
actual Python executable and sender path. Preserve any existing desktop Computer
Use wrapper; its forwarding mechanism is version-specific. The separate CLI on
PATH is not evidence of which runtime the desktop app uses.

Acceptance requires phone receipt, not just an HTTP response or exit code. On a
new installation or a relevant runtime/configuration change, confirm a real Codex
turn invokes the sender and the intended phone receives the recognizable message.
Record count/latency only when actually observed; do not infer them from silence
or successful unit tests. Check background/lock-screen and network conditions as
needed for the target environment.

The cross-device acceptance contract remains in AGENTS section 15. It includes
Windows and macOS, Android and iPhone, distinct aliases for two computers sharing
one personal topic, different users' separate topics, privacy and graceful failure.
See VALIDATION's checklist for remaining environments and evidence requirements.
Acceptance of the current Windows installation does not complete that full matrix.

Preserve private configuration and installed paths during updates. Recheck paths
and actual delivery after app/interpreter/hook changes or reported failures. Do
not repeat the entire device matrix for ordinary documentation maintenance. Add
features or providers only when a concrete requirement or observed failure warrants
reopening an accepted decision.
