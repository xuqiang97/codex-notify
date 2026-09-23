# codex-notify

Send a concise Codex completion notification to Android or iPhone:

```text
Codex official notify -> notify.py -> HTTPS ntfy -> Android / iPhone
```

**The current Windows-to-Xiaomi setup is accepted and in maintenance; full V1
cross-device acceptance still has pending checks.** See
[validation details](docs/VALIDATION.md) for scope and dates. The sender
uses Python 3.10+ and the standard library only. It handles `agent-turn-complete`,
with one publish attempt per invocation. There is no background service, polling, AI summary
API, remote approval, or remote control. “Completed” means the Codex turn ended;
it does not assert that the task or its tests succeeded.

## 1. Prerequisites and clone

- Python 3.10+ and Git on Windows or macOS.
- A local Codex version supporting the official external `notify` hook.
- HTTPS access to `https://ntfy.sh` from the computer and phone.
- The ntfy mobile app, with notification permission enabled.

```console
git clone https://github.com/xuqiang97/codex-notify.git
cd codex-notify
```

No `pip install` is needed. In Windows PowerShell, use `python`; on macOS use
`python3`. Check the actual interpreter with:

```console
python -c "import sys; print(sys.version); print(sys.executable)"
```

Replace `python` with `python3` on macOS. Save the printed executable path for
Codex configuration, especially when launching Codex from a GUI with a different
PATH. Native Windows and native macOS are the targets; WSL needs its own Linux
Python and paths and has not been validated here.

## 2. Configure a private topic locally

Copy `.env.example` to **`.env` beside `notify.py`**:

Windows PowerShell:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(24))"
notepad .env
```

macOS:

```sh
cp .env.example .env
chmod 600 .env
python3 -c "import secrets; print(secrets.token_hex(24))"
open -e .env
```

These commands print a fresh 48-character random topic locally. Paste it into
`NTFY_TOPIC` in `.env`, replacing the template. Do not share the output, commit it,
paste it into issues, or put it in shell commands/history. Save `.env` as UTF-8
(Windows UTF-8 BOM and CRLF are supported). Restrict Windows file access to your
user using file Properties → Security if the directory is shared.

Set `CODEX_NOTIFY_DEVICE` to a short, non-sensitive machine alias such as
`Laptop-A`. Two machines owned by the same person can use the same topic and
different aliases. Different people should generate different topics.

| Setting | Default | Meaning |
| --- | --- | --- |
| `CODEX_NOTIFY_PROVIDER` | `ntfy` | Only V1 provider |
| `NTFY_SERVER` | `https://ntfy.sh` | HTTPS origin; optional port, no path/query/credentials |
| `NTFY_TOPIC` | Required | 1–64 ASCII letters/digits/`_`/`-`; use the random generator |
| `NTFY_TOKEN` | Empty | Optional bearer token for an authenticated server/account |
| `CODEX_NOTIFY_DEVICE` | Hostname | Blank also uses the hostname; label capped at 64 characters |
| `CODEX_NOTIFY_TASK_TITLE` | `0` | `1` opts into local task-title lookup; may disclose sensitive title text |
| `CODEX_NOTIFY_PROJECT_ROOTS` | `[]` | Optional JSON array of absolute folders; nonempty restricts notification to event cwd within those folders |
| `CODEX_NOTIFY_TIMEOUT` | `5` | Total delivery wait and socket timeout in seconds; `0 < value <= 30` |

Precedence: **process environment > script-local `.env` > non-secret defaults**.
An explicitly empty environment value overrides the file; an empty topic disables
publishing with a configuration error. The working project's `.env` is never
read. Each copy of the sender has its own `.env`.

The small `.env` parser accepts literal `KEY=value`, blank lines, full-line `#`
comments and matching single/double quotes. No `export`, interpolation, escape
processing, multiline values or inline comments. A `#` inside a value is literal.
Duplicate keys use the last value. Malformed lines fail locally without printing
their contents. Environment-only configuration is also supported; ensure the
Codex process actually inherits it and restart Codex after changing its environment.

### Limit notifications to your projects

Keep the default `CODEX_NOTIFY_PROJECT_ROOTS=[]` unless you deliberately need
directory restrictions. It allows ordinary projects and managed worktrees in any
location; a nonempty list can silently exclude legitimate tasks elsewhere.

Desktop background tasks can also produce completion events. In Windows testing,
a background suggestion task produced a notification labeled with a Codex runtime
folder and a JSON reply. To exclude directories outside your projects, set a
scope in the sender's `.env` (use your actual parent folder or individual projects):

