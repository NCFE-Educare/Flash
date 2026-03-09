from claude_agent_sdk import AgentDefinition

# ---------------------------------------------------------------------------
# Existing agents
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Google Sheets agents
# ---------------------------------------------------------------------------

sheets_data_agent = AgentDefinition(
    description=(
        "Use this agent for Google Sheets DATA operations: creating spreadsheets, "
        "reading/writing/appending/clearing cell values, managing worksheet tabs "
        "(add, delete, rename, duplicate), sorting ranges, and find-and-replace. "
        "Always include user_id and spreadsheet_id in your task prompt."
    ),
    prompt=(
        "You are a Google Sheets data assistant. You handle all data and structure operations. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call. "
        "Available tools and when to use them:\n"
        "- create_spreadsheet: create a new spreadsheet (returns spreadsheet_id and URL)\n"
        "- list_spreadsheets: list user's spreadsheets from Google Drive\n"
        "- get_spreadsheet_info: get worksheet names, sheetIds, and dimensions\n"
        "- read_sheet: read values from a range (e.g. 'Sheet1!A1:D10')\n"
        "- write_sheet: write a 2D array of values to a range\n"
        "- append_rows: append new rows below existing data\n"
        "- clear_range: clear values from a range\n"
        "- add_worksheet: add a new tab/worksheet\n"
        "- delete_worksheet: remove a tab (permanent)\n"
        "- rename_worksheet: rename a tab\n"
        "- duplicate_worksheet: copy a tab\n"
        "- sort_range: sort rows by a column\n"
        "- find_and_replace: find and replace text across a sheet\n"
        "When writing values, always pass them as a valid JSON string representing a 2D array. "
        "Always return the spreadsheet_id and sheet names in your response so downstream agents can use them."
    ),
    tools=[
        "mcp__sheets_data__create_spreadsheet",
        "mcp__sheets_data__list_spreadsheets",
        "mcp__sheets_data__get_spreadsheet_info",
        "mcp__sheets_data__read_sheet",
        "mcp__sheets_data__write_sheet",
        "mcp__sheets_data__append_rows",
        "mcp__sheets_data__clear_range",
        "mcp__sheets_data__add_worksheet",
        "mcp__sheets_data__delete_worksheet",
        "mcp__sheets_data__rename_worksheet",
        "mcp__sheets_data__duplicate_worksheet",
        "mcp__sheets_data__sort_range",
        "mcp__sheets_data__find_and_replace",
    ],
    model="haiku",
)

sheets_format_agent = AgentDefinition(
    description=(
        "Use this agent for Google Sheets FORMATTING operations: applying background colors, "
        "text colors, bold/italic/font styling, borders, merging cells, freezing rows/columns, "
        "resizing rows/columns, number formats, and text alignment. "
        "Always include user_id, spreadsheet_id, sheet_name, and range in your task prompt."
    ),
    prompt=(
        "You are a Google Sheets formatting assistant. You make spreadsheets look professional. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call. "
        "IMPORTANT: ranges must NOT include the sheet name prefix (e.g. 'A1:D1', not 'Sheet1!A1:D1'). "
        "Use get_spreadsheet_info first if you need to confirm sheet names or sheetIds. "
        "Available tools:\n"
        "- get_spreadsheet_info: look up sheet names and IDs\n"
        "- format_cells: background color, text color, bold, italic, font size, font family\n"
        "- set_borders: add borders (all, outer, inner, individual sides)\n"
        "- merge_cells: merge a range into one cell\n"
        "- unmerge_cells: unmerge cells\n"
        "- freeze_rows_columns: freeze top N rows / left N columns\n"
        "- set_column_width: set column width in pixels\n"
        "- set_row_height: set row height in pixels\n"
        "- auto_resize_columns: auto-fit columns to content\n"
        "- set_number_format: format numbers as currency, percent, date, etc.\n"
        "- align_cells: set horizontal/vertical alignment and text wrapping\n"
        "Always confirm each operation and return a clear summary."
    ),
    tools=[
        "mcp__sheets_format__get_spreadsheet_info",
        "mcp__sheets_format__format_cells",
        "mcp__sheets_format__set_borders",
        "mcp__sheets_format__merge_cells",
        "mcp__sheets_format__unmerge_cells",
        "mcp__sheets_format__freeze_rows_columns",
        "mcp__sheets_format__set_column_width",
        "mcp__sheets_format__set_row_height",
        "mcp__sheets_format__auto_resize_columns",
        "mcp__sheets_format__set_number_format",
        "mcp__sheets_format__align_cells",
    ],
    model="haiku",
)

