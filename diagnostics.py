"""Bounded, best-effort local diagnostics. Never accept event/message text."""

from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import re
from threading import Thread
import time
import uuid


LOG_DIR = Path(__file__).resolve().with_name(".notify-logs")
FILE_BYTES = 1024 * 1024
TOTAL_BYTES = 4 * FILE_BYTES
KEEP_DAYS = 14
LOCK_WAIT = 0.05
FLUSH_WAIT = 0.15
_LOG_NAME = re.compile(r"\d{4}-\d{2}-\d{2}(?:\.1)?\.jsonl")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_OUTCOMES = frozenset({
    "http_accepted", "unsupported_event", "disabled", "out_of_scope",
    "input_error", "configuration_error", "provider_error", "internal_error",
})
_ERRORS = frozenset({
    "missing_argument", "invalid_json", "configuration_error", "provider_error",
    "http_error", "dns_error", "tls_error", "socket_timeout", "network_error",
    "delivery_timeout", "payload_too_large", "internal_error",
    "dotenv_unreadable", "dotenv_syntax", "enabled_invalid", "provider_invalid",
    "task_title_invalid", "scope_invalid", "topic_missing", "topic_template",
    "topic_invalid", "server_invalid", "token_invalid", "timeout_invalid",
})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(stream) -> bool:
    """A crashed process releases this OS lock; never wait indefinitely."""
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)


def _unlock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _append(root: Path, record: dict) -> None:
    """Only called in a daemon thread, including all filesystem operations."""
    data = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(data) > 2048 or root.is_symlink():
        return
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / "writer.lock"
    if lock_path.is_symlink():
        return
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+b") as lock:
        if not _lock(lock):
            return
        try:
            today = _utc_now().date()
            oldest = (today - timedelta(days=KEEP_DAYS - 1)).isoformat()
            files = [p for p in root.iterdir() if _LOG_NAME.fullmatch(p.name)]
            if any(p.is_symlink() or not p.is_file() for p in files):
                return
            for path in files:
                if path.name[:10] < oldest:
                    path.unlink()
            active = root / (today.isoformat() + ".jsonl")
            backup = root / (today.isoformat() + ".1.jsonl")
            if active.exists() and active.stat().st_size + len(data) > FILE_BYTES:
                active.replace(backup)
            files = sorted(p for p in root.iterdir() if _LOG_NAME.fullmatch(p.name))
            total = sum(p.stat().st_size for p in files)
            for path in files:
                if total + len(data) <= TOTAL_BYTES:
                    break
                if path != active:
                    size = path.stat().st_size
                    path.unlink()
                    total -= size
            if total + len(data) > TOTAL_BYTES:
                return
            with os.fdopen(os.open(active, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600), "a+b") as stream:
                size = stream.seek(0, 2)
                if size:
                    stream.seek(max(0, size - 2048))
                    tail = stream.read(2048)
                    if not tail.endswith(b"\n"):
                        # Discard only a bounded partial last record left by an interrupted write.
                        boundary = tail.rfind(b"\n")
                        if boundary < 0 and size > 2048:
                            return
                        stream.truncate(max(0, size - 2048) + boundary + 1)
                        stream.seek(0, 2)
                stream.write(data)
        finally:
            _unlock(lock)


def _writer(root: Path, records: Queue) -> None:
    while True:
        record = records.get()
        if record is None:
            return
        try:
            _append(root, record)
        except Exception:
            # Diagnostic failures must not affect delivery or leak filesystem details.
            pass


class Attempt:
    """Two allowlisted records per call; no disk wait before notification delivery."""

    def __init__(self, source: str = "hook") -> None:
        self._worker = None
        self._fields = {}
        self.outcome = "internal_error"
        try:
            self._start = time.monotonic()
            self._base = {"schema": 1, "invocation": uuid.uuid4().hex,
                          "source": "manual" if source == "manual" else "hook"}
            self._records = Queue()
            self._worker = Thread(target=_writer, args=(LOG_DIR, self._records), daemon=True,
                                  name="notify-diagnostics")
            self._worker.start()
            self._emit("started")
        except Exception:
            self._worker = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self._fields["elapsed_ms"] = round((time.monotonic() - self._start) * 1000)
            self._emit("finished")
            self._records.put(None)
            if self._worker is not None:
                self._worker.join(FLUSH_WAIT)
        except Exception:
            pass

    def _emit(self, phase: str) -> None:
        if self._worker is not None:
            record = dict(self._base, time=_utc_now().isoformat(timespec="milliseconds"), phase=phase)
            if phase == "finished":
                record.update(self._fields)
                record["outcome"] = self.outcome if self.outcome in _OUTCOMES else "internal_error"
            self._records.put(record)

    def correlate(self, event: dict) -> None:
        # Called only after configuration enables delivery. Never read prompts/replies/cwd.
        try:
            thread = event.get("thread-id")
            turn = event.get("turn-id")
            if isinstance(thread, str) and _UUID.fullmatch(thread):
                thread = thread.lower()
                self._fields["thread_ref"] = hashlib.sha256(thread.encode("ascii")).hexdigest()[:24]
                if isinstance(turn, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", turn):
                    self._fields["event_ref"] = hashlib.sha256(
                        (thread + "\0" + turn).encode("ascii")).hexdigest()[:24]
        except Exception:
            pass

    def update(self, *, outcome=None, error_code=None, http_status=None,
               task_title_included=None, publish_ms=None, timeout_ms=None) -> None:
        # Explicit scalar fields prevent accidental logging of exception/config/event objects.
        if isinstance(outcome, str) and outcome in _OUTCOMES:
            self.outcome = outcome
        if error_code is not None:
            self._fields["error_code"] = (
                error_code if isinstance(error_code, str) and error_code in _ERRORS else "internal_error")
        if type(http_status) is int and 100 <= http_status <= 599:
            self._fields["http_status"] = http_status
        if type(task_title_included) is bool:
            self._fields["task_title_included"] = task_title_included
        for key, value in (("publish_ms", publish_ms), ("timeout_ms", timeout_ms)):
            if type(value) in (int, float) and 0 <= value <= 86400000:
                self._fields[key] = round(value)
