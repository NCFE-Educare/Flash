# Google Sheets Integration — EduCare Bots

Complete guide to the Google Sheets AI agent — what it can do, how to set it up, and example prompts.

---

## Setup (One-Time)

### 1. Add the Sheets redirect URI to Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services** → **Credentials**
2. Open your existing OAuth 2.0 Client ID (the one used for Gmail)
3. Under **Authorized redirect URIs**, add:
   ```
   http://localhost:8000/auth/sheets/callback
   ```
4. Click **Save**

### 2. Enable the Google Sheets API and Google Drive API

1. Go to **APIs & Services** → **Library**
2. Search for **Google Sheets API** → Enable
3. Search for **Google Drive API** → Enable

> Both are free to use. No billing required.

### 3. Add the redirect URI to your `.env`

Your `.env` already has `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — those are reused.
Optionally add (defaults to the value below if omitted):

```env
GOOGLE_SHEETS_REDIRECT_URI=http://localhost:8000/auth/sheets/callback
```

### 4. Connect your Google account

Call the connect endpoint (requires a valid JWT from login):

```
GET /auth/sheets/connect
Authorization: Bearer <your_jwt>
```

This returns an `auth_url`. Open it in your browser, sign in with Google, and grant Sheets + Drive access.
Google will redirect to `/auth/sheets/callback` automatically — you'll see a success page.

---

## OAuth Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/auth/sheets/connect` | GET | Get the Google OAuth URL to grant Sheets access |
| `/auth/sheets/callback` | GET | Google redirects here after approval (no JWT needed) |
| `/auth/sheets/status` | GET | Check if Sheets is connected + which account |
| `/auth/sheets/disconnect` | DELETE | Remove Sheets access |

> **Note:** Sheets uses a **separate OAuth flow from Gmail** because it requires different permissions (Sheets + Drive scopes). You need to connect both separately, but they can use the same Google account.

---

## Agent Architecture

```
Main Agent
└── sheets_agent (orchestrator — uses Task to delegate)
    ├── sheets_data_agent      → data CRUD + worksheet management
    ├── sheets_format_agent    → colors, fonts, borders, merge, freeze, alignment
    └── sheets_visual_agent    → charts, conditional formatting, sparklines, dropdowns
```

**You only talk to the main agent.** It routes everything to `sheets_agent`, which then delegates to the right specialist. You never need to specify which sub-agent to use.

---

## What You Can Ask the AI

### Creating Spreadsheets

```
Create a new spreadsheet called "Student Grades"
```
```
Create a spreadsheet called "Monthly Budget" with a worksheet called "January"
```
```
Show me all my spreadsheets in Google Drive
```

---

### Reading & Writing Data

```
Read the data from Sheet1!A1:E20 in spreadsheet ID abc123
```
```
Write these headers to Sheet1!A1: Name, Age, Score, Grade
```
```
Fill in 10 rows of sample student data starting at row 2
```
```
Append these 3 new rows to the bottom of my grades sheet: [["Alice", 92], ["Bob", 78], ["Carol", 85]]
```
```
Clear all data from Sheet1!A2:F100
```
```
Find all occurrences of "Pass" and replace with "Passed" in my grades sheet
```
```
Sort the data in Sheet1!A2:D50 by column 3 (Score) in descending order
```

---

### Formatting

```
Make row 1 in Sheet1 have a dark blue background with white bold text
```
```
Set the font size to 12 and font family to Arial for the entire Sheet1
```
```
Add thick outer borders around the range A1:E11
```
```
Add all borders (inner and outer) to A1:D20 in black
```
```
Merge cells A1:D1 into a single header cell
```
```
Freeze the first row so it stays visible while scrolling
```
```
Freeze the first row and first column
```
```
Auto-resize all columns to fit their content
```
```
Set column A to 200px wide and column B to 150px wide
```
```
Format column C as currency ($)
```
```
Format column D as a percentage
```
```
Format column E as a date (MM/DD/YYYY)
```
```
Center-align all headers in row 1
```
```
Right-align the numbers in columns C, D, and E
```
```
Enable text wrapping for column B
```

---

### Charts

```
Create a bar chart from Sheet1!A1:B11 titled "Student Scores"
```
```
Create a pie chart of the grade distribution using Sheet1!C1:D6
```
```
Create a line chart showing score trends from Sheet1!A1:C20
```
```
Create a column chart and place it at row 0, column 8
```
```
List all charts in my spreadsheet
```
```
Delete the chart with ID 12345
```