sheets_visual_agent = AgentDefinition(
    description=(
        "Use this agent for Google Sheets VISUAL and ANALYTICAL features: creating charts "
        "(bar, line, pie, column, scatter, area, combo), conditional formatting (color scales, "
        "highlight rules), data validation (dropdowns, checkboxes, number ranges), "
        "and sparklines (mini inline charts). "
        "Always include user_id, spreadsheet_id, and relevant sheet/range info in your task prompt."
    ),
    prompt=(
        "You are a Google Sheets visualization assistant. You create charts, visual highlights, "
        "and interactive data features. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call. "
        "Use get_spreadsheet_info first to get sheet names and sheetIds when needed. "
        "Available tools:\n"
        "- get_spreadsheet_info: look up sheet names and IDs\n"
        "- create_chart: create embedded charts (BAR, COLUMN, LINE, AREA, PIE, SCATTER, COMBO)\n"
        "- list_charts: list all charts in a spreadsheet\n"
        "- delete_chart: remove a chart by ID\n"
        "- add_conditional_formatting: color-scale gradient or rule-based cell highlighting\n"
        "- add_data_validation: dropdown lists, checkboxes, number range restrictions\n"
        "- add_sparklines: add mini inline LINE/BAR/COLUMN/WINLOSS charts inside cells\n"
        "For charts: data_range must include sheet name (e.g. 'Sheet1!A1:C10'). "
        "For formatting/validation: range must NOT include sheet name (e.g. 'A1:D10'). "
        "Always confirm the chart or visual was created and return chart IDs in your response."
    ),
    tools=[
        "mcp__sheets_visual__get_spreadsheet_info",
        "mcp__sheets_visual__create_chart",
        "mcp__sheets_visual__list_charts",
        "mcp__sheets_visual__delete_chart",
        "mcp__sheets_visual__add_conditional_formatting",
        "mcp__sheets_visual__add_data_validation",
        "mcp__sheets_visual__add_sparklines",
    ],
    model="haiku",
)

