# AGENTS.md

This file is the primary implementation contract for AI coding agents working on this repository.

If an AI agent is asked to implement, review, refactor, test, or document `codex-notify`, read this file first, then read `docs/IMPLEMENTATION.md` and `docs/DECISIONS.md` before changing code.

## 1. Project mission

`codex-notify` solves one narrow problem well:

> When OpenAI Codex finishes a long-running local task on a developer's computer, proactively send a concise completion notification to the developer's phone.

The initial real-world environment is:

- Developer A: two Windows computers + Android phone (Xiaomi).
- Developer B: macOS computer + iPhone.
- Both developers use Codex locally and share this repository through GitHub.
- The same Python codebase should work across both desktop operating systems.
- V1 uses ntfy for mobile delivery.

The project is intentionally small. Do not turn it into a general workflow engine, daemon, remote-control system, or full observability platform.

## 2. Authoritative V1 decisions

These decisions are already approved. Do not silently replace them during implementation.

1. **Codex integration:** use the official external `notify` hook.
2. **Supported external event for V1:** `agent-turn-complete`.
3. **Local program:** Python, with `notify.py` as the entry point.
4. **Default push provider:** ntfy.
5. **Initial ntfy server:** hosted `https://ntfy.sh`.
6. **Cross-platform target:** Windows and macOS.
7. **Mobile target:** Android and iPhone.
8. **Repository model:** shared implementation, per-user/per-machine local configuration.
9. **Secrets:** never commit real ntfy topics, tokens, or credentials.
10. **Privacy:** notifications are summaries, not conversation exports.
11. **Extensibility:** ntfy is the V1 provider, not a permanent hard-coded dependency.
12. **Complexity:** keep V1 lightweight and easy to clone, configure, understand, test, and maintain.
13. **Dependencies:** prefer the Python standard library. Add third-party packages only when there is a clear benefit that outweighs setup cost.

## 3. Official Codex behavior that implementation may rely on

OpenAI documents the external Codex notification hook as:

```toml
notify = ["python3", "/path/to/notify.py"]
```

The notification program receives a JSON string as the first command-line argument. The official example parses it with:

```python
notification = json.loads(sys.argv[1])
```

The external `notify` hook currently supports `agent-turn-complete`.

Known fields used in OpenAI's documented example include:

- `type`
- `thread-id`
- `input-messages`
- `last-assistant-message`

Do not assume every future payload contains every field. Parse defensively.

A payload may contain additional fields in current or future Codex versions. Unknown fields must not break the program.

Official references:

- https://developers.openai.com/docs/config-file/config-advanced
- https://developers.openai.com/docs/config-file/config-reference

Important configuration constraint:

- `notify` belongs in the **user-level** `~/.codex/config.toml`.
- Do not instruct users to rely on a repository-local `.codex/config.toml` for this setting.

## 4. V1 functional flow

The target flow is:

```text
Codex
  |
  | invokes notify command with event JSON
  v
notify.py
  |
  +--> parse and validate event
  |
  +--> ignore unsupported event types
  |
  +--> derive metadata
  |      - device name
  |      - project name
  |      - short completion summary
  |
  +--> build privacy-conscious notification
  |
  +--> select provider
  |
  v
ntfy provider
  |
  | HTTPS publish
  v
ntfy.sh
  |
  +--> Android client
  +--> iOS client
```

## 5. Event-handling rules

### 5.1 Unsupported events

If `type != "agent-turn-complete"`:

- do not publish a notification;
- exit cleanly;
- do not raise an exception.

### 5.2 Malformed payloads

If the command-line payload is missing or invalid JSON:

- fail gracefully;
- print a short diagnostic to stderr;
- do not emit a Python traceback for expected input errors;
- do not attempt a network request.

### 5.3 Missing optional fields

Missing fields must degrade gracefully.

Examples:

- no `last-assistant-message` -> use a generic completion summary;
- no `input-messages` -> do not fail;
- no known working-directory field -> use a safe fallback for project naming.

Do not couple V1 to undocumented payload fields.

### 5.4 Optional project scope

`CODEX_NOTIFY_PROJECT_ROOTS` is a JSON array of absolute directory paths; the
default `[]` preserves notifications from any directory. When nonempty, only
publish if the event's valid absolute `cwd` is equal to or beneath a configured
root. Compare path components, not string prefixes, using Windows/POSIX path
semantics. Missing/invalid/outside `cwd` is a silent successful skip; do not use
process cwd to bypass this explicit scope. Reject relative roots and `..`.
This is a lexical notification scope, not a filesystem security boundary.

This opt-in handles observed desktop internal-task noise without guessing from
assistant JSON, hexadecimal directory names, prompt text or undocumented source
fields. Internal tasks running inside an allowed root are not distinguished.
Do not inspect transcripts/databases or build a background classifier for this.

## 6. Notification content

The default notification must be useful at a glance but conservative about data exposure.

Recommended shape:

