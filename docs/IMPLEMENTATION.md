# V1 Implementation Plan

This document turns the approved architecture into an executable implementation plan for the next coding agent.

Read `AGENTS.md` first. If this file and `AGENTS.md` conflict, follow `AGENTS.md`.

Implementation status: the Python/ntfy sender, local configuration, offline tests
and user setup instructions now exist. The sections below retain the approved
V1 plan and acceptance contract. See [VALIDATION.md](VALIDATION.md) for executed
software checks and remaining real-device acceptance; see [README](../README.md)
for the final configuration and commands.

## 1. Goal

Deliver the smallest reliable V1 that turns a real Codex `agent-turn-complete` event into a concise mobile notification through ntfy.

The same implementation must work on Windows and macOS.

## 2. Suggested implementation shape

Preferred starting structure:

```text
codex-notify/
├── notify.py
├── providers/
│   ├── __init__.py
│   └── ntfy.py
└── tests/
    └── test_notify.py
```

Do not create extra layers unless the implementation genuinely benefits from them.

A single-file implementation is acceptable only if event logic and provider HTTP logic remain clearly separated and testable.

## 3. Step 1 — Parse the Codex event

`notify.py` should expose a testable function for payload parsing.

Expected runtime invocation model:

```text
python notify.py "<json-payload>"
```

The first CLI argument is the Codex notification JSON.

Minimum behavior:

- no argument -> concise stderr message, no network call;
- invalid JSON -> concise stderr message, no network call;
- event type other than `agent-turn-complete` -> clean no-op;
- valid completion event -> continue.

Suggested internal model:

```python
def parse_event(raw: str) -> dict:
    ...

def is_supported_event(event: dict) -> bool:
    ...
```

Do not crash on unknown fields.

## 4. Step 2 — Load local configuration

Required configuration:

```text
CODEX_NOTIFY_PROVIDER=ntfy
NTFY_SERVER=https://ntfy.sh
NTFY_TOPIC=<random-private-topic>
```

Optional configuration:

```text
CODEX_NOTIFY_DEVICE=<friendly-device-name>
CODEX_NOTIFY_SUMMARY_MAX=300
CODEX_NOTIFY_TIMEOUT=5
```

Recommended defaults:

- provider: `ntfy`;
- server: `https://ntfy.sh`;
- device: hostname;
- summary max: `300`;
- timeout: `5` seconds.

`NTFY_TOPIC` has no default.

### Optional .env support

If implemented, use this precedence:

```text
OS environment > local .env > safe code defaults
```

Rules:

- `.env` is local-only and ignored by Git;
- do not require `python-dotenv` unless clearly justified;
- parser does not need advanced shell interpolation;
- blank lines and `#` comments should be safe;
- do not log secrets.

If .env support is omitted in the first coding pass, README must clearly explain how to set environment variables on Windows and macOS.

## 5. Step 3 — Derive device and project metadata

### Device

Use:

1. `CODEX_NOTIFY_DEVICE` when configured;
2. otherwise `socket.gethostname()`.

### Project

Goal: show only a friendly project/folder name.

Possible algorithm:

1. inspect the event for a working-directory-like field if one exists;
2. otherwise inspect the process working directory;
3. extract the final directory basename;
4. fallback to `unknown-project`.

Important:

- tolerate both Windows and POSIX path shapes even when tests execute on only one OS;
- never publish the complete absolute path by default.

Implementation may use `PureWindowsPath` / `PurePosixPath` or another well-tested strategy.

## 6. Step 4 — Build the completion summary

Source:

- primarily `last-assistant-message`.

Fallback:

```text
Codex finished the task.
```

Normalization:

- convert to text safely;
- collapse repeated whitespace/newlines for a phone-friendly preview;
- preserve Unicode;
- truncate to configured length;
- make truncation visually clear, e.g. ellipsis.

Do not:

- send `input-messages` by default;
- call an LLM to summarize;
- include full response text.

## 7. Step 5 — Build the notification model

Keep a small provider-neutral model, e.g.:

```python
title: str
message: str
metadata: dict[str, str]
```

Suggested user-facing content:

```text
Title:
Codex complete · Johnny-ThinkBook-A

Message:
Project: amazon-asin-image-hub
Status: completed
Summary: Finished implementation and tests...
```

The exact text may be polished, but the required information is:

- device;
- project;
- completed status;
- short summary.

Avoid adding timestamps unless they provide clear value; the phone notification already has delivery time.

## 8. Step 6 — Implement the ntfy provider

Use ntfy's official HTTP publish API.

Reference:

- https://docs.ntfy.sh/publish/

Requirements:

- HTTPS;
- configurable server;
- configurable topic;
- finite timeout;
- UTF-8;
- concise title and message;
- no real topic in source code;
- expected network errors are caught.

The implementation may use ntfy's JSON publish API or topic endpoint. Choose the form that produces the cleanest standard-library implementation.