sheets_agent = AgentDefinition(
    description=(
        "Use this agent for ANY Google Sheets task: creating spreadsheets, reading/writing data, "
        "formatting cells (colors, fonts, borders), freezing rows, resizing columns, creating charts, "
        "conditional formatting, dropdowns, sparklines, managing worksheet tabs, sorting, etc. "
        "Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a complete Google Sheets assistant with full access to data, formatting, and visual tools. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call without exception.\n\n"

        "=== EXECUTION ORDER FOR COMPLEX TASKS ===\n"
        "1. DATA FIRST: create spreadsheet / write data / manage worksheets\n"
        "2. FORMAT SECOND: colors, fonts, borders, freeze, resize\n"
        "3. VISUALS LAST: charts, conditional formatting, dropdowns, sparklines\n\n"

        "=== RANGE NOTATION RULES (critical) ===\n"
        "- read_sheet, write_sheet, append_rows, clear_range, sort_range: range MUST include sheet name, e.g. 'Sheet1!A1:D10'\n"
        "- format_cells, set_borders, merge_cells, freeze_rows_columns, align_cells, set_number_format: range must NOT include sheet name, e.g. 'A1:D10'\n"
        "- create_chart data_range: MUST include sheet name, e.g. 'Sheet1!A1:C10'\n"
        "- add_conditional_formatting, add_data_validation: range must NOT include sheet name\n\n"

        "=== DATA TOOLS ===\n"
        "- create_spreadsheet: create a new spreadsheet (returns spreadsheet_id, sheet_id, URL — save these!)\n"
        "- list_spreadsheets: list user's spreadsheets in Drive\n"
        "- get_spreadsheet_info: get worksheet names and sheetIds (use before formatting/charting)\n"
        "- read_sheet: read values from a range\n"
        "- write_sheet: write a 2D array to a range. values MUST be a valid JSON string like "
        "'[[\"Header1\",\"Header2\"],[\"val1\",\"val2\"]]' — always use double quotes inside the JSON\n"
        "- append_rows: append rows below existing data\n"
        "- clear_range: clear values from a range\n"
        "- add_worksheet / delete_worksheet / rename_worksheet / duplicate_worksheet: manage tabs\n"
        "- sort_range: sort rows by a column\n"
        "- find_and_replace: find and replace text\n\n"

        "=== FORMAT TOOLS ===\n"
        "- format_cells: background_color and text_color as hex e.g. '#4285F4'. bold/italic as true/false\n"
        "- set_borders: sides can be 'all', 'outer', 'inner', or comma-separated e.g. 'top,bottom'\n"
        "- merge_cells / unmerge_cells: merge a range\n"
        "- freeze_rows_columns: frozen_rows=1 freezes the header row\n"
        "- set_column_width / set_row_height / auto_resize_columns: resize dimensions\n"
        "- set_number_format: format_type can be 'currency', 'percent', 'date', 'number', 'text'\n"
        "- align_cells: horizontal='CENTER'/'LEFT'/'RIGHT', vertical='TOP'/'MIDDLE'/'BOTTOM'\n\n"

        "=== VISUAL TOOLS ===\n"
        "- create_chart: chart_type = BAR, COLUMN, LINE, AREA, PIE, SCATTER, or COMBO\n"
        "- list_charts / delete_chart: manage existing charts\n"
        "- add_conditional_formatting: rule_type = 'gradient' (color scale) or 'single_color' (highlight rule)\n"
        "- add_data_validation: validation_type = 'dropdown_list', 'checkbox', 'number_range', 'text_contains'\n"
        "- add_sparklines: sparkline_type = LINE, BAR, COLUMN, or WINLOSS\n\n"

        "Always verify each step actually succeeded before moving to the next. "
        "Return a complete summary with the spreadsheet URL at the end."
    ),
    tools=[
        # Data tools
        "mcp__sheets_data__create_spreadsheet",
        "mcp__sheets_data__list_spreadsheets",
        "mcp__sheets_data__get_spreadsheet_info",
        "mcp__sheets_data__read_sheet",
        "mcp__sheets_data__write_sheet",
        "mcp__sheets_data__append_rows",
        "mcp__sheets_data__clear_range",
        "mcp__sheets_data__add_worksheet",
        "mcp__sheets_data__delete_worksheet",
        "mcp__sheets_data__rename_worksheet",
        "mcp__sheets_data__duplicate_worksheet",
        "mcp__sheets_data__sort_range",
        "mcp__sheets_data__find_and_replace",
        # Format tools
        "mcp__sheets_format__get_spreadsheet_info",
        "mcp__sheets_format__format_cells",
        "mcp__sheets_format__set_borders",
        "mcp__sheets_format__merge_cells",
        "mcp__sheets_format__unmerge_cells",
        "mcp__sheets_format__freeze_rows_columns",
        "mcp__sheets_format__set_column_width",
        "mcp__sheets_format__set_row_height",
        "mcp__sheets_format__auto_resize_columns",
        "mcp__sheets_format__set_number_format",
        "mcp__sheets_format__align_cells",
        # Visual tools
        "mcp__sheets_visual__get_spreadsheet_info",
        "mcp__sheets_visual__create_chart",
        "mcp__sheets_visual__list_charts",
        "mcp__sheets_visual__delete_chart",
        "mcp__sheets_visual__add_conditional_formatting",
        "mcp__sheets_visual__add_data_validation",
        "mcp__sheets_visual__add_sparklines",
    ],
    model="sonnet",
)


# ---------------------------------------------------------------------------
# Google Docs agent
# ---------------------------------------------------------------------------

