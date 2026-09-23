# Architecture and Product Decisions

This document records the decisions already made for `codex-notify` V1 and the reasoning behind them.

It exists so future contributors and AI coding agents do not repeatedly reopen settled questions without new evidence.

## Decision 001 — Use Codex's official external `notify` hook

**Status:** Accepted

Use the official Codex external notification configuration rather than polling files, watching UI state, screen scraping, or automating remote desktop software.

OpenAI documents `notify` as an external-program hook and currently exposes the `agent-turn-complete` event.

Why:

- native integration point;
- lower fragility than UI automation;
- no need to infer whether Codex is finished;
- works naturally with a local script;
- aligns with the intended use case for desktop/chat/webhook notifications.

Consequence:

- V1 only promises completion notifications for events the external hook actually exposes.
- Approval-request notifications are not part of V1.

References:

- https://developers.openai.com/docs/config-file/config-advanced
- https://developers.openai.com/docs/config-file/config-reference

## Decision 002 — Use Python and keep `notify.py` as the entry point

**Status:** Accepted

The local program will be Python.

Why:

- both current maintainers already have Python installed;
- Windows and macOS are both supported well;
- OpenAI's documented notification example uses Python;
- JSON parsing, HTTP calls, hostname/path handling, and testing are straightforward;
- the same implementation can be maintained in one GitHub repository.

Consequence:

- no separate PowerShell and shell implementations;
- platform differences should be limited to Codex configuration paths/executable names.

## Decision 003 — Use ntfy as the default V1 provider

**Status:** Accepted

V1 will publish mobile notifications through ntfy.

Why ntfy fits this project:

- simple HTTP publish API;
- official Android client;
- official iOS client;
- hosted service available for zero-ops onboarding;
- open-source project;
- self-hosting remains possible later;
- sender code does not care whether the receiver is Android or iPhone;
- no desktop ntfy client is required for the sender;
- natural fit for a small Python bridge;
- easy for a public GitHub project to document and reuse.

This does **not** mean ntfy must be the only provider forever.

Potential future providers:

- Pushover for a mature commercial push experience;
- ServerChan for China-oriented delivery ecosystems;
- DingTalk/Feishu/webhook-based providers if team workflows justify them.

## Decision 004 — Use hosted `ntfy.sh` first

**Status:** Accepted

V1 defaults to:

```text
https://ntfy.sh
```

Why:

- no server deployment;
- no Docker/domain/TLS maintenance;
- fastest way to validate the real user experience;
- adequate for non-critical Codex completion notifications.

Self-hosting is deliberately deferred.

When to reconsider:

- privacy requirements increase;
- hosted delivery proves unreliable in the target networks;
- authentication/ACL requirements become stronger;
- notification volume or organizational policy changes.

## Decision 005 — Keep a lightweight provider boundary

**Status:** Accepted

Core Codex event handling must not be tightly coupled to ntfy HTTP details.

Conceptually:

```text
Codex event parsing
        |
message construction
        |
provider selection
        |
   ntfy provider
```

Why:

- preserves future options;
- makes unit testing easier;
- avoids a rewrite if the delivery provider changes;
- keeps product logic separate from network transport.

Non-goal:

- do not build a dynamic plugin marketplace or heavyweight plugin framework in V1.

## Decision 006 — Shared code, private per-user configuration

**Status:** Accepted

The GitHub repository contains shared implementation and safe example configuration only.

Each person configures their own topic locally.

Recommended model:

```text
Developer A:
  Windows A ----\
                 > same personal topic -> Developer A phone
  Windows B ----/

Developer B:
  macOS ---------------------> separate personal topic -> Developer B phone
```

Why:

- avoids cross-notifying coworkers;
- one person can receive notifications from multiple machines;
- no code changes are required to add a machine/user.

## Decision 007 — Treat anonymous ntfy topics as secrets

**Status:** Accepted

ntfy's public anonymous-topic model requires high-entropy topic names.

Rules:

- generate long random topics;
- never commit them;
- never use names such as `codex`, GitHub usernames, repository names, employee names, company names, emails, or phone numbers;
- do not expose the full topic in normal logs.

Reference:

- https://docs.ntfy.sh/publish/

## Decision 008 — Minimize pushed content

**Status:** Accepted

The default notification is a status signal, not a transcript sync mechanism.

Send:

- friendly device name;
- project name;
- completion status;
- optional task name (Decision 014); no assistant or user-message excerpts (Decision 017).

Do not send:

- user prompts or excerpts;
- assistant replies or excerpts;
- source code/diffs;
- business/customer data;
- credentials;
- full local paths.

Why:

- public hosted push infrastructure should receive the minimum necessary content;
- mobile notifications are easier to scan when concise;
- ntfy/FCM/APNS-style messages have practical size constraints;
- reduces accidental leakage.

The original roughly 300-character excerpt design was removed by Decision 017.

## Decision 009 — Prefer Python standard library

**Status:** Accepted

V1 should prefer:

- `json`;
- `os`;
- `sys`;
- `socket`;
- `pathlib` / path utilities;
- `urllib.request`;
- `unittest`;
- `unittest.mock`.

Why:

- clone-and-run experience;
- no mandatory package installation;
- smaller dependency/supply-chain surface;
- the problem is simple enough not to require a framework.

Third-party libraries may be introduced later if they solve a real problem.

## Decision 010 — Cross-platform core, no OS-specific notification APIs

**Status:** Accepted

Do not implement separate Windows toast/macOS Notification Center paths as the primary mobile solution.

The Python sender should be identical on Windows and macOS.

The mobile provider handles Android/iPhone delivery.

OS-specific differences should remain limited to:

- Python executable name/path;
- repository path;
- Codex `config.toml` location syntax.