Suggested fields, where useful:

- topic;
- title;
- message;
- tags such as completion/computer;
- normal/default priority.

Do not use attachments in V1.

Do not send the raw Codex event to ntfy.

## 9. Step 7 — Add provider dispatch

V1 only needs `ntfy`.

The dispatcher should still make the boundary explicit.

Example concept:

```python
def send_notification(config, notification) -> None:
    if config.provider == "ntfy":
        send_ntfy(...)
    else:
        raise ConfigurationError(...)
```

Do not implement Pushover/ServerChan yet.

Unknown provider values should produce a clear local configuration error.

## 10. Step 8 — Graceful error handling

Expected errors:

- missing CLI payload;
- invalid JSON;
- missing topic;
- unsupported provider;
- URL/network failure;
- timeout;
- non-success HTTP response.

Behavior:

- no raw secret output;
- no huge traceback for expected failures;
- short stderr diagnostic;
- terminate promptly.

Unexpected programming errors may remain visible during development, but user-facing expected failures should be controlled.

## 11. Step 9 — Automated tests

Prefer standard-library `unittest`.

Network calls must be mocked.

Minimum test matrix:

| Area | Case |
|---|---|
| Event | valid agent-turn-complete |
| Event | unsupported event ignored |
| Event | missing argument |
| Event | invalid JSON |
| Event | missing optional fields |
| Config | missing topic |
| Config | custom device name |
| Config | hostname fallback |
| Project | Windows-style path |
| Project | POSIX-style path |
| Summary | Unicode |
| Summary | whitespace normalization |
| Summary | truncation |
| Privacy | input-messages not pushed |
| Provider | expected ntfy URL/body/headers |
| Provider | timeout |
| Provider | HTTP/network failure |
| Dispatch | unsupported provider |

Tests must not require internet access.

Suggested command:

```bash
python -m unittest discover -v
```

If another test runner is introduced, document why.

## 12. Step 10 — Manual ntfy smoke test

After unit tests pass, perform an optional manual provider test with a disposable/private topic.

Goal:

```text
local Python -> ntfy.sh -> phone
```

Verify:

- Android receives notification;
- iPhone receives notification;
- title is readable;
- device is identifiable;
- project is identifiable;
- summary is not too long;
- Unicode is rendered correctly.

Do not commit the test topic afterward.

## 13. Step 11 — Real Codex integration test

Configure the user-level Codex config.

### Windows example

```toml
notify = [
  "python",
  "C:\\Users\\you\\path\\codex-notify\\notify.py"
]
```

### macOS example

```toml
notify = [
  "python3",
  "/Users/you/path/codex-notify/notify.py"
]
```

Then run a short but real Codex task and confirm:

1. Codex completes;
2. `notify.py` is invoked;
3. exactly one completion notification is published;
4. the correct phone receives it;
5. another user's topic does not receive it.

## 14. Step 12 — Multi-machine test

Developer A should test both Windows machines using the same personal topic but different device names.

Expected phone notifications should make the source obvious, e.g.:

```text
Codex complete · ThinkBook-A
```

and

```text
Codex complete · ThinkBook-B
```

Developer B should use a different topic on macOS.

No source-code change should be required between these configurations.

## 15. Xiaomi/Android practical test

Because Android vendor background policies can affect delivery, perform practical tests on the Xiaomi device:

- app foreground;
- app background;
- phone locked;
- several minutes after lock;
- Wi-Fi;
- mobile network if practical.

If needed, document Xiaomi/HyperOS notification, autostart, and battery-exemption settings in README.

Do not preemptively add a second provider before real testing.

## 16. iPhone practical test

On iPhone verify:

- ntfy App Store client can subscribe to the user's private topic;
- lock-screen delivery;
- background delivery;
- notification content formatting.

If iOS delivery is materially worse in practice, record evidence before proposing a provider change.

## 17. Documentation work required with implementation

The coding agent must update README from “planned” to “working” instructions after code exists.

README should then include:

- prerequisites;
- clone instructions;
- ntfy app install/subscription guidance;
- topic generation guidance;
- local config setup;
- Windows Codex config;
- macOS Codex config;
- manual test command;
- troubleshooting;
- privacy/security notes.

Keep `AGENTS.md` focused on contributor/agent rules, not end-user onboarding.

## 18. V1 definition of done

Do not call V1 complete until:

- implementation exists;
- automated tests pass;
- no real secrets are committed;
- Windows integration works;
- macOS integration works;
- Android notification works;
- iPhone notification works;
- README matches reality;
- public API/provider boundaries remain simple;
- known limitations are documented.

## 19. Expected first implementation commit summary

A good implementation should be explainable roughly as:

> Implement cross-platform Codex completion notifications using Python and ntfy, with privacy-conscious message formatting, local configuration, provider separation, and offline unit tests.

If the implementation becomes much harder to summarize than this, reassess whether V1 has become over-engineered.