Windows:

```dotenv
CODEX_NOTIFY_PROJECT_ROOTS=["D:/Projects"]
```

macOS, including multiple project locations:

```dotenv
CODEX_NOTIFY_PROJECT_ROOTS=["/Users/you/Projects","/Users/you/OtherProject"]
```

Forward slashes avoid JSON backslash escaping on Windows. Include external
worktree locations explicitly if you want notifications from them. The root itself
and its descendants match; `D:/Projects-old` does not match `D:/Projects`.
Windows paths compare without case, POSIX paths with case, independent of the OS
running the sender. This lexical check does not resolve filesystem symlinks.
It also does not normalize Windows extended paths: an event cwd such as
`\\?\D:\Projects\demo` does not match the ordinary root `D:/Projects`.
This restriction does not apply when scope is `[]`.

With a nonempty scope, missing, invalid, relative, parent-traversing or outside
event `cwd` skips silently with exit code zero. Process cwd is not a fallback for
scope checks. `[]` restores unrestricted behavior, including missing-cwd fallback
for project naming. Empty/malformed setting values are configuration errors.
No folder paths are added to outgoing notifications.

This limits directories; it is not a universal internal-task detector. Internal
tasks inside an allowed root may still notify. JSON answers from allowed projects
remain eligible. The sender reads neither prompts nor logs/databases to classify
tasks. Changes to the script-local `.env` take effect on the next invocation;
environment overrides still take precedence.

## 3. Subscribe on the phone

