#!/usr/bin/env python3
"""Codex agent-turn-complete -> a small, privacy-conscious mobile notification."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import socket
import sys
from typing import Mapping
import unicodedata

from providers import ConfigurationError, Notification, ProviderError
from providers import ntfy
from task_metadata import lookup_task_title


ENV_PATH = Path(__file__).resolve().with_name(".env")


@dataclass(frozen=True)
class Config:
    provider: str
    device: str = field(repr=False)
    ntfy: ntfy.NtfyConfig = field(repr=False)
    task_title: bool = False
    project_roots: tuple[PurePosixPath | PureWindowsPath, ...] = field(default=(), repr=False)


def parse_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("event must be valid JSON") from None
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    return event


def is_supported_event(event: dict) -> bool:
    return event.get("type") == "agent-turn-complete"


def absolute_path(value: object) -> PurePosixPath | PureWindowsPath | None:
    """Parse paths lexically on either OS, without filesystem access."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    windows = PureWindowsPath(value)
    path = windows if windows.drive or "\\" in value else PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        return None
    return path


def load_project_roots(raw: str) -> tuple[PurePosixPath | PureWindowsPath, ...]:
    diagnostic = "CODEX_NOTIFY_PROJECT_ROOTS must be a JSON array of absolute directory paths without '..'"
    try:
        values = json.loads(raw)
    except (ValueError, RecursionError):
        raise ConfigurationError(diagnostic) from None
    if not isinstance(values, list):
        raise ConfigurationError(diagnostic)
    roots = []
    for value in values:
        path = absolute_path(value)
        if path is None:
            raise ConfigurationError(diagnostic)
        roots.append(path)
    return tuple(roots)


