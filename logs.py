"""
Agent trace viewer — run this in a separate terminal to watch agent activity in real time.

Usage:
  1. Terminal 1: $env:AGENT_TRACE="1"; python run.py
  2. Terminal 2: python logs.py

When you send a query via the API, you'll see in Terminal 2:
  - What the user asked
  - Which agent is doing what (delegations, tools, responses)
"""

import sys
import time
from pathlib import Path

TRACE_FILE = Path(__file__).parent / "agent_trace.md"


def tail_file():
    """Tail the trace file and print new lines to stdout (like tail -f)."""
    if not TRACE_FILE.exists():
        TRACE_FILE.touch()
        print(f"Created {TRACE_FILE.name}. Waiting for agent activity...")
        print("(Make sure API is running with AGENT_TRACE=1)\n")

    with open(TRACE_FILE, "r", encoding="utf-8") as f:
        # Seek to end to only show new content
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                print(line, end="")
                sys.stdout.flush()
            else:
                time.sleep(0.1)


if __name__ == "__main__":
    print("Agent trace viewer — watching agent_trace.md")
    print("Send queries via the API to see activity here. Ctrl+C to exit.\n")
    try:
        tail_file()
    except KeyboardInterrupt:
        print("\nStopped.")