Install the official ntfy client using the links on
[ntfy's mobile setup page](https://docs.ntfy.sh/subscribe/phone/): Android from
Google Play/F-Droid/official APK, iPhone from the App Store. Add a subscription to
server `https://ntfy.sh` and exactly the topic saved in your `.env`. Allow
notifications and check the subscription is not muted.

For Xiaomi/HyperOS, test foreground, background and locked-screen delivery. If
background delivery fails, check notification permission, autostart/background
activity and battery restrictions for ntfy; menu names vary by OS release.
On iPhone, check notification/lock-screen permissions and Focus settings. Test on
Wi-Fi and cellular. The Windows/Xiaomi check used Google Play ntfy with Instant
Delivery enabled; there is no requirement to switch to F-Droid. See the validation
record for the conditions actually tested rather than assuming all combinations pass.

## 4. First manual test

From this repository directory, run:

Windows:

```powershell
python scripts/smoke_test.py
```

macOS:

```sh
python3 scripts/smoke_test.py
```

**This sends one real notification** using your local topic and a harmless
synthetic event. The notification body uses Chinese labels; no Codex task or AI
API is invoked. Expected display:

```text
codex-notify · 本轮已结束

设备：Laptop-A
项目：codex-notify
状态：本轮已结束
```

The sender is silent on success. Check both stderr and your phone: exit code zero
alone does not prove delivery. Provider errors also return zero to keep the Codex
turn successful. Missing/bad local configuration or input returns `2`.
If project scope is enabled, include this sender's repository location before
running the smoke test, or its synthetic event is intentionally skipped.

## 5. Configure Codex's official hook

Edit the **user-level** config: Windows `%USERPROFILE%\.codex\config.toml`, macOS
`~/.codex/config.toml` (or `config.toml` under your custom `CODEX_HOME`). Back it up
first and preserve existing settings. Put `notify` at the **TOML top level, before
any `[section]` headers**; replace an existing `notify` instead of defining it twice.
Project-local `.codex/config.toml` is not the place for this setting.

Windows, using your actual Python executable and clone path:

```toml
notify = [
  "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
  "C:\\Users\\you\\path\\codex-notify\\notify.py"
]
```

macOS, substituting the result of `python3 -c 'import sys; print(sys.executable)'`:

```toml
notify = [
  "/absolute/path/to/python3",
  "/Users/you/path/codex-notify/notify.py"
]
```

`"python"` or `"python3"` also works when it resolves in Codex's PATH. Each array
item is an argument, so paths containing spaces need no extra embedded quotation
marks. Do not add an event JSON argument: Codex appends it. Do not put the topic or
token in this config. Restart Codex, run a small task, and verify one notification
for its completed turn. Multiple completed turns produce multiple notifications;
there is no history or deduplication store.

References: [official notify documentation](https://developers.openai.com/zh-Hans/docs/config-file/config-advanced)
and [configuration reference](https://developers.openai.com/docs/config-file/config-reference).
The external hook and terminal `tui.notifications` are different features.
V1 only handles `agent-turn-complete`; approval events are ignored.

### Windows desktop: preserve an existing Computer Use hook

The desktop versions inspected in September 2026 can manage a user-level hook
whose first arguments are an installed `codex-computer-use.exe` and `turn-ended`.
Preserve that handler rather than discarding it. Those versions support forwarding
to a user command through `--previous-notify` and a **JSON-encoded command array**:

```toml
notify = [
  'C:\path\to\installed\codex-computer-use.exe',
  'turn-ended',
  '--previous-notify',
  '["C:\\path\\to\\python.exe", "D:\\Projects\\codex-notify\\notify.py"]'
]
```

This template uses TOML literal strings; the last item is JSON, so its Windows
backslashes are doubled. Keep the actual existing helper path and use your real
Python/sender paths. Do not add an event argument or a second top-level `notify`.
If a `--previous-notify` command already exists, inspect its purpose before
replacing it. This chain is a desktop implementation detail, not an official
cross-version API: after an app update check the resulting config and real delivery.
Without this wrapper, use the ordinary Windows/macOS examples above. Back up the
user config, preserve unrelated settings, and fully restart the desktop app after
changing the hook. The CLI on PATH can differ from the desktop's bundled runtime.

## Project and task names in notifications

The title shows the project basename and **本轮已结束** (this turn ended).
The body uses Chinese labels: 设备 (device), 项目 (project), optional 任务 (task),
状态 (status). There is no reply preview. Status always means the turn
ended; it does not infer success, failure, or a request for approval. Only the
computer tag is sent, without a success checkmark.
To also show the Codex task name, add this opt-in setting to the sender's `.env`:

```dotenv
CODEX_NOTIFY_TASK_TITLE=1
```

For example, a task named “修复登录问题” in project `my-app` appears as:

```text
my-app · 修复登录问题 · 本轮已结束

设备：Laptop-A
项目：my-app
任务：修复登录问题
状态：本轮已结束
```

Notifications never include assistant replies or user-input excerpts. Task names
are separate local metadata and remain an explicit opt-in.

The official completion event is not assumed to contain a task title. When
explicitly enabled, the sender matches its `thread-id` to the existing local
`session_index.jsonl` in `CODEX_HOME` (default `~/.codex`). It reads only the last
1 MiB and uses the latest matching `thread_name`, capped at 80 characters after
privacy checks. It never uses `input-messages` or another task's name as a fallback,
never reads transcripts or databases, and never writes to the index.

This index is an internal Codex detail, **not a stable API**. Missing, unreadable,
changed, incomplete or older-than-the-tail metadata produces a project-only title;
stale index data can show an older task name. If the project is unavailable too,
the sender shows the available task name or `Codex`. A project name remains a
folder basename, not necessarily a custom sidebar project label. The synthetic
smoke test has no thread ID, so it intentionally shows only the project.

Task names may contain sensitive words or originate from your initial request.
Enabling this option permits that bounded title to leave the computer. Rename
sensitive tasks or keep the option disabled. The same conservative content filters
apply; they cannot detect arbitrary confidential prose.

## Privacy and security boundaries

- Reply previews have been removed. `last-assistant-message` and `input-messages`
  are never used to construct notification content. Remove obsolete
  `CODEX_NOTIFY_SUMMARY_MAX` entries from your `.env` or environment; if left in
  place, they are ignored and cannot re-enable previews. No fallback preview text
  is sent. A future preview feature requires a separate design and privacy review.
- Anonymous topics are shared secrets, not authenticated private channels.
  Anyone who knows a topic may be able to read/publish to it. Generate random
  topics, rotate one if exposed, and use server-supported authentication/ACLs when
  needed. A token does not by itself make an otherwise public topic private.
- `.env` and `.env.*` are Git-ignored, except the safe `.env.example`. Never use
  `git add -f` for private config. The template topic is rejected by the sender.
- Only device alias, project **basename**, fixed turn-ended status and (when opted
  in) a bounded task title are sent. User inputs, assistant replies, thread/turn
  IDs, unknown fields and the raw event are not forwarded. Project uses an
  absolute event `cwd` if usable, otherwise process cwd, then `unknown-project`.
  Both Windows and POSIX paths work. Project/device labels are capped at 64
  characters; task names at 80. Whitespace is normalized and Unicode preserved.
- Metadata labels retain conservative pattern checks for recognizable paths,
  URLs, code/credential markers (including quoted JSON credential keys) and the
  configured topic/token. These checks are
  **not a guarantee against arbitrary confidential prose or unknown credentials**.
  Keep device/project labels non-sensitive and leave task names disabled when
  their disclosure is inappropriate. Removing reply previews does not anonymize
  metadata. No source files, diffs or conversations are read from disk; the
  optional task-name lookup only reads the bounded local index described above.
  No content is written to application logs.
- HTTPS certificate verification stays enabled. HTTP, URL credentials, query
  strings and redirects are rejected. Topics go in the JSON body, tokens only in
  the Authorization header. Errors never echo the payload, config values, URL,
  provider response body or underlying exception text.
- Transport encryption is not end-to-end encryption. ntfy and the mobile push
  infrastructure process notifications; server caching follows ntfy defaults.
  Phone lock-screen previews may expose content. See the
  [ntfy publish/security details](https://docs.ntfy.sh/publish/) and
  [privacy policy](https://ntfy.sh/docs/privacy/).
- One attempt, no retries. A daemon network worker plus a bounded wait prevents
  DNS/slow headers from keeping the CLI alive beyond the timeout (apart from
  process startup/scheduling). On timeout delivery is unknown; retrying manually
  could duplicate a message. This is a best-effort convenience notification.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `NTFY_TOPIC is required` or template error | Edit the sender's `.env`, remove stale/empty environment overrides |
| Invalid `.env` / UTF-8 error | Check reported line, matching quotes, encoding and file permissions |
| HTTP 401/403 | Topic permissions, server account and optional token |
| HTTP 429 | Server rate limits; sender does not retry |
| HTTP 3xx | Use the server's final HTTPS origin; redirects are deliberately disabled |
| Connection error/timeout | DNS, firewall/proxy, TLS trust, HTTPS reachability; do not disable TLS verification |
| Manual test works, Codex does not | User-level top-level `notify`, absolute paths, correct interpreter, restart Codex, supported event/version |
| Notification goes to wrong phone | Environment overrides, server/topic subscription; different users need separate topics |
| No reply preview, even with the old preview setting | Expected: reply previews were removed; only completion metadata is sent |
| Wrong project label | Missing/invalid event cwd; unrestricted mode falls back to the hook process directory |
| Notifications labeled with runtime folders | Optional project scope can exclude those directories, but also excludes unlisted projects/worktrees; it does not identify every background task |
| No notification after enabling project scope | Check event cwd, JSON path syntax, and whether the project/worktree/smoke-test directory is included |
| Phone delivery delayed | App permissions, mute/Focus, battery/background restrictions and network; compare foreground vs locked |

Proxy behavior follows Python urllib's platform/environment proxy configuration.
The worker observes the same timeout and safe error handling through a proxy.
Changing `NTFY_SERVER` to a self-hosted origin is supported, but deployment and
iOS push forwarding configuration are outside V1; follow ntfy's server docs.

## Updating an existing installation

Keep the sender at the path referenced by your user-level hook. Before updating,
check `git status --short` and keep a private backup of `.env` outside the
repository. With a clean working tree, use `git pull --ff-only`; if Git reports
divergence or local changes, resolve them rather than force-resetting the clone.
Do not copy `.env.example` over an existing `.env`. Review template changes and
merge only needed settings, preserving your private topic and device alias.

The next hook invocation uses the updated script and `.env`. After moving the
repository, replacing Python, changing hook configuration or updating the desktop
app, check the actual executable paths and confirm a real completed turn reaches
the phone. Preserve any existing desktop hook wrapper as described above. Restart
Codex after changing its hook or inherited environment. Documentation-only updates
do not require another phone test; repeat relevant device checks when behavior or
the environment changes, or when delivery becomes abnormal.

## Development and validation

Read [AGENTS.md](AGENTS.md), [implementation details](docs/IMPLEMENTATION.md) and
[accepted decisions](docs/DECISIONS.md) before editing. Core event/config/content
logic lives in `notify.py`; transport is in `providers/ntfy.py`, with provider-neutral
types in `providers/__init__.py`. Optional task-title lookup is isolated in
`task_metadata.py`.

```console
python -m unittest discover -v
python -m compileall -q notify.py task_metadata.py providers tests scripts
git diff --check
```

On macOS use `python3`. All automated tests are offline, use fake topics/tokens,
and mock HTTP; accidental socket use is blocked in the in-process test fixtures.
Subprocess tests cover argument handling and daemon timeout exit without real
publishing. The smoke script is manual and is **not** discovered by unittest.
GitHub Actions runs the same tests on Windows/macOS with Python 3.10 and 3.13.

See [validation record](docs/VALIDATION.md) for executed checks and pending device
acceptance. Automated request construction is not proof of mobile receipt.

## License

MIT — see [LICENSE](LICENSE).