**Supported chart types:** `BAR`, `COLUMN`, `LINE`, `AREA`, `PIE`, `SCATTER`, `COMBO`

---

### Conditional Formatting

```
Add a green-yellow-red color scale to the scores in C2:C50
(green = high scores, red = low scores)
```
```
Highlight all cells in C2:C50 that are less than 60 in red
```
```
Highlight cells in D2:D50 that contain the text "Fail" in orange
```
```
Color cells in B2:B100 green if they are greater than or equal to 90
```

**Supported conditions:** `GREATER_THAN`, `LESS_THAN`, `EQUAL_TO`, `NOT_EQUAL_TO`,
`GREATER_THAN_OR_EQUAL`, `LESS_THAN_OR_EQUAL`, `TEXT_CONTAINS`, `TEXT_NOT_CONTAINS`,
`IS_EMPTY`, `IS_NOT_EMPTY`

---

### Data Validation

```
Add a dropdown to column E with options: Pass, Fail, Incomplete
```
```
Add checkboxes to column F rows 2–50
```
```
Restrict column C rows 2–50 to numbers between 0 and 100
```
```
Add a dropdown list with options: Math, Science, English, History to cells D2:D100
```

---

### Sparklines (Mini Charts)

```
Add line sparklines in column G for each student's scores in columns B-F
```
```
Add bar sparklines in column H showing quiz performance trends
```
```
Add win/loss sparklines in column I to show pass/fail trends
```

**Supported sparkline types:** `LINE`, `BAR`, `COLUMN`, `WINLOSS`

---

### Worksheet Tab Management

```
Add a new worksheet tab called "Summary"
```
```
Delete the worksheet called "Old Data"
```
```
Rename the worksheet "Sheet1" to "Grades"
```
```
Make a copy of the "Grades" tab called "Grades Backup"
```
```
Get information about all worksheets in this spreadsheet
```

---

## Complex Prompts (Multi-Step)

The AI handles multi-step tasks automatically. Just describe what you want:

```
Create a spreadsheet called "Class Report":
- Add headers: Student Name, Score, Grade, Status
- Fill in 8 sample rows
- Make the header row dark green with white bold text
- Add borders around all data
- Format the Score column as a number
- Add a bar chart of the scores
- Color scores below 60 red and above 90 green
```

```
Create a monthly budget spreadsheet with:
- Headers: Category, Budget, Actual, Difference
- 6 budget categories with sample amounts
- Freeze the header row
- Format Budget and Actual columns as currency
- Add conditional formatting: red if Difference is negative, green if positive
- Auto-resize all columns
- Create a column chart comparing Budget vs Actual
```

```
Build a student tracking sheet with:
- 3 worksheets: "Math", "Science", "English"
- 15 rows of student data in each
- Blue headers on all three sheets
- A dropdown in the Grade column with values: A, B, C, D, F
- A line chart per sheet showing score trends
- Freeze the header row on each sheet
```

---

## API Reference — All MCP Tools

### Data Tools (`sheets_data_agent`)

| Tool | Parameters |
|---|---|
| `create_spreadsheet` | `user_id`, `title`, `sheet_name` (opt) |
| `list_spreadsheets` | `user_id`, `max_results` (opt) |
| `get_spreadsheet_info` | `user_id`, `spreadsheet_id` |
| `read_sheet` | `user_id`, `spreadsheet_id`, `range` (e.g. `Sheet1!A1:D10`) |
| `write_sheet` | `user_id`, `spreadsheet_id`, `range`, `values` (JSON 2D array) |
| `append_rows` | `user_id`, `spreadsheet_id`, `range`, `values` (JSON 2D array) |
| `clear_range` | `user_id`, `spreadsheet_id`, `range` |
| `add_worksheet` | `user_id`, `spreadsheet_id`, `sheet_name`, `rows` (opt), `cols` (opt) |
| `delete_worksheet` | `user_id`, `spreadsheet_id`, `sheet_name` |
| `rename_worksheet` | `user_id`, `spreadsheet_id`, `old_name`, `new_name` |
| `duplicate_worksheet` | `user_id`, `spreadsheet_id`, `sheet_name`, `new_name` (opt) |
| `sort_range` | `user_id`, `spreadsheet_id`, `range`, `sort_column`, `ascending` (opt) |
| `find_and_replace` | `user_id`, `spreadsheet_id`, `find`, `replacement`, `sheet_name` (opt), `match_case` (opt), `match_entire_cell` (opt) |

