"""Optional REAL publish of a harmless synthetic Codex event; not a unit test."""

import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    script = Path(__file__).resolve().parents[1] / "notify.py"
    event = {
        "type": "agent-turn-complete",
        "cwd": str(script.parent),
        "last-assistant-message": "Manual notification test completed. 测试完成 ✅",
    }
    print("Sending one test event using your local configuration. Check stderr and your phone.")
    return subprocess.run([sys.executable, str(script), json.dumps(event)], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
