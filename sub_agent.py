from claude_agent_sdk import AgentDefinition

# Define the specialized subagent for data processing
data_processor_agent = AgentDefinition(
    description="Use this agent when you need to read and summarize mock data files.",
    prompt="You are a data processing assistant. Use your tools to read the file and summarize its contents.",
    tools=["mcp__my_tools__read_mock_data"],
    model="haiku",
)

# Define the specialized subagent for drafting emails
email_drafter_agent = AgentDefinition(
    description="Use this agent when you need to draft professional emails, including subject lines and body content.",
    prompt="You are an email drafting assistant. Use your tools to create well-structured, professional emails. Match the tone to the context (formal, casual, etc.) and include a clear subject line.",
    tools=["mcp__my_tools__draft_email"],
    model="haiku",
)