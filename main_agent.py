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
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    SystemMessage,
    ResultMessage,
)

from agent_config import make_agent_options


async def main():
    _agent_cwd = Path(__file__).parent / "cwd"
    _agent_cwd.mkdir(exist_ok=True)

    # No user_id in CLI mode — agent will ask the user if needed
    options = make_agent_options(agent_cwd=_agent_cwd)

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
                    elif block.name == "WebSearch":
                        query = block.input.get("query", "")
                        print(f"  → [WebSearch] Searching: {query}")
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