## Decision 011 — Graceful failure is more important than guaranteed delivery

**Status:** Accepted

A failed push notification must not turn a successfully completed Codex task into an apparent failure.

Requirements:

- finite timeout;
- concise stderr diagnostic;
- no uncontrolled traceback for expected network errors;
- no unbounded retry loop;
- no indefinite blocking.

ntfy.sh is a convenience notification service, not a P0 production-alerting SLA for this project.

## Decision 012 — V1 stays completion-only

**Status:** Accepted

Do not expand V1 into:

- approval requests;
- remote approve/reject;
- remote Codex control;
- remote desktop automation;
- dashboards;
- persistent task history.

Reason:

The concrete problem being solved is:

> “I am away from my computer. Tell my phone when the local Codex task finishes.”

Solve this reliably first.

## Decision 013 — Validate real mobile delivery before adding more providers

**Status:** Accepted

After implementation, test the actual combinations:

- Windows -> ntfy -> Xiaomi Android;
- macOS -> ntfy -> iPhone;
- lock screen/background delivery;
- different Wi-Fi/mobile networks where practical.

Only add another provider because real testing reveals a need, not because additional integrations are easy to code.

For Xiaomi/HyperOS in particular, background/battery behavior should be tested in practice.

## Decision 014 — Project-first titles and optional local task names

**Status:** Accepted after the user requested specific project/task completion
notifications instead of the generic completion title.

Put the project basename and, when available and enabled, the Codex task title in
the mobile title. Keep the device in the body. Decision 016 refines the original
“本轮已完成” wording to “本轮已结束” to avoid implying task success.

Task names are disabled by default (`CODEX_NOTIFY_TASK_TITLE=0`). Opting in reads
only the last 1 MiB of Codex's existing local `session_index.jsonl`, matches the
exact event `thread-id`, and uses the latest matching `thread_name`. The index is
an internal detail, so missing/changed/unreadable metadata must degrade safely.
Do not create a database, scan transcripts or extract a title from user prompts.

This extends Decision 008's minimal payload with an explicitly enabled title,
filtered before truncation to 80 characters. Title text may itself be sensitive
and must be described as an explicit opt-in. Decision 017 removes reply previews
entirely while retaining optional task names. Core Python, official
notify, ntfy, standard-library and completion-only decisions remain unchanged.

## Decision 015 — Optional explicit project scope for desktop internal-task noise

**Status:** Accepted on 2026-09-23 after Windows validation exposed an unrelated
desktop background suggestion notification and the user selected a project root.

Support `CODEX_NOTIFY_PROJECT_ROOTS` as a JSON array of absolute local directories.
An empty array (default) retains the prior behavior. With roots configured, only
the event's absolute `cwd`, equal to or beneath a root, permits notification.
Missing/invalid/outside cwd skips silently; it must not fall back to the hook's
process directory for this decision. Use path components and platform path
semantics; reject relative paths and parent traversal.

Why: the observed internal task used a Codex runtime directory. Scope lets a user
exclude such directories without suppressing legitimate JSON responses, matching
a changing runtime hash, reading prompts or depending on undocumented event
source fields. It also lets each computer choose its own project locations.

Limits: this is directory scope, not general internal-task detection. Internal
tasks inside an allowed project can still notify. Symlinks are not resolved;
this is not a security sandbox. Worktrees and projects elsewhere need their own
roots. No new service, history store, provider or runtime dependency is added.

## Decision 016 — Recognizable tasks and neutral reply previews

**Status:** Accepted on 2026-09-23 after the user approved notification presentation
improvements and enabling task names on this Windows machine. **Preview portions
are superseded by Decision 017 below; neutral wording and metadata labels remain.**

Use “本轮已结束” in the title and status, Chinese body labels, and “回复预览” for
the bounded assistant excerpt. Replace the generic task-finished fallback with
“本轮已结束，请返回 Codex 查看结果。”. Keep only the computer tag; a turn ending is
not evidence of success. Preserve default length 300, existing environment keys,
privacy checks and task-title fallback. No AI summarization, reply classification,
new language framework, retries or history storage is added.

Task-title lookup remains opt-in and disabled by default for other users; the
current user explicitly enabled it locally. Missing titles never suppress a push.
The user also chose unrestricted local scope after directory filtering missed a
normal task in a managed worktree. Decision 015's optional capability remains for
users who deliberately want directory restrictions; internal-task noise is accepted
on this machine. Real configuration and task names are not committed.

## Decision 017 — Remove reply previews from V1

**Status:** Accepted on 2026-09-23 after the user explicitly requested deletion
rather than a disabled preview feature.

Notifications contain only device, project, optional task name and a neutral
turn-ended status. Remove the summary builder, generic reply fallback, length
setting and outgoing preview line. `last-assistant-message` and `input-messages`
are ignored for notification construction. Old `CODEX_NOTIFY_SUMMARY_MAX` entries
are unused settings and cannot restore content or cause value-validation errors;
the dotenv file must still have valid syntax.

Why: short ordinary replies could be forwarded in full, and heuristic filtering
missed JSON credential fields and arbitrary confidential prose. The user only
needs task identification and completion signals. Removal eliminates this reply
export path without trying to build a general-purpose redaction system.

Keep conservative privacy checks for device/project/task labels, optional bounded
task-title lookup, event filtering, provider behavior and per-machine directory
scope. Metadata can itself be sensitive; this decision does not promise otherwise.
Any phase-two reply-preview feature requires a new design and privacy review.

## Changing these decisions

A future contributor may propose a change, but should:

1. identify which decision is being changed;
2. explain the new evidence/problem;
3. update this document;
4. update `AGENTS.md` if implementation constraints change;
5. update README setup documentation;
6. preserve backward compatibility where reasonable.
