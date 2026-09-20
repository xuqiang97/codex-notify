"""Optional, bounded, read-only lookup of an existing Codex task title.

session_index.jsonl is an internal Codex file, not a stable public API. Missing,
changed, stale or unreadable metadata must never stop a completion notification.
No transcripts, database, history files or network services are accessed here.
"""

import json
import os
from pathlib import Path
import re


MAX_INDEX_BYTES = 1024 * 1024


def lookup_task_title(event: dict, codex_home: Path | None = None) -> str | None:
    thread_id = event.get("thread-id")
    if not isinstance(thread_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        thread_id, re.IGNORECASE,
    ):
        return None
    if codex_home is None:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = codex_home / "session_index.jsonl"
    try:
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            offset = max(0, size - MAX_INDEX_BYTES)
            stream.seek(offset)
            tail = stream.read(MAX_INDEX_BYTES)
        if offset:
            # Discard a possibly partial leading record. Never read unbounded data.
            tail = tail.partition(b"\n")[2]
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(record, dict):
            continue
        identifier = record.get("id")
        if isinstance(identifier, str) and identifier.lower() == thread_id.lower():
            title = record.get("thread_name")
            # An empty latest title must not resurrect an older (possibly private) name.
            return title if isinstance(title, str) and title.strip() else None
    return None