def is_in_project_scope(event: dict, config: Config) -> bool:
    if not config.project_roots:
        return True
    # Never substitute the hook's cwd for a missing event cwd when scope is set.
    path = absolute_path(event.get("cwd"))
    return path is not None and any(path.is_relative_to(root) for root in config.project_roots)


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value lines; never execute, expand, or unescape text."""
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ConfigurationError("cannot read the local .env as UTF-8") from None
    values = {}
    for number, line in enumerate(contents.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigurationError(f"invalid .env syntax on line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"unclosed .env quote on line {number}")
            value = value[1:-1]
        values[key] = value
    return values


def load_config(
    environ: Mapping[str, str] | None = None, env_path: Path | None = None,
) -> Config | None:
    """Return None when explicitly disabled, before validating delivery settings."""
    values = read_env(ENV_PATH if env_path is None else env_path)
    values.update(os.environ if environ is None else environ)
    enabled = values.get("CODEX_NOTIFY_ENABLED", "1").strip()
    if enabled not in ("0", "1"):
        raise ConfigurationError("CODEX_NOTIFY_ENABLED must be 0 or 1")
    if enabled == "0":
        return None
    provider = values.get("CODEX_NOTIFY_PROVIDER", "ntfy").strip().lower()
    if provider != "ntfy":
        raise ConfigurationError("CODEX_NOTIFY_PROVIDER must be ntfy in V1")
    task_title = values.get("CODEX_NOTIFY_TASK_TITLE", "0").strip()
    if task_title not in ("0", "1"):
        raise ConfigurationError("CODEX_NOTIFY_TASK_TITLE must be 0 or 1")
    device = values.get("CODEX_NOTIFY_DEVICE", "").strip()
    if not device:
        try:
            device = socket.gethostname()
        except OSError:
            device = "unknown-device"
    roots = load_project_roots(values.get("CODEX_NOTIFY_PROJECT_ROOTS", "[]"))
    return Config(provider, device, ntfy.load_config(values), task_title == "1", roots)


def normalize(text: str) -> str:
    # Preserve normal Unicode and emoji joiners; drop control/bidi/surrogate data.
    cleaned = "".join(
        " " if char.isspace() else char
        for char in text
        if char.isspace() or not unicodedata.category(char).startswith("C") or char == "\u200d"
    )
    return " ".join(cleaned.split())


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def project_name(event: dict, secrets: tuple[str, ...] = ()) -> str:
    def basename(value: object) -> str:
        path = absolute_path(value)
        return path.name if path is not None else ""

    name = basename(event.get("cwd"))
    if not name:
        try:
            name = basename(os.getcwd())
        except OSError:
            pass
    # Inspect the full label: truncation can otherwise conceal part of a secret.
    if private_text(name, secrets):
        return "unknown-project"
    return truncate(normalize(name), 64) or "unknown-project"


# Conservative heuristics, NOT a general secret/business-data classifier.
# Reject metadata labels containing recognizable code, credentials, URLs or paths.
_SENSITIVE = re.compile(
    r"```|~~~|`|https?://|[A-Za-z]:[\\/]|\\\\|(?:^|[\s(\[\"'])/(?:\S+)"
    r"|(?:^|\n)(?:diff --git |@@ |[+-]{3} |\s*(?:def |class |import |from \S+ import |function |const |let |SELECT |INSERT ))"
    r"|\b(?:api[_ -]?key|access[_ -]?token|token|password|passwd|secret|authorization)\b[\"']?\s*[:=]"
    r"|\bBearer\s+\S+|\b(?:sk-|gh[pousr]_|github_pat_|ntfy_)[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b",
    re.IGNORECASE,
)


def private_text(text: str, secrets: tuple[str, ...]) -> bool:
    cleaned = normalize(text)
    return bool(_SENSITIVE.search(text) or _SENSITIVE.search(cleaned)) or any(
        secret and (secret in text or secret in cleaned) for secret in secrets
    )


def build_notification(event: dict, config: Config) -> Notification:
    secrets = (config.ntfy.topic, config.ntfy.token)
    device = normalize(config.device)
    if private_text(device, secrets):
        device = "unknown-device"
    project = project_name(event, secrets)
    task = lookup_task_title(event) if config.task_title else None
    if task is not None:
        task = None if private_text(task, secrets) else truncate(normalize(task), 80) or None
    title_parts = []
    if project != "unknown-project":
        title_parts.append(project)
    if task:
        title_parts.append(task)
    title_parts.append("本轮已结束")
    if len(title_parts) == 1:
        title_parts.insert(0, "Codex")
    lines = [f"设备：{truncate(device, 64) or 'unknown-device'}", f"项目：{project}"]
    if task:
        lines.append(f"任务：{task}")
    lines.append("状态：本轮已结束")
    return Notification(
        title=" · ".join(title_parts),
        message="\n".join(lines),
    )


def send_notification(config: Config, notification: Notification) -> None:
    if config.provider != "ntfy":
        raise ConfigurationError("CODEX_NOTIFY_PROVIDER must be ntfy in V1")
    ntfy.send_notification(config.ntfy, notification)


def handle_event(event: dict, *, report_skips: bool = False) -> int:
    """Shared delivery path for the hook and manual smoke test."""
    if not is_supported_event(event):
        return 0
    try:
        config = load_config()
        if config is None:
            if report_skips:
                print("Notifications are disabled (CODEX_NOTIFY_ENABLED=0); nothing was sent.")
            return 0
        if not is_in_project_scope(event, config):
            if report_skips:
                print("Test directory is outside the configured project scope; nothing was sent.")
            return 0
        send_notification(config, build_notification(event, config))
    except ConfigurationError as exc:
        print(f"codex-notify: {exc}", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"codex-notify: {exc}", file=sys.stderr)
        # Best effort: a push outage must not look like a failed Codex turn.
        return 0
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("codex-notify: expected one event JSON argument", file=sys.stderr)
        return 2
    try:
        event = parse_event(sys.argv[1])
    except ValueError as exc:
        print(f"codex-notify: {exc}", file=sys.stderr)
        return 2
    return handle_event(event)


if __name__ == "__main__":
    sys.exit(main())
