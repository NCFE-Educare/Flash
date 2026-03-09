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
    from gmail_tools import gmail_tools_server
    from sheets_tools import sheets_data_server, sheets_format_server, sheets_visual_server
    from sub_agent import data_processor_agent, email_drafter_agent, gmail_agent, sheets_agent
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
            f"delegate to 'sheets_agent' subagent. Always include 'user_id={uid}' in the task prompt."
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
            "delegate to 'sheets_agent' subagent. Always include the user_id in the task prompt."
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
        "- Mock data / file processing → delegate to 'data_processor' subagent\n"
        "- Drafting emails (no Gmail account needed) → delegate to 'email_drafter' subagent\n"
        + gmail_rule
        + sheets_rule
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
    ]

    kwargs: dict = dict(
        mcp_servers={
            "my_tools": my_tools_server,
            "gmail_tools": gmail_tools_server,
            "sheets_data": sheets_data_server,
            "sheets_format": sheets_format_server,
            "sheets_visual": sheets_visual_server,
        },
        agents={
            "data_processor": data_processor_agent,
            "email_drafter": email_drafter_agent,
            "gmail_agent": gmail_agent,
            "sheets_agent": sheets_agent,
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