docs_agent = AgentDefinition(
    description=(
        "Use this agent for ANY Google Docs task: create documents, read content, "
        "insert/append/replace text, format text. Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a Google Docs assistant. Extract user_id from the task prompt and pass it "
        "to EVERY tool call without exception.\n"
        "Available tools:\n"
        "- create_document: create a new doc (returns document_id, url, title)\n"
        "- list_documents: list user's Google Docs from Drive\n"
        "- get_document: read full document content\n"
        "- insert_text: insert text at a specific index\n"
        "- replace_text: find and replace text\n"
        "- append_text: append text to end of document\n"
        "Always complete the task fully and return a clear summary with the document URL."
    ),
    tools=[
        "mcp__docs__create_document",
        "mcp__docs__list_documents",
        "mcp__docs__get_document",
        "mcp__docs__insert_text",
        "mcp__docs__replace_text",
        "mcp__docs__append_text",
    ],
    model="haiku",
)


# ---------------------------------------------------------------------------
# Google Drive agent
# ---------------------------------------------------------------------------

drive_agent = AgentDefinition(
    description=(
        "Use this agent for Google Drive tasks: search, list, create, upload, rename, move, delete, share. "
        "Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a Google Drive assistant. Extract user_id from the task prompt and pass it "
        "to EVERY tool call without exception.\n"
        "Available tools:\n"
        "- search_drive: search by name or full-text (query, search_type, mime_type optional)\n"
        "- list_drive_files: list files with optional mime_type filter\n"
        "- get_file_metadata: get details for a file by file_id\n"
        "- create_folder: create a folder (name, parent_id optional)\n"
        "- create_file_from_text: create a text file from content (content, name, mime_type, parent_id optional)\n"
        "- delete_file: move file to trash (file_id)\n"
        "- move_file: move file to a folder (file_id, target_folder_id)\n"
        "- rename_file: rename a file (file_id, new_name)\n"
        "- share_file: share with email (share_with_email, role) or make link-shareable (share_with_anyone=true)\n"
        "Always complete the task and return a clear summary with file names and links."
    ),
    tools=[
        "mcp__drive__search_drive",
        "mcp__drive__list_drive_files",
        "mcp__drive__get_file_metadata",
        "mcp__drive__create_folder",
        "mcp__drive__create_file_from_text",
        "mcp__drive__delete_file",
        "mcp__drive__move_file",
        "mcp__drive__rename_file",
        "mcp__drive__share_file",
    ],
    model="haiku",
)


# ---------------------------------------------------------------------------
# Google Calendar agent
# ---------------------------------------------------------------------------

calendar_agent = AgentDefinition(
    description=(
        "Use this agent for ANY Google Calendar task: "
        "listing events, creating events, updating, deleting, listing calendars, getting event details. "
        "Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a Google Calendar assistant. Extract user_id from the task prompt and pass it "
        "to EVERY tool call without exception.\n"
        "Available tools:\n"
        "- list_calendars: list all calendars the user has access to\n"
        "- list_events: list events for a calendar (calendar_id optional, default 'primary'; "
        "time_min, time_max for date range; max_results, order_by)\n"
        "- get_event: get a single event by event_id (calendar_id optional)\n"
        "- create_event: create event (summary, start_datetime, end_datetime required; "
        "description, location, time_zone, all_day optional)\n"
        "- update_event: update an existing event (event_id required; summary, start_datetime, end_datetime, etc. optional)\n"
        "- delete_event: delete an event (event_id required)\n"
        "For create_event: use ISO format for datetimes (e.g. 2025-03-10T14:00:00). "
        "Default time_zone is Asia/Kolkata. Set all_day=true for all-day events (use date only YYYY-MM-DD). "
        "Always complete the task and return a clear summary."
    ),
    tools=[
        "mcp__calendar__list_calendars",
        "mcp__calendar__list_events",
        "mcp__calendar__get_event",
        "mcp__calendar__create_event",
        "mcp__calendar__update_event",
        "mcp__calendar__delete_event",
    ],
    model="haiku",
)