```text
Title:
<project> · <task name, if enabled and available> · 本轮已完成

Message:
Device: <device>
Project: <project>
Task: <task name, if enabled and available>
Status: turn completed
Summary: <short summary>
```

Exact punctuation may evolve, but the information model should stay compact.
Completion means the Codex turn ended, not that all work or tests succeeded.
When `CODEX_NOTIFY_SUMMARY_MAX=0`, omit the summary line.

### Optional task name

The user requested recognizable project/task completion notifications. Task-title
lookup is opt-in with `CODEX_NOTIFY_TASK_TITLE=1` (default `0`). Use only the exact
event `thread-id` to look up the latest matching `thread_name` in the existing
`CODEX_HOME/session_index.jsonl` (default home `~/.codex`). Read at most the final
1 MiB, never modify it, and never read transcripts/databases or derive a title
from user prompts. This is best-effort internal metadata, not an official API.
Missing/unreadable/changed metadata must fall back to project-only notifications.
Apply privacy checks before truncating the title to 80 characters. The local title
can itself contain sensitive text: keep this setting independent of summaries
and document the disclosure when opting in. No new database or history service.

### 6.1 Device name

Configuration may override the device name.

Fallback should be the machine hostname, for example via Python's `socket.gethostname()`.

This matters because one user may run Codex on multiple computers.

### 6.2 Project name

Prefer a short repository/project folder name, not a full absolute filesystem path.

Potential derivation order:

1. a working-directory value from the event, if present and trustworthy;
2. process working directory;
3. a generic fallback such as `unknown-project`.

Only expose the final basename in the outgoing mobile notification.
Apply privacy checks to the complete basename before truncating its display label.

Windows and POSIX paths must both be handled correctly.

### 6.3 Summary

The summary may be based on `last-assistant-message`, but it must be truncated.

Default target: approximately 300 characters.

Requirements:

- preserve Unicode;
- normalize excessive whitespace;
- do not send the full assistant response by default;
- do not include `input-messages` in the outgoing notification by default;
- do not attempt AI summarization in V1;
- do not add an OpenAI/API call just to summarize the message.

If no assistant message exists, use a generic message such as `Codex finished the task.`

## 7. Privacy and security rules

These rules are non-negotiable for V1.

### 7.1 ntfy topic

For anonymous ntfy.sh use, the topic should be treated like a secret.

Never:

- commit a real topic;
- print the full topic in normal logs;
- use predictable topics such as `codex`, `johnny-codex`, a GitHub username, company name, repository name, phone number, or email.

Use high-entropy random topic names.

### 7.2 Notification payload

Do not publish by default:

- complete user prompts;
- complete assistant responses;
- source files;
- code diffs;
- API keys;
- credentials;
- customer/business data;
- full local paths.

The mobile message should remain a lightweight status notification.

### 7.3 Configuration files

Local secrets/configuration must be ignored by Git.

Provide only safe templates such as `.env.example`.

## 8. Configuration contract

The exact implementation may be refined, but V1 should support environment-based configuration with safe defaults.

Recommended variables:

```text
CODEX_NOTIFY_PROVIDER=ntfy
NTFY_SERVER=https://ntfy.sh
NTFY_TOPIC=<high-entropy-topic>
CODEX_NOTIFY_DEVICE=<optional-friendly-device-name>
CODEX_NOTIFY_SUMMARY_MAX=300
CODEX_NOTIFY_TIMEOUT=5
CODEX_NOTIFY_PROJECT_ROOTS=[]
```

Configuration precedence should be simple and documented.

A reasonable V1 policy is:

1. operating-system environment variables;
2. optional local `.env` file if implemented;
3. hard-coded safe defaults for non-secret values.

If `.env` support is added, `.env` must be ignored by Git.

Do not require a third-party dotenv package unless there is a strong reason. A tiny, well-tested parser is acceptable, or the project may simply document how to set environment variables.

The required secret-like value is `NTFY_TOPIC`. If it is missing, do not send anything; report a clear local configuration error.

## 9. Provider architecture

ntfy is the only provider required for V1, but the code must not entangle event parsing, message construction, and ntfy HTTP details.

A lightweight structure is preferred.

For example:

```text
notify.py
providers/
  __init__.py
  ntfy.py
```

or an equivalently simple functional boundary.

The core should conceptually call something like:

```python
send_notification(title, message, metadata)
```

Provider-specific code owns:

- provider configuration;
- HTTP endpoint construction;
- provider headers/body;
- provider-specific errors.

Core code owns:

- Codex payload parsing;
- event filtering;
- project/device derivation;
- summary normalization;
- privacy rules;
- provider selection.

Do not build a plugin framework with dynamic package loading for V1.

Future providers may include:

- Pushover;
- ServerChan;
- DingTalk/Feishu-style webhooks;
- another HTTP push service.

V1 does not need to implement them.

## 10. ntfy implementation guidance

Use ntfy's documented HTTP API.

Official references:

- https://docs.ntfy.sh/publish/
- https://github.com/binwiederhier/ntfy

Prefer HTTPS.

The implementation should support the hosted default:

