"""Optional REAL publish of a harmless synthetic Codex event; not a unit test."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import notify


def main() -> int:
    event = {
        "type": "agent-turn-complete",
        "cwd": str(ROOT),
    }
    print("Testing notification delivery using your local configuration. Check output and your phone.")
    return notify.handle_event(event, report_skips=True)


if __name__ == "__main__":
    sys.exit(main())
