# V1 validation record

Initial implementation validation was performed on 2026-09-20, followed by
Windows device checks on 2026-09-22 and local corrections on 2026-09-23.
This is not a claim that all V1 device acceptance criteria have been met.

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
- The original excerpt filter was heuristic and could not reliably identify
  unknown credentials or confidential prose. Decision 017 subsequently removed
  reply previews altogether. Non-sensitive metadata labels are still required;
  see the removal checks below and current README boundaries.

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

## Windows/Xiaomi real-device validation (2026-09-22)

Baseline: `8908b96`. The Windows/macOS x Python 3.10/3.13 matrix for that exact
commit was rechecked and all four jobs
[passed](https://github.com/xuqiang97/codex-notify/actions/runs/35501682450).
Windows Python 3.14.6 also passed all 64 offline tests, compile checks and
`git diff --check`. That CI run validates the baseline, not the subsequent changes.

Desktop app: 26.915.4065.0; running desktop Codex core: 0.155.0-alpha.9.2.
The separate CLI on PATH reported 0.153.4 and was not used to infer the desktop
runtime version. Phone: Xiaomi Android, Google Play ntfy with Instant Delivery
enabled. Exact handset/Android/ntfy version numbers were not recorded.

User-level config used absolute Python/sender paths and preserved the desktop's
existing Computer Use `turn-ended` handler through `--previous-notify`. The
sender used summary limit 300 and task-title lookup disabled. No topic, token,
personal path or original config is included in this record.

| Check | Observed result | Evidence boundary |
| --- | --- | --- |
| `scripts/smoke_test.py` to Xiaomi | Passed: one notification, timely receipt | Sender exit 0 without error plus user-confirmed phone receipt |
| Real desktop Codex task: `git status --short` | Passed: one notification, timely receipt | User-confirmed receipt after the real completed turn |
| Desktop background, Windows and phone locked; 30-second delayed task | Passed: one notification, timely receipt | User confirmed after following the combined lock-screen procedure; not a long-duration sleep test |
| Same project and conversation switched to local Work mode | Passed: one notification, timely receipt | User-confirmed receipt; other projects, new conversations and Cloud were not covered |

“Timely” is the user's qualitative observation; exact latency was not measured.
Individual successful-test clock times and Wi-Fi/cellular state were not recorded.
The user had independently verified Windows HTTP POST to Google Play ntfy before
this session, including timely receipt without a proxy; this does not establish
every later test's network conditions.

An additional unwanted notification arrived at 10:42:19 UTC+08:00 with a runtime
folder label and `{"suggestions":[]}`. Read-only diagnosis found a nearby
`ambient_suggestions` turn and matching desktop background-generation code. This
supports an internal-task source, not duplicate delivery of the user's test turn.
The original sender had no project scope and would accept any supported completion.

## Local corrections and regression (2026-09-23)

- Check the complete project basename for configured secrets and sensitive
  markers before truncation. New fake-secret tests cover Windows/POSIX names and
  fallback cwd; no real secrets are used as fixtures.
- Add optional `CODEX_NOTIFY_PROJECT_ROOTS` (Decision 015). This machine's user
  selected a local project-parent scope. Other machines default to unrestricted
  behavior until individually configured. The script does not classify task text
  or read runtime logs/databases to decide whether to send.
- Local `.env` was backed up and only its scope changed; topic/token, device,
  summary and task-title settings were preserved. The desktop wrapper remained
  intact. The now-installed desktop 26.917.6896.0 / core 0.155.0-alpha.16 still
  has the inspected wrapper support; this is not a repeat of the prior device tests.
- Windows 11 build 26200 / Python 3.14.6: **76 offline tests passed**. Python 3.10
  AST compatibility, compilation and three README TOML examples (including the
  nested JSON command) passed. Scope examples also parsed successfully.
- With actual local config loaded but transport mocked and sockets blocked,
  an allowed project event dispatched once; a synthetic runtime-folder event and
  an event without cwd dispatched zero times. All returned 0 without stderr.
  These checks sent no real notifications and do not establish live suppression
  of a future desktop background task.

The scope became effective at **18:10:15 UTC+08:00**. A subsequently reported
runtime-folder notification containing `{"exclude":[]}` was timestamped 18:06:10,
before the new code/configuration took effect. Replaying a synthetic event with
that directory shape and output against the current local configuration, with
transport mocked, dispatched zero notifications and returned 0 without stderr.

The user subsequently confirmed that normal project notifications still arrived
and no further runtime-folder notifications had been observed after 18:10:15.
Post-change real project receipt is therefore confirmed. Exact post-change
latency/count and the observation duration were not measured; absence so far is
not a guarantee that every future internal task will be excluded. Continue normal
use rather than repeating the entire phone-client/lock-screen setup. Check the
[Offline tests workflow](https://github.com/xuqiang97/codex-notify/actions/workflows/tests.yml)
for the exact published revision's cross-platform CI result; the baseline CI link
above is not evidence for the changed code.

## Scope follow-up and presentation refinement (2026-09-23)

A later user report identified a background suggestion notification from inside an
allowed project. The earlier absence-of-noise observation was therefore limited:
directory scope cannot distinguish background tasks within allowed roots. The user
accepted these notifications. Separately, a normal project task ran in a managed
Codex worktree outside the selected root and was skipped. Adding the worktree root
restored delivery, confirmed by the user on Xiaomi. The user then chose `[]` locally
to avoid further directory-based omissions; mocked checks covered ordinary projects,
worktrees, another drive, extended Windows paths and missing cwd. The optional
scope implementation remains available; these checks do not guarantee all delivery.

The user approved Decision 016's presentation changes: Chinese labels, neutral
turn-ended wording, reply preview, and removal of the success checkmark. At that
stage local task names were enabled with user consent, preview limit 300 and unrestricted scope.
Other users retain the opt-in default. No private configuration or actual task
titles are recorded here. The synthetic smoke text is now neutral as well.

Local validation: **77 offline tests passed** on Windows/Python 3.14.6, including
ordinary/error-looking replies, JSON, missing/private previews, disabled previews,
missing task names, Unicode HTTP encoding and computer-only tags. Python 3.10
syntax compatibility and compile checks passed. These were local checks at that
stage; subsequent metadata-only phone receipt is recorded below.

## Reply-preview removal (2026-09-23)

After finding task names sufficient, the user disabled reply previews locally
and then explicitly requested complete removal from V1 (Decision 017). A prior
read-only audit reproduced missed JSON credentials and business prose with fake
fixtures while previews were enabled. This motivated removal, not a claim of
complete redaction. The user reported task names useful; this is not a new
measurement of phone latency or duplicate count.

The summary builder, generic reply fallback, preview configuration and outgoing
preview field are now absent. The local obsolete setting was removed with an
external backup; task names remain enabled and directory scope unrestricted.
Existing preview settings in other installations are ignored, including nonzero
or invalid values, and cannot restore reply content. Dotenv syntax must still be
valid. Metadata privacy checks remain; task/device/project names may be sensitive.

Validation: **77 offline tests passed** on Windows/Python 3.14.6. Tests cover
short/long replies, JSON credentials, Chinese business prose, source snippets,
missing/non-string replies and old settings in both dotenv and environment.
Mocked event-to-HTTP checks assert metadata-only payloads with task names on/off
and with successful/failing transport; reply/input markers never enter request
bodies, headers or diagnostics. Existing Unicode, cross-platform paths, bounded
metadata, TLS, redirect, timeout and provider-failure checks continue to pass.
Python 3.10 AST compatibility, compile checks and `git diff --check` passed.
These tests publish nothing. Check the published revision's workflow result for
cross-platform CI; historical CI results do not validate this change.

The user subsequently confirmed that real notifications still arrived normally
on Xiaomi after reply-preview removal. This validates live receipt for the final
metadata-only format, with task names enabled locally. Exact latency and count
were not newly measured, and lock-screen/network variations were not repeated.
No original notification text, real task name, topic or token is recorded here.

## Final metadata privacy correction (2026-09-23)

A read-only audit reproduced a remaining metadata-filter gap using fake data:
quoted credential keys such as `{"password":"private-fixture"}` could appear in
device/project/task labels. The shared check now accepts a closing single or
double quote before the credential separator. This is a narrow correction to
the existing heuristic, not a general confidential-content classifier; reply
previews remain absent and task names remain opt-in.

Windows/Python 3.14.6: **79 offline tests passed**, including regression checks
for quoted keys, whitespace/case variations, normalization, ordinary task names
and all three metadata fields before truncation. A mocked event-to-HTTP check
confirms safe fallback labels, omitted sensitive task names and one dispatch
without exposing the fake value. Compilation, Python 3.10 AST compatibility and
`git diff --check` passed. No real notification was sent by these tests.

The correction was published as `d3711a6`; all four Windows/macOS x Python
3.10/3.13 CI jobs [passed](https://github.com/xuqiang97/codex-notify/actions/runs/35868779739).
The user subsequently confirmed that the real turn notification still arrived
normally on Xiaomi. This completes the final receipt check for the current
Windows machine. Exact latency/count and lock-screen/network variations were
not newly measured. The local Windows-to-Xiaomi use case is accepted and enters
maintenance; broader device acceptance remains limited to the checklist below.

## Real-environment acceptance checklist

No personal topic or phone subscription was available to the initial implementation
run, and that initial run did not modify user-level Codex config. Subsequent local
setup and user-reported iPhone receipt are recorded above. Execute the remaining
checks with private local
configuration; do not attach topics, tokens or confidential notification content
to the evidence.

| Environment | Required evidence | Status |
| --- | --- | --- |
| Windows machine A → ntfy → Xiaomi/Android | Manual smoke; real Codex completed turn; exactly one notification | Baseline passed; real receipt after the final metadata privacy correction confirmed by user, without a new latency/count measurement |
| Windows machine B → same personal topic | Distinct device alias; no source changes | Pending |
| macOS → ntfy → iPhone | Manual smoke; real Codex completed turn; exactly one notification | Receipt reported on 2026-09-20; exact count/latency pending |
| Two users with different topics | No cross-user receipt | Pending |
| Xiaomi/HyperOS | Foreground, background, locked immediately/after several minutes; Wi-Fi/cellular | Basic and combined short lock-screen receipt passed; extended/network matrix pending |
| iPhone | Foreground/background/lock-screen receipt; Unicode, Focus/permissions; Wi-Fi/cellular | Pending |
| Both desktops | GUI/terminal Codex launch, real Python paths and inherited env, real TLS/proxy/network outage behavior | Windows GUI path verified; remaining combinations and real outage behavior pending |
| Hosted ntfy | Real HTTPS acceptance and phone receipt; HTTP success alone is insufficient | Windows/Xiaomi baseline passed |

Record OS, Python, Codex and mobile app versions, test time, observed delivery
latency/count, and pass/fail for each row when tested. V1's complete end-to-end
acceptance remains pending until these checks are performed.
