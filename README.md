# codex-notify

Cross-platform mobile notifications for OpenAI Codex completion events.

> **Status:** V1 specification is ready. The first implementation is intentionally left for the next coding agent to complete from the repository instructions.

## Why this project exists

Codex can run long local tasks on a developer's computer. When the developer walks away from the machine, repeatedly checking a remote-desktop session just to see whether the task has finished is wasteful.

`codex-notify` bridges Codex's official external `notify` hook to a mobile push provider so a completed local task can proactively notify a phone.

## V1 architecture

```text
Codex
  |
  | agent-turn-complete
  v
notify.py
  |
  | provider interface
  v
ntfy
  |
  +--> Android
  +--> iPhone
```

The same Python implementation must work on Windows and macOS.

## Decisions already made

- Use the official Codex `notify` hook.
- Use Python for the local notification program: `notify.py`.
- Use ntfy as the default V1 push provider.
- Start with the hosted `https://ntfy.sh` service; do not require self-hosting for V1.
- Keep the notification core provider-agnostic enough to add Pushover, ServerChan, or another provider later.
- Use one high-entropy ntfy topic per person. Multiple computers owned by the same person may share that person's topic.
- Never commit real topics, tokens, or other credentials.
- Default notifications should contain only device name, project name, completion status, and a short assistant-summary excerpt. Do not publish full prompts, full responses, source code, or sensitive business data.

See [AGENTS.md](AGENTS.md) for the implementation contract and [docs/DECISIONS.md](docs/DECISIONS.md) for the decision record.

## Target V1

V1 should:

1. Accept the Codex notification JSON passed to `notify.py`.
2. Ignore unsupported events.
3. Handle `agent-turn-complete`.
4. Derive a friendly device name and project name.
5. Build a compact, privacy-conscious completion notification.
6. Send it to ntfy over HTTPS.
7. Fail gracefully if the network or provider is unavailable.
8. Work on both Windows and macOS without platform-specific notification code.
9. Include automated tests that do not send real network notifications.

## Planned repository shape

```text
codex-notify/
├── AGENTS.md
├── README.md
├── LICENSE
├── .env.example
├── .gitignore
├── notify.py
├── providers/
│   ├── __init__.py
│   └── ntfy.py
├── tests/
│   └── test_notify.py
└── docs/
    ├── DECISIONS.md
    └── IMPLEMENTATION.md
```

The coding agent may keep the implementation even smaller if it preserves the provider boundary and all V1 acceptance criteria.

## Codex configuration

Codex notification configuration belongs in the user-level `~/.codex/config.toml`.

Windows example:

```toml
notify = [
  "python",
  "C:\\path\\to\\codex-notify\\notify.py"
]
```

macOS example:

```toml
notify = [
  "python3",
  "/Users/you/path/to/codex-notify/notify.py"
]
```

Codex currently supports the external `agent-turn-complete` notification event. Do not design V1 around approval-request notifications or other events that the external hook does not currently expose.

Official Codex reference:
- https://developers.openai.com/docs/config-file/config-advanced
- https://developers.openai.com/docs/config-file/config-reference

## ntfy

V1 uses ntfy because it provides a simple HTTP API, Android and iOS clients, hosted service for zero-ops onboarding, and an open-source self-hosting path if requirements change later.

Official references:
- https://github.com/binwiederhier/ntfy
- https://docs.ntfy.sh/
- https://docs.ntfy.sh/publish/

## Security

Treat an anonymous ntfy topic as a secret. Use a long random value and never use predictable names such as `codex`, a person's name, a repository name, or a company name.

Real configuration belongs in local environment/config files ignored by Git. The repository should only contain safe examples.

## Development

Before making implementation changes, read:

1. [AGENTS.md](AGENTS.md)
2. [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)
3. [docs/DECISIONS.md](docs/DECISIONS.md)

The project should prefer Python's standard library and keep installation friction low.

## License

MIT
