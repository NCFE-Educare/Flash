"""Google Sheets MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

Three MCP servers are registered at the bottom:
  sheets_data_server   → used by sheets_data_agent   (CRUD + worksheet mgmt)
  sheets_format_server → used by sheets_format_agent (colors, fonts, borders…)
  sheets_visual_server → used by sheets_visual_agent (charts, conditional fmt…)

OAuth helpers (get_sheets_auth_url / exchange_sheets_code) are plain functions
called directly by api.py — they are NOT MCP tools.
"""

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_sheets_tokens, get_sheets_tokens, save_sheets_tokens

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_SHEETS_REDIRECT_URI = os.environ.get(
    "GOOGLE_SHEETS_REDIRECT_URI", "http://localhost:8000/auth/sheets/callback"
)

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Allow oauthlib to accept a superset of the requested scopes without raising an
# error. This happens when the user has already connected Gmail — Google returns
# ALL previously granted scopes (Gmail + Sheets) together in the token response.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_pending_flows: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# OAuth helpers (called by api.py, NOT MCP tools)
# ---------------------------------------------------------------------------

def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_SHEETS_REDIRECT_URI],
        }
    }


def get_sheets_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Sheets access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SHEETS_SCOPES,
        redirect_uri=GOOGLE_SHEETS_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "sheets"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_sheets_code(code: str, state: str) -> dict | None:
    """Exchange OAuth code for tokens, save to DB. Returns {email, user_id} or None."""
    try:
        padding = 4 - len(state) % 4
        padded = state + ("=" * padding if padding != 4 else "")
        state_data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        user_id = int(state_data["user_id"])
    except Exception:
        return None

    try:
        flow = _pending_flows.pop(state, None)
        if flow is None:
            flow = Flow.from_client_config(
                _client_config(),
                scopes=SHEETS_SCOPES,
                redirect_uri=GOOGLE_SHEETS_REDIRECT_URI,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Get the user's email via Drive API
        drive_svc = build("drive", "v3", credentials=creds)
        about = drive_svc.about().get(fields="user").execute()
        google_email = about["user"]["emailAddress"]

        expiry_str = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else datetime.utcnow().isoformat()
        )

        save_sheets_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Sheets OAuth ERROR] exchange_sheets_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_sheets_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Sheets is not connected for this account. "
            "Please call GET /auth/sheets/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SHEETS_SCOPES,
    )

    try:
        expiry = datetime.fromisoformat(token_data["token_expiry"])
        if expiry.tzinfo is not None:
            expiry = expiry.replace(tzinfo=None)
        creds.expiry = expiry
    except Exception:
        pass

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed_expiry = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else token_data["token_expiry"]
        )
        save_sheets_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _sheets(user_id: int):
    """Build an authenticated Sheets v4 service."""
    return build("sheets", "v4", credentials=_get_credentials(user_id))


def _drive(user_id: int):
    """Build an authenticated Drive v3 service."""
    return build("drive", "v3", credentials=_get_credentials(user_id))


def _hex_to_color(hex_color: str) -> dict:
    """Convert hex color string (#RRGGBB or #RGB) to a Sheets API color object."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return {"red": r / 255.0, "green": g / 255.0, "blue": b / 255.0}


def _col_index(col: str) -> int:
    """Convert a column letter (A, B, … Z, AA, AB…) to a 0-based index."""
    col = col.upper()
    result = 0
    for c in col:
        result = result * 26 + (ord(c) - ord("A") + 1)
    return result - 1


def _a1_to_grid_range(sheet_id: int, range_str: str) -> dict:
    """
    Convert A1-notation range (e.g. 'A1:D5') to a Sheets API GridRange dict.
    range_str must NOT include a sheet name prefix.
    """
    range_str = range_str.strip()
    match = re.match(
        r"^([A-Za-z]+)(\d+)(?::([A-Za-z]+)(\d+))?$", range_str
    )
    if not match:
        raise ValueError(f"Cannot parse range '{range_str}' — use A1 notation, e.g. 'A1:D5'")

    sc, sr, ec, er = match.groups()
    grid: dict[str, Any] = {
        "sheetId": sheet_id,
        "startRowIndex": int(sr) - 1,
        "startColumnIndex": _col_index(sc),
    }
    if ec and er:
        grid["endRowIndex"] = int(er)
        grid["endColumnIndex"] = _col_index(ec) + 1
    else:
        grid["endRowIndex"] = int(sr)
        grid["endColumnIndex"] = _col_index(sc) + 1
    return grid


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Return the integer sheetId for the named worksheet tab."""
    ss = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in ss.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props["sheetId"])
    raise ValueError(f"Worksheet '{sheet_name}' not found in spreadsheet '{spreadsheet_id}'")


def _batch_update(service, spreadsheet_id: str, requests: list) -> dict:
    return (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
        .execute()
    )


# ---------------------------------------------------------------------------
# ── DATA TOOLS ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@tool(
    "create_spreadsheet",
    (
        "Create a new Google Spreadsheet with a given title. "
        "Optionally specify the first worksheet name (default: 'Sheet1'). "
        "Returns the spreadsheet_id and URL. "
        "user_id: required. title: required. sheet_name: optional (default 'Sheet1')."
    ),
    {"user_id": int, "title": str, "sheet_name": str},
)
async def create_spreadsheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        title = str(args["title"])
        sheet_name = str(args.get("sheet_name", "Sheet1"))

        svc = _sheets(user_id)
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": sheet_name}}],
        }
        result = svc.spreadsheets().create(body=body).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "spreadsheet_id": result["spreadsheetId"],
                    "title": result["properties"]["title"],
                    "url": result["spreadsheetUrl"],
                    "sheet_name": sheet_name,
                    "sheet_id": result["sheets"][0]["properties"]["sheetId"],
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating spreadsheet: {e}"}]}


