from claude_agent_sdk import tool, create_sdk_mcp_server
from typing import Any

# Define the custom tools
@tool("read_mock_data", "Reads mock data from a specific file", {"file_name": str})
async def read_mock_data(args: dict[str, Any]) -> dict[str, Any]:
    # In a real app, this would read an actual file or database
    mock_data = f"Here is some secret mock data found inside {args['file_name']}."
    return {"content": [{"type": "text", "text": mock_data}]}


@tool(
    "draft_email",
    "Drafts a professional email with subject and body. Use for creating emails based on recipient, purpose, and tone.",
    {"recipient": str, "purpose": str, "tone": str},
)
async def draft_email(args: dict[str, Any]) -> dict[str, Any]:
    """Draft an email - returns a structured draft for the subagent to refine or present."""
    recipient = args.get("recipient", "Recipient")
    purpose = args.get("purpose", "General inquiry")
    tone = args.get("tone", "professional")
    # Generate a template draft - subagent can refine before presenting to user
    draft = (
        f"**Email Draft**\n\n"
        f"To: {recipient}\n"
        f"Subject: Re: {purpose}\n\n"
        f"Dear {recipient.split()[0] if recipient else 'Recipient'},\n\n"
        f"I am writing to {purpose.lower()}.\n\n"
        f"Please let me know if you have any questions.\n\n"
        f"Best regards"
    )
    return {"content": [{"type": "text", "text": draft}]}


# Package the tools into an MCP server so Claude can use them
my_tools_server = create_sdk_mcp_server(
    name="my_tools",
    version="1.0.0",
    tools=[read_mock_data, draft_email],
)