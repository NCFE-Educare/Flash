import asyncio
import os
import sys
from pathlib import Path

# Load .env before SDK imports (ANTHROPIC_API_KEY required for Claude Code CLI)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    SystemMessage,
    ResultMessage,
)

# Import our modularized components
from tools import my_tools_server
from gmail_tools import gmail_tools_server
from sub_agent import data_processor_agent, email_drafter_agent


def _build_cli_env() -> dict[str, str]:
    """Build env vars for the CLI subprocess. Enables Bedrock when USE_BEDROCK=1 in .env."""
    env: dict[str, str] = {}
    if os.environ.get("USE_BEDROCK", "").strip() == "1":
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        env["AWS_REGION"] = os.environ.get("AWS_REGION", "us-east-1")
        # AWS creds from env (AWS_ACCESS_KEY_ID, etc.) or AWS_BEARER_TOKEN_BEDROCK
    return env


async def main():
    # Agent workspace - restrict to this folder only
    _agent_cwd = Path(__file__).parent / "cwd"
    _agent_cwd.mkdir(exist_ok=True)

    options = ClaudeAgentOptions(
        # 1. Register MCP servers
        mcp_servers={
            "my_tools": my_tools_server,
            "gmail_tools": gmail_tools_server,
        },
        
        # 2. Register subagents
        agents={
            "data_processor": data_processor_agent,
            "email_drafter": email_drafter_agent,
        },
        
        # 3. Restrict main agent tools - NO read_mock_data/draft_email so it MUST delegate to subagents
        tools=["Skill", "Task", "Bash", "Read", "Write"],
        allowed_tools=["Skill", "Task", "Bash", "Read", "Write"],
        
        # 4. Tell the SDK to look for the .claude/skills/ folder in the project
        setting_sources=["project"],
        
        # 4b. Force agent to use relative paths + delegate mock data/emails to subagents
        system_prompt=(
            f"You are working in a restricted directory. Always use RELATIVE paths (e.g. 'test.txt', './report.txt') - never absolute paths like /home/user/ or C:/. Your working directory is: {_agent_cwd}. "
            "IMPORTANT: When the user asks to read/process mock data (e.g. report.txt) or draft emails, you MUST use the Task tool to delegate to the data_processor or email_drafter subagent. Do not handle these tasks yourself. "
            "For Gmail operations (reading emails, sending emails, searching, etc.) use the gmail_tools MCP server tools directly. "
            "NOTE: In this CLI mode there is no logged-in user_id — ask the user to provide their user_id or use the API endpoint instead."
        ),
        
        # 5. Auto-approve tools so subagent can run MCP tools without interactive permission prompt
        permission_mode="bypassPermissions",
        
        # 6. Restrict agent to this folder only (not your whole directory)
        cwd=str(_agent_cwd),
        
        # 7. Pass env to CLI subprocess (for Bedrock: set USE_BEDROCK=1 in .env)
        env=_build_cli_env(),
    )

    # Map model names to agent names (CLI may not send agent name in Task input)
    model_to_agent = {}
    if options.agents:
        for name, agent_def in options.agents.items():
            model = getattr(agent_def, "model", None)
            if model:
                model_to_agent[model.lower()] = name

    def process_message(message):
        """Print a message from the agent."""
        if isinstance(message, AssistantMessage):
            agent_label = "Main agent"
            for model_key, agent_name in model_to_agent.items():
                if model_key in message.model.lower():
                    agent_label = agent_name
                    break
            print(f"\n[Agent: {agent_label}]")
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    if block.name == "Task":
                        agent_name = (
                            block.input.get("subagent_type")
                            or block.input.get("agent")
                            or "subagent (see next [Agent] line)"
                        )
                        task = str(block.input.get("prompt") or block.input.get("task") or block.input.get("description", ""))
                        task_preview = task[:80] + "..." if len(task) > 80 else task
                        print(f"  → [Subagent] Delegating to '{agent_name}': {task_preview}")
                    else:
                        print(f"  → [Tool] {block.name} {block.input}")
                elif isinstance(block, TextBlock):
                    if agent_label != "Main agent":
                        print(f"  --- {agent_label} response ---")
                    print(block.text)
        elif isinstance(message, SystemMessage):
            print(f"[System] {message.subtype}")
        elif isinstance(message, ResultMessage):
            print(f"\n[Result] turns={message.num_turns} duration={message.duration_ms}ms")

    async def read_input(prompt: str) -> str:
        """Read user input without blocking the event loop."""
        return await asyncio.get_running_loop().run_in_executor(None, lambda: input(prompt))

    # Interactive chatbot loop
    print("Chatbot ready. Type your message (or 'quit'/'exit' to end).\n")
    async with ClaudeSDKClient(options=options) as client:
        while True:
            user_input = await read_input("You: ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            print("\nAgent is thinking...")
            await client.query(user_input)
            async for message in client.receive_response():
                process_message(message)
            print()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())