@tool(
    "list_spreadsheets",
    (
        "List the user's Google Spreadsheets from Drive. "
        "max_results: optional int (default 20). "
        "Returns id, name, last modified, and URL for each. "
        "user_id: required."
    ),
    {"user_id": int, "max_results": int},
)
async def list_spreadsheets(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        max_results = int(args.get("max_results", 20))

        drv = _drive(user_id)
        result = drv.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            spaces="drive",
            fields="files(id,name,createdTime,modifiedTime,webViewLink)",
            pageSize=max_results,
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": "No spreadsheets found in Drive."}]}

        sheets = [
            {
                "spreadsheet_id": f["id"],
                "name": f["name"],
                "modified": f.get("modifiedTime", ""),
                "created": f.get("createdTime", ""),
                "url": f.get("webViewLink", ""),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(sheets, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing spreadsheets: {e}"}]}


@tool(
    "get_spreadsheet_info",
    (
        "Get metadata about a spreadsheet: worksheet names, their sheetIds, "
        "row/column counts, and the spreadsheet title. "
        "Use this before formatting or charting to get the sheetId for a worksheet. "
        "user_id: required. spreadsheet_id: required."
    ),
    {"user_id": int, "spreadsheet_id": str},
)
async def get_spreadsheet_info(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])

        svc = _sheets(user_id)
        ss = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        info = {
            "spreadsheet_id": ss["spreadsheetId"],
            "title": ss["properties"]["title"],
            "url": ss.get("spreadsheetUrl", ""),
            "worksheets": [
                {
                    "sheet_id": s["properties"]["sheetId"],
                    "title": s["properties"]["title"],
                    "index": s["properties"]["index"],
                    "row_count": s["properties"].get("gridProperties", {}).get("rowCount", 0),
                    "column_count": s["properties"].get("gridProperties", {}).get("columnCount", 0),
                }
                for s in ss.get("sheets", [])
            ],
        }
        return {"content": [{"type": "text", "text": json.dumps(info, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting spreadsheet info: {e}"}]}


@tool(
    "read_sheet",
    (
        "Read cell values from a spreadsheet range. "
        "range: A1 notation including sheet name, e.g. 'Sheet1!A1:D10' or 'Sheet1!A:D'. "
        "Returns a 2D array of values. "
        "user_id: required. spreadsheet_id: required. range: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str},
)
async def read_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        range_ = str(args["range"])

        svc = _sheets(user_id)
        result = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_)
            .execute()
        )
        values = result.get("values", [])
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"range": result.get("range", range_), "values": values}, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading sheet: {e}"}]}


@tool(
    "write_sheet",
    (
        "Write (overwrite) values to a spreadsheet range. "
        "range: A1 notation including sheet name, e.g. 'Sheet1!A1'. "
        "values: a JSON string representing a 2D array, e.g. "
        "'[[\"Name\",\"Score\"],[\"Alice\",95],[\"Bob\",87]]'. "
        "user_id: required. spreadsheet_id: required. range: required. values: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "values": str},
)
async def write_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        range_ = str(args["range"])

        raw = args["values"]
        if isinstance(raw, list):
            # Already parsed (SDK passed a real list)
            values = raw
        else:
            raw_str = str(raw)
            try:
                values = json.loads(raw_str)
            except (json.JSONDecodeError, ValueError):
                # Fall back to ast for Python-style lists with single quotes
                import ast
                values = ast.literal_eval(raw_str)

        svc = _sheets(user_id)
        result = (
            svc.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            )
            .execute()
        )
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Written {result.get('updatedRows', 0)} rows × "
                    f"{result.get('updatedColumns', 0)} columns to {result.get('updatedRange', range_)}."
                ),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error writing to sheet: {e}"}]}


@tool(
    "append_rows",
    (
        "Append new rows to the bottom of existing data in a sheet. "
        "range: sheet name or full A1 range (the API finds the first empty row automatically), "
        "e.g. 'Sheet1' or 'Sheet1!A:D'. "
        "values: JSON string of a 2D array, e.g. '[[\"Alice\",95],[\"Bob\",87]]'. "
        "user_id: required. spreadsheet_id: required. range: required. values: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "values": str},
)
async def append_rows(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        range_ = str(args["range"])

        raw = args["values"]
        if isinstance(raw, list):
            values = raw
        else:
            raw_str = str(raw)
            try:
                values = json.loads(raw_str)
            except (json.JSONDecodeError, ValueError):
                import ast
                values = ast.literal_eval(raw_str)

        svc = _sheets(user_id)
        result = (
            svc.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute()
        )
        updates = result.get("updates", {})
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Appended {updates.get('updatedRows', len(values))} rows to "
                    f"{updates.get('updatedRange', range_)}."
                ),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error appending rows: {e}"}]}


@tool(
    "clear_range",
    (
        "Clear all values from a range (keeps formatting). "
        "range: A1 notation including sheet name, e.g. 'Sheet1!A1:D100'. "
        "user_id: required. spreadsheet_id: required. range: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str},
)
async def clear_range(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        range_ = str(args["range"])

        svc = _sheets(user_id)
        svc.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_, body={}
        ).execute()
        return {"content": [{"type": "text", "text": f"Cleared range {range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error clearing range: {e}"}]}


@tool(
    "add_worksheet",
    (
        "Add a new worksheet tab to an existing spreadsheet. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "rows: optional int (default 1000). cols: optional int (default 26)."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "rows": int, "cols": int},
)
async def add_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        rows = int(args.get("rows", 1000))
        cols = int(args.get("cols", 26))

        svc = _sheets(user_id)
        result = _batch_update(svc, spreadsheet_id, [{
            "addSheet": {
                "properties": {
                    "title": sheet_name,
                    "gridProperties": {"rowCount": rows, "columnCount": cols},
                }
            }
        }])
        new_props = result["replies"][0]["addSheet"]["properties"]
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "sheet_name": new_props["title"],
                    "sheet_id": new_props["sheetId"],
                    "message": f"Worksheet '{sheet_name}' added successfully.",
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding worksheet: {e}"}]}


@tool(
    "delete_worksheet",
    (
        "Delete a worksheet tab from a spreadsheet. WARNING: this is permanent. "
        "user_id: required. spreadsheet_id: required. sheet_name: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str},
)
async def delete_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        _batch_update(svc, spreadsheet_id, [{"deleteSheet": {"sheetId": sheet_id}}])
        return {"content": [{"type": "text", "text": f"Worksheet '{sheet_name}' deleted."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting worksheet: {e}"}]}


@tool(
    "rename_worksheet",
    (
        "Rename a worksheet tab. "
        "user_id: required. spreadsheet_id: required. "
        "old_name: required (current tab name). new_name: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "old_name": str, "new_name": str},
)
async def rename_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        old_name = str(args["old_name"])
        new_name = str(args["new_name"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, old_name)
        _batch_update(svc, spreadsheet_id, [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "title": new_name},
                "fields": "title",
            }
        }])
        return {"content": [{"type": "text", "text": f"Worksheet renamed from '{old_name}' to '{new_name}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error renaming worksheet: {e}"}]}


@tool(
    "duplicate_worksheet",
    (
        "Duplicate an existing worksheet tab within the same spreadsheet. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "new_name: optional (defaults to 'Copy of <sheet_name>')."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "new_name": str},
)
async def duplicate_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        new_name = str(args.get("new_name", f"Copy of {sheet_name}"))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        result = _batch_update(svc, spreadsheet_id, [{
            "duplicateSheet": {
                "sourceSheetId": sheet_id,
                "newSheetName": new_name,
            }
        }])
        new_props = result["replies"][0]["duplicateSheet"]["properties"]
        return {
            "content": [{
                "type": "text",
                "text": f"Worksheet '{sheet_name}' duplicated as '{new_props['title']}' (sheetId={new_props['sheetId']}).",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error duplicating worksheet: {e}"}]}


@tool(
    "sort_range",
    (
        "Sort rows in a range by a specific column. "
        "range: A1 notation including sheet name, e.g. 'Sheet1!A2:D100' (exclude header row). "
        "sort_column: 1-based column number to sort by (e.g. 2 = column B). "
        "ascending: optional bool (default true). "
        "user_id: required. spreadsheet_id: required. range: required. sort_column: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "sort_column": int, "ascending": bool},
)
async def sort_range(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        range_ = str(args["range"])
        sort_col = int(args["sort_column"]) - 1  # convert to 0-based
        ascending = bool(args.get("ascending", True))

        svc = _sheets(user_id)

        # Parse sheet name from range
        if "!" in range_:
            sheet_name, cell_range = range_.split("!", 1)
        else:
            raise ValueError("range must include sheet name, e.g. 'Sheet1!A2:D100'")

        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, cell_range)

        _batch_update(svc, spreadsheet_id, [{
            "sortRange": {
                "range": grid_range,
                "sortSpecs": [{
                    "dimensionIndex": sort_col,
                    "sortOrder": "ASCENDING" if ascending else "DESCENDING",
                }],
            }
        }])
        return {"content": [{"type": "text", "text": f"Range '{range_}' sorted by column {sort_col + 1} ({'ascending' if ascending else 'descending'})."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error sorting range: {e}"}]}


@tool(
    "find_and_replace",
    (
        "Find text and replace it across an entire spreadsheet or a specific sheet. "
        "user_id: required. spreadsheet_id: required. find: required. replacement: required. "
        "sheet_name: optional — if omitted, searches all sheets. "
        "match_case: optional bool (default false). match_entire_cell: optional bool (default false)."
    ),
    {"user_id": int, "spreadsheet_id": str, "find": str, "replacement": str,
     "sheet_name": str, "match_case": bool, "match_entire_cell": bool},
)
async def find_and_replace(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        find = str(args["find"])
        replacement = str(args["replacement"])
        match_case = bool(args.get("match_case", False))
        match_entire_cell = bool(args.get("match_entire_cell", False))

        svc = _sheets(user_id)

        req: dict[str, Any] = {
            "find": find,
            "replacement": replacement,
            "matchCase": match_case,
            "matchEntireCell": match_entire_cell,
            "searchByRegex": False,
            "includeFormulas": False,
        }

        if "sheet_name" in args and args["sheet_name"]:
            sheet_id = _get_sheet_id(svc, spreadsheet_id, str(args["sheet_name"]))
            req["range"] = {"sheetId": sheet_id}

        result = _batch_update(svc, spreadsheet_id, [{"findReplace": req}])
        stats = result["replies"][0].get("findReplace", {})
        return {
            "content": [{
                "type": "text",
                "text": f"Replaced {stats.get('occurrencesChanged', 0)} occurrence(s) of '{find}' with '{replacement}'.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error in find/replace: {e}"}]}


# ---------------------------------------------------------------------------
# ── FORMAT TOOLS ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@tool(
    "format_cells",
    (
        "Apply visual formatting to a cell range: background color, text color, bold, italic, "
        "font size, font family, strikethrough, underline. "
        "user_id: required. spreadsheet_id: required. sheet_name: required (e.g. 'Sheet1'). "
        "range: required — A1 notation WITHOUT sheet prefix, e.g. 'A1:D1' or 'A1'. "
        "background_color: optional hex string e.g. '#4285F4'. "
        "text_color: optional hex string e.g. '#FFFFFF'. "
        "bold: optional bool. italic: optional bool. strikethrough: optional bool. "
        "underline: optional bool. font_size: optional int (points, e.g. 12). "
        "font_family: optional string e.g. 'Arial', 'Roboto', 'Courier New'."
    ),
    {
        "user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
        "background_color": str, "text_color": str, "bold": bool, "italic": bool,
        "strikethrough": bool, "underline": bool, "font_size": int, "font_family": str,
    },
)
async def format_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        cell_fmt: dict[str, Any] = {}
        fields_list: list[str] = []

        if "background_color" in args and args["background_color"]:
            cell_fmt["backgroundColor"] = _hex_to_color(str(args["background_color"]))
            fields_list.append("userEnteredFormat.backgroundColor")

        text_fmt: dict[str, Any] = {}
        if "text_color" in args and args["text_color"]:
            text_fmt["foregroundColor"] = _hex_to_color(str(args["text_color"]))
        if "bold" in args and args["bold"] is not None:
            text_fmt["bold"] = bool(args["bold"])
        if "italic" in args and args["italic"] is not None:
            text_fmt["italic"] = bool(args["italic"])
        if "strikethrough" in args and args["strikethrough"] is not None:
            text_fmt["strikethrough"] = bool(args["strikethrough"])
        if "underline" in args and args["underline"] is not None:
            text_fmt["underline"] = bool(args["underline"])
        if "font_size" in args and args["font_size"]:
            text_fmt["fontSize"] = int(args["font_size"])
        if "font_family" in args and args["font_family"]:
            text_fmt["fontFamily"] = str(args["font_family"])

        if text_fmt:
            cell_fmt["textFormat"] = text_fmt
            fields_list.append("userEnteredFormat.textFormat")

        if not fields_list:
            return {"content": [{"type": "text", "text": "No formatting options provided — nothing changed."}]}

        _batch_update(svc, spreadsheet_id, [{
            "repeatCell": {
                "range": grid_range,
                "cell": {"userEnteredFormat": cell_fmt},
                "fields": ",".join(fields_list),
            }
        }])
        return {"content": [{"type": "text", "text": f"Formatting applied to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error formatting cells: {e}"}]}


@tool(
    "set_borders",
    (
        "Add borders to a cell range. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix, e.g. 'A1:D10'. "
        "sides: optional — comma-separated sides: 'top', 'bottom', 'left', 'right', "
        "'inner_horizontal', 'inner_vertical'. Use 'all' for all sides (default). "
        "style: optional — 'SOLID' (default), 'SOLID_MEDIUM', 'SOLID_THICK', 'DOUBLE', 'DOTTED', 'DASHED', 'NONE'. "
        "color: optional hex color (default '#000000')."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
     "sides": str, "style": str, "color": str},
)
async def set_borders(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])
        sides_str = str(args.get("sides", "all")).lower()
        style = str(args.get("style", "SOLID")).upper()
        color = _hex_to_color(str(args.get("color", "#000000")))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        border_obj = {"style": style, "color": color}
        no_border = {"style": "NONE"}

        all_sides = {"top", "bottom", "left", "right", "inner_horizontal", "inner_vertical"}
        if sides_str == "all":
            active = all_sides
        elif sides_str == "outer":
            active = {"top", "bottom", "left", "right"}
        elif sides_str == "inner":
            active = {"inner_horizontal", "inner_vertical"}
        else:
            active = {s.strip() for s in sides_str.split(",")}

        border_req: dict[str, Any] = {"range": grid_range}
        for side in all_sides:
            api_key = {
                "top": "top", "bottom": "bottom", "left": "left", "right": "right",
                "inner_horizontal": "innerHorizontal", "inner_vertical": "innerVertical",
            }[side]
            border_req[api_key] = border_obj if side in active else no_border

        _batch_update(svc, spreadsheet_id, [{"updateBorders": border_req}])
        return {"content": [{"type": "text", "text": f"Borders ({sides_str}, {style}) applied to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting borders: {e}"}]}


@tool(
    "merge_cells",
    (
        "Merge a range of cells into a single cell. "
        "merge_type: optional — 'MERGE_ALL' (default), 'MERGE_COLUMNS' (merge each column), "
        "'MERGE_ROWS' (merge each row). "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str, "merge_type": str},
)
async def merge_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])
        merge_type = str(args.get("merge_type", "MERGE_ALL")).upper()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        _batch_update(svc, spreadsheet_id, [{
            "mergeCells": {"range": grid_range, "mergeType": merge_type}
        }])
        return {"content": [{"type": "text", "text": f"Cells {sheet_name}!{range_} merged ({merge_type})."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error merging cells: {e}"}]}


@tool(
    "unmerge_cells",
    (
        "Unmerge previously merged cells in a range. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str},
)
async def unmerge_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        _batch_update(svc, spreadsheet_id, [{"unmergeCells": {"range": grid_range}}])
        return {"content": [{"type": "text", "text": f"Cells {sheet_name}!{range_} unmerged."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error unmerging cells: {e}"}]}


@tool(
    "freeze_rows_columns",
    (
        "Freeze the top N rows and/or left N columns so they stay visible while scrolling. "
        "Set frozen_rows=0 or frozen_cols=0 to unfreeze. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "frozen_rows: optional int (default 0). frozen_cols: optional int (default 0)."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "frozen_rows": int, "frozen_cols": int},
)
async def freeze_rows_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        frozen_rows = int(args.get("frozen_rows", 0))
        frozen_cols = int(args.get("frozen_cols", 0))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "frozenRowCount": frozen_rows,
                        "frozenColumnCount": frozen_cols,
                    },
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        }])
        parts = []
        if frozen_rows:
            parts.append(f"{frozen_rows} row(s)")
        if frozen_cols:
            parts.append(f"{frozen_cols} column(s)")
        msg = f"Frozen {' and '.join(parts)} in '{sheet_name}'." if parts else f"Unfroze all rows/columns in '{sheet_name}'."
        return {"content": [{"type": "text", "text": msg}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error freezing rows/columns: {e}"}]}


@tool(
    "set_column_width",
    (
        "Set the width of one or more columns. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "start_column: required — 1-based column number (e.g. 1 = A, 2 = B). "
        "end_column: optional — if omitted, only the start column is resized. "
        "width_pixels: required — width in pixels (e.g. 150)."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str,
     "start_column": int, "end_column": int, "width_pixels": int},
)
async def set_column_width(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        start_col = int(args["start_column"]) - 1  # 0-based
        end_col = int(args.get("end_column", args["start_column"])) # inclusive, stays 1-based for now
        width = int(args["width_pixels"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,  # exclusive end
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }])
        return {"content": [{"type": "text", "text": f"Column(s) {args['start_column']}–{end_col} set to {width}px in '{sheet_name}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting column width: {e}"}]}


@tool(
    "set_row_height",
    (
        "Set the height of one or more rows. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "start_row: required — 1-based row number. "
        "end_row: optional — if omitted, only the start row is resized. "
        "height_pixels: required — height in pixels (e.g. 40)."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str,
     "start_row": int, "end_row": int, "height_pixels": int},
)
async def set_row_height(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        start_row = int(args["start_row"]) - 1  # 0-based
        end_row = int(args.get("end_row", args["start_row"]))  # exclusive end
        height = int(args["height_pixels"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": start_row,
                    "endIndex": end_row,
                },
                "properties": {"pixelSize": height},
                "fields": "pixelSize",
            }
        }])
        return {"content": [{"type": "text", "text": f"Row(s) {args['start_row']}–{end_row} set to {height}px in '{sheet_name}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting row height: {e}"}]}


@tool(
    "auto_resize_columns",
    (
        "Auto-fit column widths to match their content. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "start_column: optional — 1-based, default 1 (column A). "
        "end_column: optional — 1-based inclusive, default resizes all columns."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "start_column": int, "end_column": int},
)
async def auto_resize_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        start_col = int(args.get("start_column", 1)) - 1
        end_col = int(args.get("end_column", 26))  # exclusive end

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                }
            }
        }])
        return {"content": [{"type": "text", "text": f"Columns auto-resized in '{sheet_name}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error auto-resizing columns: {e}"}]}


@tool(
    "set_number_format",
    (
        "Apply a number format to a cell range. "
        "format_type: one of 'number', 'currency', 'percent', 'date', 'datetime', 'time', 'text', 'scientific', or a custom pattern. "
        "Examples of custom patterns: '#,##0.00', '$#,##0', '0%', 'MM/DD/YYYY', '@'. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix. format_type: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str, "format_type": str},
)
async def set_number_format(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])
        fmt_type = str(args["format_type"]).lower()

        presets = {
            "number": ("#,##0.00", "NUMBER"),
            "currency": ("$#,##0.00", "CURRENCY"),
            "percent": ("0.00%", "PERCENT"),
            "date": ("MM/DD/YYYY", "DATE"),
            "datetime": ("MM/DD/YYYY HH:mm:ss", "DATE_TIME"),
            "time": ("HH:mm:ss", "TIME"),
            "text": ("@", "TEXT"),
            "scientific": ("0.00E+00", "SCIENTIFIC"),
        }

        if fmt_type in presets:
            pattern, nft = presets[fmt_type]
        else:
            pattern = args["format_type"]
            nft = "NUMBER"

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        _batch_update(svc, spreadsheet_id, [{
            "repeatCell": {
                "range": grid_range,
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": nft, "pattern": pattern}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }])
        return {"content": [{"type": "text", "text": f"Number format '{pattern}' applied to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting number format: {e}"}]}


@tool(
    "align_cells",
    (
        "Set the horizontal and/or vertical alignment of cells. "
        "horizontal: optional — 'LEFT', 'CENTER', 'RIGHT'. "
        "vertical: optional — 'TOP', 'MIDDLE', 'BOTTOM'. "
        "wrap_strategy: optional — 'WRAP' (wrap text), 'CLIP', 'OVERFLOW_CELL'. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
     "horizontal": str, "vertical": str, "wrap_strategy": str},
)
async def align_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        cell_fmt: dict[str, Any] = {}
        fields_list: list[str] = []

        if "horizontal" in args and args["horizontal"]:
            cell_fmt["horizontalAlignment"] = str(args["horizontal"]).upper()
            fields_list.append("userEnteredFormat.horizontalAlignment")
        if "vertical" in args and args["vertical"]:
            cell_fmt["verticalAlignment"] = str(args["vertical"]).upper()
            fields_list.append("userEnteredFormat.verticalAlignment")
        if "wrap_strategy" in args and args["wrap_strategy"]:
            cell_fmt["wrapStrategy"] = str(args["wrap_strategy"]).upper()
            fields_list.append("userEnteredFormat.wrapStrategy")

        if not fields_list:
            return {"content": [{"type": "text", "text": "No alignment options provided."}]}

        _batch_update(svc, spreadsheet_id, [{
            "repeatCell": {
                "range": grid_range,
                "cell": {"userEnteredFormat": cell_fmt},
                "fields": ",".join(fields_list),
            }
        }])
        return {"content": [{"type": "text", "text": f"Alignment applied to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error aligning cells: {e}"}]}


# ---------------------------------------------------------------------------
# ── VISUAL TOOLS ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _parse_a1_to_source_range(svc, spreadsheet_id: str, full_range: str) -> dict:
    """
    Parse 'SheetName!A1:C10' into a Sheets API GridRange dict.
    Fetches the sheetId for the named sheet automatically.
    """
    if "!" in full_range:
        sheet_name, cell_range = full_range.split("!", 1)
        # Strip surrounding quotes if present (some AI models add them)
        sheet_name = sheet_name.strip("'\"")
    else:
        raise ValueError("data_range must include sheet name, e.g. 'Sheet1!A1:C10'")

    sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
    return _a1_to_grid_range(sheet_id, cell_range)


@tool(
    "create_chart",
    (
        "Create a chart embedded in a spreadsheet worksheet. "
        "chart_type: 'BAR', 'COLUMN', 'LINE', 'AREA', 'PIE', 'SCATTER', 'COMBO'. "
        "data_range: full A1 range including sheet name, e.g. 'Sheet1!A1:C10'. "
        "The first column is used as labels/X-axis; remaining columns are data series. "
        "title: chart title. "
        "sheet_name: the worksheet tab where the chart will be placed. "
        "anchor_row: optional 0-based row index for chart position (default 0). "
        "anchor_col: optional 0-based column index for chart position (default 6). "
        "x_axis_title: optional. y_axis_title: optional. "
        "has_header_row: optional bool — whether first row is headers (default true). "
        "width_pixels: optional (default 600). height_pixels: optional (default 371). "
        "user_id: required. spreadsheet_id: required."
    ),
    {
        "user_id": int, "spreadsheet_id": str, "chart_type": str, "data_range": str,
        "title": str, "sheet_name": str, "anchor_row": int, "anchor_col": int,
        "x_axis_title": str, "y_axis_title": str, "has_header_row": bool,
        "width_pixels": int, "height_pixels": int,
    },
)
async def create_chart(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        chart_type = str(args.get("chart_type", "COLUMN")).upper()
        data_range = str(args["data_range"])
        title = str(args.get("title", "Chart"))
        sheet_name = str(args["sheet_name"])
        anchor_row = int(args.get("anchor_row", 0))
        anchor_col = int(args.get("anchor_col", 6))
        x_title = str(args.get("x_axis_title", ""))
        y_title = str(args.get("y_axis_title", ""))
        has_header = bool(args.get("has_header_row", True))
        width_px = int(args.get("width_pixels", 600))
        height_px = int(args.get("height_pixels", 371))

        svc = _sheets(user_id)
        anchor_sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        source_range = _parse_a1_to_source_range(svc, spreadsheet_id, data_range)

        if chart_type == "PIE":
            chart_spec: dict[str, Any] = {
                "title": title,
                "pieChart": {
                    "legendPosition": "RIGHT_LEGEND",
                    "domain": {
                        "sourceRange": {"sources": [
                            {**source_range, "endColumnIndex": source_range["startColumnIndex"] + 1}
                        ]}
                    },
                    "series": {
                        "sourceRange": {"sources": [
                            {**source_range, "startColumnIndex": source_range["startColumnIndex"] + 1,
                             "endColumnIndex": source_range["startColumnIndex"] + 2}
                        ]}
                    },
                    "threeDimensional": False,
                },
            }
        else:
            axis = []
            if x_title:
                axis.append({"position": "BOTTOM_AXIS", "title": x_title})
            if y_title:
                axis.append({"position": "LEFT_AXIS", "title": y_title})

            # Domain = first column, series = remaining columns
            domain_range = {**source_range, "endColumnIndex": source_range["startColumnIndex"] + 1}
            n_cols = source_range["endColumnIndex"] - source_range["startColumnIndex"]
            series = []
            for i in range(1, n_cols):
                series_range = {
                    **source_range,
                    "startColumnIndex": source_range["startColumnIndex"] + i,
                    "endColumnIndex": source_range["startColumnIndex"] + i + 1,
                }
                series.append({
                    "series": {"sourceRange": {"sources": [series_range]}},
                    "targetAxis": "LEFT_AXIS",
                })

            chart_spec = {
                "title": title,
                "basicChart": {
                    "chartType": chart_type,
                    "legendPosition": "BOTTOM_LEGEND",
                    "axis": axis,
                    "domains": [{"domain": {"sourceRange": {"sources": [domain_range]}}}],
                    "series": series,
                    "headerCount": 1 if has_header else 0,
                },
            }

        chart_body = {
            "spec": chart_spec,
            "position": {
                "overlayPosition": {
                    "anchorCell": {
                        "sheetId": anchor_sheet_id,
                        "rowIndex": anchor_row,
                        "columnIndex": anchor_col,
                    },
                    "offsetXPixels": 0,
                    "offsetYPixels": 0,
                    "widthPixels": width_px,
                    "heightPixels": height_px,
                }
            },
        }

        result = _batch_update(svc, spreadsheet_id, [{"addChart": {"chart": chart_body}}])
        chart_id = result["replies"][0]["addChart"]["chart"]["chartId"]
        return {
            "content": [{
                "type": "text",
                "text": f"Chart '{title}' ({chart_type}) created successfully. Chart ID: {chart_id}",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating chart: {e}"}]}


@tool(
    "list_charts",
    (
        "List all charts embedded in a spreadsheet, including their IDs, types, and titles. "
        "user_id: required. spreadsheet_id: required."
    ),
    {"user_id": int, "spreadsheet_id": str},
)
async def list_charts(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])

        svc = _sheets(user_id)
        ss = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        charts = []
        for sheet in ss.get("sheets", []):
            sheet_title = sheet["properties"]["title"]
            for chart in sheet.get("charts", []):
                spec = chart.get("spec", {})
                chart_type = "UNKNOWN"
                if "basicChart" in spec:
                    chart_type = spec["basicChart"].get("chartType", "UNKNOWN")
                elif "pieChart" in spec:
                    chart_type = "PIE"
                charts.append({
                    "chart_id": chart["chartId"],
                    "title": spec.get("title", "(untitled)"),
                    "type": chart_type,
                    "sheet": sheet_title,
                })

        if not charts:
            return {"content": [{"type": "text", "text": "No charts found in this spreadsheet."}]}
        return {"content": [{"type": "text", "text": json.dumps(charts, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing charts: {e}"}]}


@tool(
    "delete_chart",
    (
        "Delete a chart from a spreadsheet by its chart ID. "
        "Use list_charts to find the chart_id. "
        "user_id: required. spreadsheet_id: required. chart_id: required."
    ),
    {"user_id": int, "spreadsheet_id": str, "chart_id": int},
)
async def delete_chart(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        chart_id = int(args["chart_id"])

        svc = _sheets(user_id)
        _batch_update(svc, spreadsheet_id, [{"deleteEmbeddedObject": {"objectId": chart_id}}])
        return {"content": [{"type": "text", "text": f"Chart {chart_id} deleted."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting chart: {e}"}]}


@tool(
    "add_conditional_formatting",
    (
        "Add conditional formatting to highlight cells based on rules. "
        "rule_type: 'gradient' (color scale) or 'single_color'. "
        "For 'gradient': colors cells from min_color → mid_color → max_color based on values. "
        "For 'single_color': applies bg_color when condition is met. "
        "condition: for single_color, one of: "
        "'GREATER_THAN', 'LESS_THAN', 'EQUAL_TO', 'NOT_EQUAL_TO', 'GREATER_THAN_OR_EQUAL', "
        "'LESS_THAN_OR_EQUAL', 'TEXT_CONTAINS', 'TEXT_NOT_CONTAINS', 'IS_EMPTY', 'IS_NOT_EMPTY'. "
        "condition_value: the value to compare against (e.g. '50'). "
        "min_color, mid_color, max_color: hex colors for gradient (e.g. '#FF0000', '#FFFF00', '#00FF00'). "
        "bg_color: hex color for single_color rule. "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix. rule_type: required."
    ),
    {
        "user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
        "rule_type": str, "condition": str, "condition_value": str,
        "min_color": str, "mid_color": str, "max_color": str, "bg_color": str,
    },
)
async def add_conditional_formatting(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])
        rule_type = str(args.get("rule_type", "gradient")).lower()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        if rule_type == "gradient":
            min_color = _hex_to_color(str(args.get("min_color", "#FF0000")))
            mid_color = _hex_to_color(str(args.get("mid_color", "#FFFF00")))
            max_color = _hex_to_color(str(args.get("max_color", "#00FF00")))
            rule = {
                "gradientRule": {
                    "minpoint": {"color": min_color, "type": "MIN"},
                    "midpoint": {"color": mid_color, "type": "PERCENTILE", "value": "50"},
                    "maxpoint": {"color": max_color, "type": "MAX"},
                }
            }
        else:
            condition_type = str(args.get("condition", "GREATER_THAN")).upper()
            condition_value = str(args.get("condition_value", "0"))
            bg_color = _hex_to_color(str(args.get("bg_color", "#FFFF00")))

            condition_obj: dict[str, Any] = {"type": condition_type}
            if condition_type not in ("IS_EMPTY", "IS_NOT_EMPTY"):
                condition_obj["values"] = [{"userEnteredValue": condition_value}]

            rule = {
                "booleanRule": {
                    "condition": condition_obj,
                    "format": {"backgroundColor": bg_color},
                }
            }

        _batch_update(svc, spreadsheet_id, [{
            "addConditionalFormatRule": {
                "rule": {**rule, "ranges": [grid_range]},
                "index": 0,
            }
        }])
        return {"content": [{"type": "text", "text": f"Conditional formatting ({rule_type}) added to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding conditional formatting: {e}"}]}


@tool(
    "add_data_validation",
    (
        "Add data validation to cells (dropdowns, checkboxes, number restrictions). "
        "validation_type: 'dropdown_list', 'checkbox', 'number_range', 'text_contains'. "
        "For 'dropdown_list': values = comma-separated options, e.g. 'Yes,No,Maybe'. "
        "For 'checkbox': no extra params needed. "
        "For 'number_range': min_value and max_value required. "
        "For 'text_contains': pattern = text the cell must contain. "
        "show_warning: optional bool — if true, shows a warning but allows entry (default false = reject). "
        "user_id: required. spreadsheet_id: required. sheet_name: required. "
        "range: required — A1 notation WITHOUT sheet prefix. validation_type: required."
    ),
    {
        "user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
        "validation_type": str, "values": str, "min_value": str, "max_value": str,
        "pattern": str, "show_warning": bool,
    },
)
async def add_data_validation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        sheet_name = str(args["sheet_name"])
        range_ = str(args["range"])
        v_type = str(args["validation_type"]).lower()
        show_warning = bool(args.get("show_warning", False))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_)

        if v_type == "dropdown_list":
            options = [v.strip() for v in str(args.get("values", "")).split(",") if v.strip()]
            condition = {
                "type": "ONE_OF_LIST",
                "values": [{"userEnteredValue": o} for o in options],
            }
        elif v_type == "checkbox":
            condition = {"type": "BOOLEAN"}
        elif v_type == "number_range":
            condition = {
                "type": "NUMBER_BETWEEN",
                "values": [
                    {"userEnteredValue": str(args.get("min_value", "0"))},
                    {"userEnteredValue": str(args.get("max_value", "100"))},
                ],
            }
        elif v_type == "text_contains":
            condition = {
                "type": "TEXT_CONTAINS",
                "values": [{"userEnteredValue": str(args.get("pattern", ""))}],
            }
        else:
            return {"content": [{"type": "text", "text": f"Unknown validation_type: {v_type}"}]}

        _batch_update(svc, spreadsheet_id, [{
            "setDataValidation": {
                "range": grid_range,
                "rule": {
                    "condition": condition,
                    "inputMessage": "",
                    "strict": not show_warning,
                    "showCustomUi": v_type == "dropdown_list",
                },
            }
        }])
        return {"content": [{"type": "text", "text": f"Data validation ({v_type}) applied to {sheet_name}!{range_}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding data validation: {e}"}]}


@tool(
    "add_sparklines",
    (
        "Add sparkline mini-charts inside cells. A SPARKLINE formula is written into each target cell. "
        "sparkline_type: 'LINE' (default), 'BAR', 'COLUMN', 'WINLOSS'. "
        "Each row in target_range gets a sparkline from the corresponding row of data_range. "
        "target_range: A1 notation including sheet name — one column, e.g. 'Sheet1!F2:F11'. "
        "data_range: A1 notation including sheet name — the source data, e.g. 'Sheet1!A2:E11'. "
        "sparkline_color: optional hex color (default '#000000'). "
        "user_id: required. spreadsheet_id: required."
    ),
    {
        "user_id": int, "spreadsheet_id": str, "target_range": str, "data_range": str,
        "sparkline_type": str, "sparkline_color": str,
    },
)
async def add_sparklines(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"])
        target_range = str(args["target_range"])
        data_range = str(args["data_range"])
        sparkline_type = str(args.get("sparkline_type", "LINE")).upper()
        sparkline_color = str(args.get("sparkline_color", "#000000")).lstrip("#")

        svc = _sheets(user_id)

        # Parse target range to get individual cell addresses
        if "!" not in target_range:
            raise ValueError("target_range must include sheet name, e.g. 'Sheet1!F2:F11'")
        tgt_sheet, tgt_cells = target_range.split("!", 1)
        tgt_sheet = tgt_sheet.strip("'\"")

        if "!" not in data_range:
            raise ValueError("data_range must include sheet name, e.g. 'Sheet1!A2:E11'")
        src_sheet, src_cells = data_range.split("!", 1)
        src_sheet = src_sheet.strip("'\"")

        # Parse the target column + row range
        tgt_match = re.match(r"^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$", tgt_cells)
        src_match = re.match(r"^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$", src_cells)

        if not tgt_match or not src_match:
            raise ValueError("Both target_range and data_range must be full ranges like 'A2:A11'")

        tgt_col, tgt_start_row, _, tgt_end_row = tgt_match.groups()
        src_col_start, src_start_row, src_col_end, _ = src_match.groups()

        n_rows = int(tgt_end_row) - int(tgt_start_row) + 1
        formulas = []
        for i in range(n_rows):
            row = int(tgt_start_row) + i
            data_row = int(src_start_row) + i
            data_cell = f"'{src_sheet}'!{src_col_start}{data_row}:{src_col_end}{data_row}"
            color_hex = sparkline_color
            formula = (
                f'=SPARKLINE({data_cell},{{"charttype","{sparkline_type.lower()}",'
                f'"color","#{color_hex}"}})'
            )
            formulas.append([formula])

        result = (
            svc.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tgt_sheet}'!{tgt_col}{tgt_start_row}:{tgt_col}{tgt_end_row}",
                valueInputOption="USER_ENTERED",
                body={"values": formulas},
            )
            .execute()
        )
        return {
            "content": [{
                "type": "text",
                "text": f"Added {n_rows} {sparkline_type} sparkline(s) to '{tgt_sheet}'!{tgt_col}{tgt_start_row}:{tgt_col}{tgt_end_row}.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding sparklines: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registrations — one per sub-agent
# ---------------------------------------------------------------------------

sheets_data_server = create_sdk_mcp_server(
    name="sheets_data",
    version="1.0.0",
    tools=[
        create_spreadsheet,
        list_spreadsheets,
        get_spreadsheet_info,
        read_sheet,
        write_sheet,
        append_rows,
        clear_range,
        add_worksheet,
        delete_worksheet,
        rename_worksheet,
        duplicate_worksheet,
        sort_range,
        find_and_replace,
    ],
)

sheets_format_server = create_sdk_mcp_server(
    name="sheets_format",
    version="1.0.0",
    tools=[
        get_spreadsheet_info,   # needed to look up sheet IDs
        format_cells,
        set_borders,
        merge_cells,
        unmerge_cells,
        freeze_rows_columns,
        set_column_width,
        set_row_height,
        auto_resize_columns,
        set_number_format,
        align_cells,
    ],
)

sheets_visual_server = create_sdk_mcp_server(
    name="sheets_visual",
    version="1.0.0",
    tools=[
        get_spreadsheet_info,   # needed to look up sheet IDs
        create_chart,
        list_charts,
        delete_chart,
        add_conditional_formatting,
        add_data_validation,
        add_sparklines,
    ],
)
