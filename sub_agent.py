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

# Define the specialized subagent for all Gmail operations
gmail_agent = AgentDefinition(
    description=(
        "Use this agent for ANY Gmail or email-related task: "
        "reading emails, checking inbox, searching emails, sending emails, "
        "replying to emails, creating drafts, marking as read/unread, "
        "moving to trash, archiving, managing labels, or getting Gmail profile info. "
        "Always include the user_id in your task prompt."
    ),
    prompt=(
        "You are a Gmail assistant. You have full access to the user's Gmail account. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY "
        "Gmail tool call without exception. "
        "Available tools and when to use them:\n"
        "- list_emails: check inbox, show recent or unread emails\n"
        "- get_email: read the full body of a specific email (needs email_id)\n"
        "- search_emails: find emails by sender, subject, date, attachment etc.\n"
        "- send_email: compose and immediately send an email\n"
        "- create_draft: save an email as draft without sending\n"
        "- reply_to_email: reply in an existing thread (needs email_id and thread_id)\n"
        "- mark_as_read: mark emails read or unread\n"
        "- move_to_trash: delete/trash emails\n"
        "- archive_email: archive emails (removes from inbox, keeps in All Mail)\n"
        "- add_label: add a label to an email\n"
        "- list_labels: list all available Gmail labels\n"
        "- get_gmail_profile: get the connected Gmail address and mailbox info\n"
        "Always complete the task fully and return a clear, friendly summary to the user."
    ),
    tools=[
        "mcp__gmail_tools__list_emails",
        "mcp__gmail_tools__get_email",
        "mcp__gmail_tools__search_emails",
        "mcp__gmail_tools__send_email",
        "mcp__gmail_tools__create_draft",
        "mcp__gmail_tools__reply_to_email",
        "mcp__gmail_tools__mark_as_read",
        "mcp__gmail_tools__move_to_trash",
        "mcp__gmail_tools__archive_email",
        "mcp__gmail_tools__add_label",
        "mcp__gmail_tools__list_labels",
        "mcp__gmail_tools__get_gmail_profile",
    ],
    model="haiku",
)