### Format Tools (`sheets_format_agent`)

| Tool | Parameters |
|---|---|
| `format_cells` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `background_color` (hex), `text_color` (hex), `bold`, `italic`, `strikethrough`, `underline`, `font_size`, `font_family` |
| `set_borders` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `sides` (all/outer/inner/top/bottom/left/right/inner_horizontal/inner_vertical), `style`, `color` (hex) |
| `merge_cells` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `merge_type` (MERGE_ALL/MERGE_ROWS/MERGE_COLUMNS) |
| `unmerge_cells` | `user_id`, `spreadsheet_id`, `sheet_name`, `range` |
| `freeze_rows_columns` | `user_id`, `spreadsheet_id`, `sheet_name`, `frozen_rows`, `frozen_cols` |
| `set_column_width` | `user_id`, `spreadsheet_id`, `sheet_name`, `start_column`, `end_column` (opt), `width_pixels` |
| `set_row_height` | `user_id`, `spreadsheet_id`, `sheet_name`, `start_row`, `end_row` (opt), `height_pixels` |
| `auto_resize_columns` | `user_id`, `spreadsheet_id`, `sheet_name`, `start_column` (opt), `end_column` (opt) |
| `set_number_format` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `format_type` (number/currency/percent/date/datetime/time/text/scientific or custom pattern) |
| `align_cells` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `horizontal` (LEFT/CENTER/RIGHT), `vertical` (TOP/MIDDLE/BOTTOM), `wrap_strategy` (WRAP/CLIP/OVERFLOW_CELL) |

### Visual Tools (`sheets_visual_agent`)

| Tool | Parameters |
|---|---|
| `create_chart` | `user_id`, `spreadsheet_id`, `chart_type` (BAR/COLUMN/LINE/AREA/PIE/SCATTER/COMBO), `data_range` (e.g. `Sheet1!A1:C10`), `title`, `sheet_name`, `anchor_row` (opt), `anchor_col` (opt), `x_axis_title` (opt), `y_axis_title` (opt), `has_header_row` (opt), `width_pixels` (opt), `height_pixels` (opt) |
| `list_charts` | `user_id`, `spreadsheet_id` |
| `delete_chart` | `user_id`, `spreadsheet_id`, `chart_id` |
| `add_conditional_formatting` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `rule_type` (gradient/single_color), `condition`, `condition_value`, `min_color`, `mid_color`, `max_color`, `bg_color` |
| `add_data_validation` | `user_id`, `spreadsheet_id`, `sheet_name`, `range`, `validation_type` (dropdown_list/checkbox/number_range/text_contains), `values`, `min_value`, `max_value`, `pattern`, `show_warning` |
| `add_sparklines` | `user_id`, `spreadsheet_id`, `target_range` (e.g. `Sheet1!F2:F11`), `data_range` (e.g. `Sheet1!A2:E11`), `sparkline_type` (LINE/BAR/COLUMN/WINLOSS), `sparkline_color` (hex) |

---

## Range Notation Rules

| Context | Format | Example |
|---|---|---|
| Reading/writing data | Include sheet name | `Sheet1!A1:D10` |
| Formatting (range param) | NO sheet name prefix | `A1:D10` |
| Chart data range | Include sheet name | `Sheet1!A1:C10` |
| Sparkline target/data | Include sheet name | `Sheet1!F2:F11` |

---

## Troubleshooting

**"Google Sheets is not connected for this account"**
→ Call `GET /auth/sheets/connect` and complete the OAuth flow in your browser.

**"Worksheet 'Sheet1' not found"**
→ Use `get_spreadsheet_info` to check the exact worksheet name — it's case-sensitive.

**Chart columns don't match data**
→ The first column of `data_range` is always used as labels/X-axis. Put labels in column A.

**Conditional formatting not visible**
→ Make sure you're looking at the right sheet. The rule is applied to the exact range specified.

**"drive.readonly" permission denied**
→ Re-connect Sheets by visiting `/auth/sheets/connect` again — the new scopes include Drive read access.