```text
https://ntfy.sh
```

but the server URL must be configurable so a future self-hosted server can be used without rewriting the core.

Prefer Python standard-library HTTP support such as `urllib.request` unless there is a compelling reason to add `requests`.

Use a finite timeout. The notification hook must not hang indefinitely because a network endpoint is unavailable.

Do not send oversized messages. ntfy documents a normal message limit of 4,096 bytes; V1 summaries should be far smaller than that.

## 11. Failure behavior

Mobile notification is a convenience layer. It must never become a reason a completed Codex task appears broken.

Requirements:

- use a finite network timeout;
- catch expected network errors;
- print concise diagnostics to stderr;
- do not expose credentials in error messages;
- avoid unbounded retries;
- never block indefinitely.

A single retry with a short delay is acceptable, but not required for the first implementation.

Tests must cover provider failure.

## 12. Cross-platform rules

The Python code itself should not require:

- PowerShell;
- Bash;
- AppleScript;
- `terminal-notifier`;
- Windows toast APIs;
- OS-specific GUI libraries.

The only platform-specific setup should be the user's Codex command path and Python executable name.

Expected examples:

Windows:

```toml
notify = [
  "python",
  "C:\\path\\to\\codex-notify\\notify.py"
]
```

macOS:

```toml
notify = [
  "python3",
  "/Users/you/path/to/codex-notify/notify.py"
]
```

Do not assume path separators are the same across systems.

## 13. Python style

Target modern Python 3.

Guidelines:

- favor Python 3.10+ compatibility unless implementation evidence requires another baseline;
- use type hints for public/internal boundaries where useful;
- keep functions small and testable;
- use `main() -> int`;
- call `sys.exit(main())` at the entry point;
- avoid global mutable state;
- use the standard library where practical;
- keep provider network calls injectable/mockable in tests;
- preserve UTF-8/Unicode correctly.

Do not add a framework for a small script.

## 14. Testing requirements

Automated tests must not publish real ntfy messages.

At minimum test:

1. valid `agent-turn-complete` payload;
2. unsupported event type is ignored;
3. invalid JSON;
4. missing optional fields;
5. missing `NTFY_TOPIC`;
6. device-name fallback;
7. project-name extraction for Windows-style path;
8. project-name extraction for POSIX-style path;
9. Unicode summary;
10. summary whitespace normalization;
11. summary truncation;
12. ntfy request formation;
13. network timeout/provider error;
14. no prompt/full input content leaks into default notification.

Prefer `unittest` and `unittest.mock` so tests can run without third-party dependencies.

A real ntfy smoke test may be documented as an optional manual test, never as part of the default automated test suite.

## 15. V1 acceptance criteria

V1 is complete when all of the following are true:

- A Windows machine can configure Codex to invoke `notify.py`.
- A macOS machine can configure Codex to invoke the same `notify.py`.
- A real Codex `agent-turn-complete` event produces one mobile notification.
- Android receives the ntfy notification.
- iPhone receives the ntfy notification.
- Two computers owned by the same person can share one personal topic and are distinguishable by device name.
- Different users can use different topics without code changes.
- No real topic or token is present in the Git repository.
- The message contains project, status, device, and a short summary.
- The message does not include full prompts/responses by default.
- Network/provider failure does not produce an uncontrolled traceback or indefinite block.
- Automated tests pass without internet access.
- README setup instructions match the implementation.

## 16. Explicit V1 non-goals

Do not add these unless the repository owner explicitly requests them:

- approval-request notifications;
- remote approval from the phone;
- remote Codex control;
- remote desktop integration;
- task queues;
- long-running background daemon;
- web dashboard;
- database;
- task history service;
- analytics;
- multiple providers implemented at once;
- Docker requirement;
- self-hosted ntfy requirement;
- AI-generated summaries via another model/API;
- publishing complete Codex conversations to the push provider.

External Codex `notify` currently supports `agent-turn-complete`; do not pretend approval events are available through the same hook.

## 17. Implementation workflow for coding agents

When asked to implement V1:

1. Read this file completely.
2. Read `docs/IMPLEMENTATION.md`.
3. Read `docs/DECISIONS.md`.
4. Inspect the current repository before editing.
5. Preserve the decisions above.
6. Implement the smallest maintainable solution.
7. Add/update automated tests.
8. Run the full test suite.
9. Perform static/syntax checks appropriate to the project.
10. Update README if actual setup differs from the planned instructions.
11. Never insert a real ntfy topic or secret into committed files.
12. Summarize changed files, tests run, and any remaining limitations in the final response/commit description.

If a design question is not covered here, prefer simplicity, cross-platform behavior, privacy, and standard-library solutions.

## 18. Documentation maintenance

When behavior changes:

- update README for end-user setup;
- update this file if agent constraints change;
- update `docs/DECISIONS.md` when a product/architecture decision changes;
- update `docs/IMPLEMENTATION.md` when acceptance criteria or implementation sequencing changes.

Do not leave documentation describing an architecture that the code no longer follows.
