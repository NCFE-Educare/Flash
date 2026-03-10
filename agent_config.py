"""
Shared agent configuration factory.

Both main_agent.py (CLI) and api.py (/chat endpoints) import from here so that
any capability change — new tools, updated system prompt, new MCP servers — only
ever needs to be made in ONE place.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions


def build_cli_env() -> dict[str, str]:
    """Build env vars for the Claude CLI subprocess. Enables Bedrock when USE_BEDROCK=1."""
    env: dict[str, str] = {}
    if os.environ.get("USE_BEDROCK", "").strip() == "1":
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        env["AWS_REGION"] = os.environ.get("AWS_REGION", "us-east-1")
    return env


def make_agent_options(
    agent_cwd: Path,
    user_id: int | str | None = None,
    resume_id: str | None = None,
    include_partial_messages: bool = False,
) -> ClaudeAgentOptions:
    """
    Build and return a ClaudeAgentOptions instance.

    Args:
        agent_cwd:                 Working directory the agent is restricted to.
        user_id:                   Logged-in user's ID (None in CLI mode — agent will ask).
        resume_id:                 Claude session ID to resume (None = new session).
        include_partial_messages:  Set True for streaming endpoints.
    """
    from calendar_tools import calendar_tools_server
    from docs_tools import docs_server
    from drive_tools import drive_server
    from gmail_tools import gmail_tools_server
    from sheets_tools import sheets_data_server, sheets_format_server, sheets_visual_server
    from forms_tools import forms_server
    from slides_tools import slides_data_server, slides_format_server
    from sub_agent import calendar_agent, docs_agent, drive_agent, forms_agent, gmail_agent, sheets_agent, sheets_data_agent, sheets_format_agent, sheets_visual_agent, slides_agent, slides_data_agent, slides_format_agent
    from tools import my_tools_server

    uid = str(user_id) if user_id is not None else None

    if uid:
        user_context = f"The current user's ID is: {uid}.\n"
        gmail_rule = (
            f"- ANY Gmail task (read, search, send, reply, trash, labels, profile, etc.) → "
            f"delegate to 'gmail_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        sheets_rule = (
            f"- ANY Google Sheets task (create spreadsheet, read/write data, formatting, charts, "
            f"conditional formatting, dropdowns, sparklines, worksheet management, etc.) → "
            f"delegate to 'sheets_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        docs_rule = (
            f"- ANY Google Docs task (create document, read content, insert/append/replace text, etc.) → "
            f"delegate to 'docs_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        drive_rule = (
            f"- ANY Google Drive task (search, list, create, upload, rename, move, delete, share) → "
            f"delegate to 'drive_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        calendar_rule = (
            f"- ANY Google Calendar task (list events, create event, update, delete, list calendars) → "
            f"delegate to 'calendar_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        slides_rule = (
            f"- ANY Google Slides task (create presentation, add slides, insert text, formatting, bullets, etc.) → "
            f"delegate to 'slides_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
        forms_rule = (
            f"- ANY Google Forms task (create form, add questions, list forms, update form, delete questions) → "
            f"delegate to 'forms_agent' subagent. Always include 'user_id={uid}' in the task prompt.\n"
        )
    else:
        user_context = (
            "NOTE: No logged-in user — ask the user to provide their user_id before "
            "delegating any Gmail or Sheets tasks.\n"
        )
        gmail_rule = (
            "- ANY Gmail task (read, search, send, reply, trash, labels, profile, etc.) → "
            "delegate to 'gmail_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        sheets_rule = (
            "- ANY Google Sheets task (create spreadsheet, read/write data, formatting, charts, "
            "conditional formatting, dropdowns, sparklines, worksheet management, etc.) → "
            "delegate to 'sheets_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        docs_rule = (
            "- ANY Google Docs task (create document, read content, insert/append/replace text, etc.) → "
            "delegate to 'docs_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        drive_rule = (
            "- ANY Google Drive task (search, list, create, upload, rename, move, delete, share) → "
            "delegate to 'drive_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        calendar_rule = (
            "- ANY Google Calendar task (list events, create event, update, delete, list calendars) → "
            "delegate to 'calendar_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        slides_rule = (
            "- ANY Google Slides task (create presentation, add slides, insert text, formatting, bullets, etc.) → "
            "delegate to 'slides_agent' subagent. Always include the user_id in the task prompt.\n"
        )
        forms_rule = (
            "- ANY Google Forms task (create form, add questions, list forms, update form, delete questions) → "
            "delegate to 'forms_agent' subagent. Always include the user_id in the task prompt.\n"
        )

    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    time_context = (
        f"Current date and time (IST): {now.strftime('%A, %B %d, %Y at %H:%M:%S IST')}.\n"
    )

    system_prompt = (
        time_context
        + f"You are working in a restricted directory. Always use RELATIVE paths "
        f"(e.g. 'test.txt', './report.txt') — never absolute paths like /home/user/ or C:/. "
        f"Your working directory is: {agent_cwd}.\n"
        f"{user_context}"

        "\n=== RESPONSE FORMATTING RULES (follow strictly) ===\n"
        "- Use Markdown to make responses readable and scannable.\n"
        "- Use **bold** for key terms, headings, and important points.\n"
        "- Use *italics* for subtle emphasis where helpful.\n"
        "- Use bullet lists (- or *) for options, steps, or multiple items.\n"
        "- Use numbered lists (1. 2. 3.) for ordered steps or procedures.\n"
        "- Use markdown tables when presenting structured data (columns/rows).\n"
        "- Use emojis sparingly (1–3 per response) for clarity — e.g. ✅ 📋 📊 — not in every sentence.\n"
        "- Keep responses concise and friendly. Avoid walls of text.\n"

        "\n=== WEB SEARCH ===\n"
        "- You have access to the **WebSearch** tool. Use it directly (no delegation needed) whenever "
        "the user asks for real-time or up-to-date information such as current prices, news, weather, "
        "documentation, or anything that requires live internet data.\n"

        "\n=== DELEGATION RULES (always use Task tool, never handle yourself) ===\n"
        "- PDF creation, document export to PDF, or PDF-related tasks → delegate to 'docs_agent' subagent. Google Docs can export to PDF.\n"
        "- PowerPoint/presentation creation, slides, or .pptx tasks → delegate to 'slides_agent' subagent. Use Google Slides for presentations.\n"
        + gmail_rule
        + sheets_rule
        + docs_rule
        + drive_rule
        + calendar_rule
        + slides_rule
    )

    base_tools = ["Skill", "Task", "Bash", "Read", "Write", "WebSearch"]

    # Explicitly permit every tool exposed by each MCP server so the agent
    # can call them without additional permission prompts.
    mcp_tool_permissions = [
        "mcp__my_tools__*",
        "mcp__gmail_tools__*",
        "mcp__sheets_data__*",
        "mcp__sheets_format__*",
        "mcp__sheets_visual__*",
        "mcp__docs__*",
        "mcp__drive__*",
        "mcp__calendar__*",
        "mcp__slides_data__*",
        "mcp__slides_format__*",
        "mcp__forms__*",
    ]

    kwargs: dict = dict(
        mcp_servers={
            "my_tools": my_tools_server,
            "gmail_tools": gmail_tools_server,
            "sheets_data": sheets_data_server,
            "sheets_format": sheets_format_server,
            "sheets_visual": sheets_visual_server,
            "docs": docs_server,
            "drive": drive_server,
            "calendar": calendar_tools_server,
            "slides_data": slides_data_server,
            "slides_format": slides_format_server,
            "forms": forms_server,
        },
        agents={
            "gmail_agent": gmail_agent,
            "sheets_agent": sheets_agent,
            "sheets_data_agent": sheets_data_agent,
            "sheets_format_agent": sheets_format_agent,
            "sheets_visual_agent": sheets_visual_agent,
            "docs_agent": docs_agent,
            "drive_agent": drive_agent,
            "calendar_agent": calendar_agent,
            "slides_agent": slides_agent,
            "slides_data_agent": slides_data_agent,
            "slides_format_agent": slides_format_agent,
            "forms_agent": forms_agent,
        },
        tools=base_tools,
        allowed_tools=base_tools + mcp_tool_permissions,
        setting_sources=["project"],
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        cwd=str(agent_cwd),
        env=build_cli_env(),
        resume=resume_id,
    )

    if include_partial_messages:
        kwargs["include_partial_messages"] = True

    return ClaudeAgentOptions(**kwargs)
