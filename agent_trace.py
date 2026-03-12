"""
Agent trace module — writes agent activity to a file when AGENT_TRACE=1.

Run `python logs.py` in a separate terminal to watch the trace in real time.
"""

import os
import threading
from datetime import datetime
from pathlib import Path

TRACE_FILE = Path(__file__).parent / "agent_trace.md"
_ENABLED = os.environ.get("AGENT_TRACE", "").strip() == "1"
_LOCK = threading.Lock()


def is_enabled() -> bool:
    """Whether trace writing is enabled (AGENT_TRACE=1)."""
    return _ENABLED


def write(line: str) -> None:
    """Append a line to the trace file. No-op if AGENT_TRACE is not set."""
    if not _ENABLED:
        return
    try:
        with _LOCK:
            with open(TRACE_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
    except Exception:
        pass


def session_start(session_id: int, user_message: str) -> None:
    """Write trace session header with user message."""
    if not _ENABLED:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write("")
    write("=" * 70)
    write(f"=== Session {session_id} | {now} ===")
    write("")
    write("USER: " + user_message)
    write("")
    write("-" * 70)
