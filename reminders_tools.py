"""Reminders MCP tools — create and list reminders. Delivered via SSE push when due."""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import create_reminder, get_reminders_for_user

IST = timezone(timedelta(hours=5, minutes=30))


def _parse_relative_offset(s: str) -> timedelta | None:
    """Parse '20 seconds', '5 minutes', '2 hours', '1 day' etc. Returns timedelta or None."""
    s = str(s).strip().lower()
    m = re.match(r"(\d+)\s*(sec(?:ond)?s?|min(?:ute)?s?|hrs?|hours?|days?)\b", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("sec"):
        return timedelta(seconds=n)
    if unit.startswith("min"):
        return timedelta(minutes=n)
    if "hr" in unit or unit.startswith("hour"):
        return timedelta(hours=n)
    if unit.startswith("day"):
        return timedelta(days=n)
    return None


@tool(
    "create_reminder",
    (
        "Create a reminder for the user. "
        "user_id: required. message: required — what to remind about. "
        "relative_offset: PREFERRED for 'in X seconds/minutes/hours' — e.g. '20 seconds', '5 minutes', '2 hours', '1 day'. "
        "Server computes exact time from now; use this for 'in 30 sec', 'in 1 min', 'in 2 hours' to avoid timing errors. "
        "remind_at: use ONLY for absolute times like 'tomorrow at 9am' — ISO datetime with timezone (e.g. 2025-03-17T09:00:00+05:30). "
        "If relative_offset is provided, remind_at is ignored. Default timezone Asia/Kolkata (IST)."
    ),
    {"user_id": int, "message": str, "relative_offset": str, "remind_at": str},
)
async def create_reminder_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Create a reminder. Use relative_offset for 'in X sec/min/hr' — server computes time correctly."""
    user_id = int(args["user_id"])
    message = str(args.get("message", "")).strip()
    relative_offset = (args.get("relative_offset") or "").strip()
    remind_at = (args.get("remind_at") or "").strip()

    if not message:
        return {"content": [{"type": "text", "text": "Error: message is required."}]}

    if relative_offset:
        delta = _parse_relative_offset(relative_offset)
        if delta is None:
            return {"content": [{"type": "text", "text": f"Error: could not parse relative_offset '{relative_offset}'. Use e.g. '20 seconds', '5 minutes', '2 hours'."}]}
        now = datetime.now(IST)
        target = now + delta
        remind_at = target.isoformat()
    elif not remind_at:
        return {"content": [{"type": "text", "text": "Error: provide either relative_offset (e.g. '5 minutes') or remind_at (ISO datetime with timezone)."}]}

    try:
        row = create_reminder(user_id, remind_at, message)
        result = {
            "message": "Reminder set successfully.",
            "reminder_id": row["id"],
            "remind_at": remind_at,
            "message": message,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating reminder: {e}"}]}


@tool(
    "list_reminders",
    (
        "List reminders for the user. user_id: required. "
        "include_delivered: optional bool (default False) — if True, show past/delivered reminders too."
    ),
    {"user_id": int, "include_delivered": bool},
)
async def list_reminders_tool(args: dict[str, Any]) -> dict[str, Any]:
    """List reminders for the user."""
    user_id = int(args["user_id"])
    include_delivered = bool(args.get("include_delivered", False))

    rows = get_reminders_for_user(user_id, include_delivered=include_delivered)
    if not rows:
        return {"content": [{"type": "text", "text": "No reminders found."}]}

    items = [
        {
            "id": r["id"],
            "remind_at": r["remind_at"],
            "message": r["message"],
            "delivered": bool(r["delivered"]),
        }
        for r in rows
    ]
    return {"content": [{"type": "text", "text": json.dumps(items, indent=2)}]}


reminders_tools_server = create_sdk_mcp_server(
    name="reminders",
    version="1.0.0",
    tools=[create_reminder_tool, list_reminders_tool],
)
