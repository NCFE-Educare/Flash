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
        "You MUST delegate to sheets_data_agent, sheets_format_agent, or sheets_visual_agent — never use tools directly. "
        "Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a Google Sheets orchestrator. You do NOT have direct tools. "
        "You MUST use the Task tool to delegate to your sub-agents:\n\n"

        "- **sheets_data_agent**: For creating spreadsheets, listing, reading/writing/clearing cells, "
        "managing worksheets (add, delete, rename, duplicate), sorting, find-and-replace. "
        "Include user_id and spreadsheet_id (when known) in the task prompt.\n\n"

        "- **sheets_format_agent**: For formatting: colors, fonts, borders, merge, freeze, resize, "
        "number format, alignment. Ranges must NOT include sheet name (e.g. 'A1:D10'). "
        "Include user_id, spreadsheet_id, sheet_name, and range in the task prompt.\n\n"

        "- **sheets_visual_agent**: For charts, conditional formatting, data validation (dropdowns), "
        "sparklines. Chart data_range MUST include sheet name (e.g. 'Sheet1!A1:C10'). "
        "Include user_id, spreadsheet_id, and relevant sheet/range info in the task prompt.\n\n"

        "=== EXECUTION ORDER FOR COMPLEX TASKS ===\n"
        "1. Delegate to sheets_data_agent FIRST: create spreadsheet / write data / manage worksheets\n"
        "2. Delegate to sheets_format_agent SECOND: colors, fonts, borders, freeze, resize\n"
        "3. Delegate to sheets_visual_agent LAST: charts, conditional formatting, dropdowns, sparklines\n\n"

        "For multi-step tasks, delegate in order. Pass spreadsheet_id and sheet names from earlier results "
        "to the next delegation. Always include user_id in every delegation. "
        "Return a complete summary with the spreadsheet URL at the end."
    ),
    tools=["Task"],
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


# ---------------------------------------------------------------------------
# Google Slides agents
# ---------------------------------------------------------------------------

slides_data_agent = AgentDefinition(
    description=(
        "Use this agent for Google Slides DATA operations: creating presentations, "
        "listing presentations, getting presentation structure, adding slides, "
        "inserting text, replacing text, creating text boxes. "
        "Always include user_id and presentation_id in your task prompt."
    ),
    prompt=(
        "You are a Google Slides data assistant. You handle all content and structure operations. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call. "
        "Available tools:\n"
        "- create_presentation: create a new presentation (returns presentation_id, url, title)\n"
        "- list_presentations: list user's presentations from Drive\n"
        "- get_presentation: get full presentation structure (slides, shapes, objectIds)\n"
        "- get_presentation_info: get simplified slide IDs and shape objectIds for formatting\n"
        "- add_slide: add a new slide (returns slide_object_id)\n"
        "- insert_text: insert text into a shape at index (need object_id from get_presentation)\n"
        "- replace_all_text: replace text across the presentation\n"
        "- create_text_box: create a text box on a slide (need slide_object_id)\n"
        "Use get_presentation or get_presentation_info to find objectIds before inserting text or formatting. "
        "Always return the presentation_id and URL in your response."
    ),
    tools=[
        "mcp__slides_data__create_presentation",
        "mcp__slides_data__list_presentations",
        "mcp__slides_data__get_presentation",
        "mcp__slides_data__get_presentation_info",
        "mcp__slides_data__add_slide",
        "mcp__slides_data__insert_text",
        "mcp__slides_data__replace_all_text",
        "mcp__slides_data__create_text_box",
    ],
    model="haiku",
)

slides_format_agent = AgentDefinition(
    description=(
        "Use this agent for Google Slides FORMATTING operations: text style (bold, italic, "
        "font, size, color), paragraph bullets, paragraph alignment. "
        "Always include user_id, presentation_id, and object_id in your task prompt."
    ),
    prompt=(
        "You are a Google Slides formatting assistant. You make presentations look professional. "
        "The task prompt will always include the user_id — extract it and pass it to EVERY tool call. "
        "Use get_presentation_info first to find shape objectIds before formatting. "
        "Available tools:\n"
        "- get_presentation_info: get slide IDs and shape objectIds\n"
        "- update_text_style: bold, italic, font_family, font_size_pt, foreground_color_hex, link_url. "
        "text_range_type: 'ALL' for entire shape, or 'FIXED_RANGE' with start_index/end_index\n"
        "- create_paragraph_bullets: add bullets (bullet_preset: BULLET_DISC_CIRCLE_SQUARE, etc.)\n"
        "- delete_paragraph_bullets: remove bullets\n"
        "- update_paragraph_style: alignment (START, CENTER, END, JUSTIFIED)\n"
        "Colors: use hex format e.g. '#4285F4'. Always confirm each operation succeeded."
    ),
    tools=[
        "mcp__slides_format__get_presentation_info",
        "mcp__slides_format__update_text_style",
        "mcp__slides_format__create_paragraph_bullets",
        "mcp__slides_format__delete_paragraph_bullets",
        "mcp__slides_format__update_paragraph_style",
    ],
    model="haiku",
)

slides_agent = AgentDefinition(
    description=(
        "Use this agent for ANY Google Slides task: creating presentations, adding slides, "
        "inserting text, formatting (bold, fonts, colors, bullets, alignment), creating text boxes. "
        "You MUST delegate to slides_data_agent or slides_format_agent — never use tools directly. "
        "Always include user_id in your task prompt."
    ),
    prompt=(
        "You are a Google Slides orchestrator. You do NOT have direct tools. "
        "You MUST use the Task tool to delegate to your sub-agents:\n\n"

        "- **slides_data_agent**: For creating presentations, listing, adding slides, inserting text, "
        "replace_all_text, creating text boxes, getting presentation structure. "
        "Include user_id and presentation_id (when known) in the task prompt.\n\n"

        "- **slides_format_agent**: For text formatting (bold, italic, font, size, color), "
        "paragraph bullets, paragraph alignment. Include user_id, presentation_id, and object_id in the task prompt.\n\n"

        "=== EXECUTION ORDER FOR COMPLEX TASKS ===\n"
        "1. Delegate to slides_data_agent FIRST: create presentation / add slides / insert text / create text boxes\n"
        "2. Delegate to slides_format_agent SECOND: apply text style, bullets, alignment\n\n"

        "For multi-step tasks (e.g. create presentation and format it), delegate to slides_data_agent first, "
        "then delegate to slides_format_agent with the presentation_id and objectIds from the first result.\n\n"

        "Always include user_id in every delegation. Return a clear summary with the presentation URL at the end."
    ),
    tools=["Task"],
    model="haiku",
)