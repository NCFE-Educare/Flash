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
from typing import Any, Dict, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import (
    delete_sheets_tokens,
    get_sheets_tokens,
    save_sheets_tokens,
)

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
_sheet_metadata_cache: dict[str, dict] = {}  # Cache: {spreadsheet_id: {sheet_name: sheet_id, ...}}


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
        try:
            creds.refresh(Request())
        except Exception:
            raise
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
    else:
        print(f"[SHEETS DEBUG] Token still valid (not expired)")

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
    Convert A1-notation range (e.g. 'A1:D5', 'A:D', '1:5', 'A1') to a Sheets API GridRange dict.
    range_str must NOT include a sheet name prefix.
    """
    range_str = range_str.strip().upper()
    grid: dict[str, Any] = {"sheetId": sheet_id}

    # Full range: A1:D5
    full_match = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", range_str)
    if full_match:
        sc, sr, ec, er = full_match.groups()
        grid["startRowIndex"] = int(sr) - 1
        grid["endRowIndex"] = int(er)
        grid["startColumnIndex"] = _col_index(sc)
        grid["endColumnIndex"] = _col_index(ec) + 1
        return grid

    # Column only: A:D
    col_only = re.match(r"^([A-Z]+):([A-Z]+)$", range_str)
    if col_only:
        sc, ec = col_only.groups()
        grid["startColumnIndex"] = _col_index(sc)
        grid["endColumnIndex"] = _col_index(ec) + 1
        return grid

    # Row only: 1:5
    row_only = re.match(r"^(\d+):(\d+)$", range_str)
    if row_only:
        sr, er = row_only.groups()
        grid["startRowIndex"] = int(sr) - 1
        grid["endRowIndex"] = int(er)
        return grid

    # Partial: A1:A or A1:5
    partial = re.match(r"^([A-Z]+)(\d+):([A-Z]+)?(\d+)?$", range_str)
    if partial:
        sc, sr, ec, er = partial.groups()
        grid["startRowIndex"] = int(sr) - 1
        grid["startColumnIndex"] = _col_index(sc)
        if er: grid["endRowIndex"] = int(er)
        if ec: grid["endColumnIndex"] = _col_index(ec) + 1
        return grid

    # Single cell: A1
    single = re.match(r"^([A-Z]+)(\d+)$", range_str)
    if single:
        sc, sr = single.groups()
        grid["startRowIndex"] = int(sr) - 1
        grid["endRowIndex"] = int(sr)
        grid["startColumnIndex"] = _col_index(sc)
        grid["endColumnIndex"] = _col_index(sc) + 1
        return grid

    raise ValueError(f"Invalid A1 range: '{range_str}'. Use 'A1:D5', 'A:D', or '1:5'.")


def _get_sheet_id_cached(service, spreadsheet_id: str, sheet_name: str) -> int:
    """
    Return the integer sheetId for the named worksheet tab.
    Uses cache to avoid repeated API calls.
    """
    if spreadsheet_id not in _sheet_metadata_cache:
        try:
            ss = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            metadata: dict = {}
            for sheet in ss.get("sheets", []):
                props = sheet.get("properties", {})
                metadata[props.get("title")] = props.get("sheetId")
            _sheet_metadata_cache[spreadsheet_id] = metadata
        except Exception as e:
            raise ValueError(f"Could not fetch sheet metadata: {e}")

    cache = _sheet_metadata_cache[spreadsheet_id]
    if sheet_name not in cache:
        raise ValueError(
            f"Sheet '{sheet_name}' not found. Available sheets: {', '.join(cache.keys())}"
        )
    return cache[sheet_name]


def _clear_cache(spreadsheet_id: str) -> None:
    """Clear cached metadata when sheets are added/deleted/renamed."""
    _sheet_metadata_cache.pop(spreadsheet_id, None)


# ---------------------------------------------------------------------------
# Validation utilities
# ---------------------------------------------------------------------------

def _validate_required(args: dict, *keys: str) -> None:
    """Raise ValueError if any required keys are missing or empty."""
    missing = [k for k in keys if k not in args or not str(args.get(k, "")).strip()]
    if missing:
        raise ValueError(f"Missing required parameter(s): {', '.join(missing)}")


def _validate_a1_notation(range_str: str) -> None:
    """Validate that a range is in A1 notation (no sheet name)."""
    if "!" in range_str:
        raise ValueError(
            f"Range '{range_str}' includes sheet name. Use only the cell notation (e.g., 'A1:D10'), "
            "not 'Sheet1!A1:D10'."
        )


def _validate_full_a1_notation(range_str: str) -> Tuple[str, str]:
    """
    Validate and parse a full A1 range with sheet name (e.g., 'Sheet1!A1:D10').
    Returns (sheet_name, range_without_sheet_name).
    """
    if "!" not in range_str:
        raise ValueError(
            f"Range '{range_str}' must include sheet name (e.g., 'Sheet1!A1:D10')"
        )
    parts = range_str.split("!", 1)
    sheet_name = parts[0].strip("'\"")
    cell_range = parts[1]
    _validate_a1_notation(cell_range)
    return sheet_name, cell_range


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
        "Create a new Google Spreadsheet. "
        "REQUIRED: user_id, title. OPTIONAL: sheet_name (default 'Sheet1'). "
        "RETURNS: spreadsheet_id, url, sheet_id. "
        "USE THIS: when starting fresh. "
        "EXAMPLE: user_id=123, title='Q4 Sales Report', sheet_name='2024 Q4'."
    ),
    {"user_id": int, "title": str, "sheet_name": str},
)
async def create_spreadsheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "title")
        user_id = int(args["user_id"])
        title = str(args["title"]).strip()
        sheet_name = str(args.get("sheet_name", "Sheet1")).strip()

        if not title:
            return {"content": [{"type": "text", "text": "Error: title cannot be empty."}]}
        if not sheet_name:
            sheet_name = "Sheet1"

        svc = _sheets(user_id)
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": sheet_name}}],
        }
        result = svc.spreadsheets().create(body=body).execute()
        sid = result["spreadsheetId"]
        _clear_cache(sid)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "spreadsheet_id": sid,
                    "title": result["properties"]["title"],
                    "url": result["spreadsheetUrl"],
                    "sheet_name": sheet_name,
                    "sheet_id": result["sheets"][0]["properties"]["sheetId"],
                }, indent=2),
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating spreadsheet: {e}"}]}


@tool(
    "list_spreadsheets",
    (
        "List the user's Google Spreadsheets from Drive. "
        "REQUIRED: user_id. OPTIONAL: max_results (default 20). "
        "RETURNS: list of spreadsheets (id, name, modified, url). "
        "EXAMPLE: user_id=123, max_results=10."
    ),
    {"user_id": int, "max_results": int},
)
async def list_spreadsheets(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id")
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
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing spreadsheets: {e}"}]}


@tool(
    "get_spreadsheet_info",
    (
        "Get all worksheet names, their IDs, and dimensions. "
        "REQUIRED: user_id, spreadsheet_id. "
        "RETURNS: worksheet metadata (names, IDs, row/column counts). "
        "USE THIS: BEFORE formatting or charting (to get sheetIds). "
        "EXAMPLE: Before format_cells, call this first to get sheet_id."
    ),
    {"user_id": int, "spreadsheet_id": str},
)
async def get_spreadsheet_info(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()

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
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "read_sheet",
    (
        "Read cell values from a range. "
        "REQUIRED: user_id, spreadsheet_id, range (with sheet name, e.g. 'Sheet1!A1:D10'). "
        "RETURNS: 2D array of values. "
        "USE THIS: to inspect data before modifying. "
        "EXAMPLE: range='Sales!A1:C100' reads rows 1-100 from 'Sales' sheet."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str},
)
async def read_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        range_ = str(args["range"]).strip()

        # Validate range format
        if "!" not in range_:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: range must include sheet name (e.g., 'Sheet1!A1:D10'), got '{range_}'"
                }]
            }

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
                "text": json.dumps({
                    "range": result.get("range", range_),
                    "row_count": len(values),
                    "values": values
                }, indent=2),
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading sheet: {e}"}]}


@tool(
    "write_sheet",
    (
        "Write (overwrite) values to a range. REPLACES existing data. "
        "REQUIRED: user_id, spreadsheet_id, range (with sheet name, e.g. 'Sheet1!A1'), values (JSON 2D array). "
        "RETURNS: number of rows/columns written. "
        "⚠️  WARNING: This OVERWRITES data. Use append_rows to ADD rows instead. "
        "EXAMPLE: range='Sheet1!A1', values='[[\"Name\",\"Score\"],[\"Alice\",95]]' writes a 2×2 table."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "values": str},
)
async def write_sheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range", "values")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        range_ = str(args["range"]).strip()

        if "!" not in range_:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: range must include sheet name (e.g., 'Sheet1!A1'), got '{range_}'"
                }]
            }

        raw = args["values"]
        if isinstance(raw, list):
            values = raw
        else:
            raw_str = str(raw).strip()
            try:
                values = json.loads(raw_str)
            except (json.JSONDecodeError, ValueError):
                import ast
                values = ast.literal_eval(raw_str)

        if not isinstance(values, list):
            return {"content": [{"type": "text", "text": "Error: values must be a 2D array (list of lists)"}]}

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
                    f"✓ Written {result.get('updatedRows', 0)} rows × "
                    f"{result.get('updatedColumns', 0)} columns to {result.get('updatedRange', range_)}."
                ),
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "append_rows",
    (
        "Append new rows to the bottom of existing data. Automatically finds the first empty row. "
        "REQUIRED: user_id, spreadsheet_id, range (sheet name, e.g. 'Sheet1'), values (JSON 2D array). "
        "RETURNS: number of rows appended. "
        "USE THIS: to add records without overwriting existing data. "
        "EXAMPLE: range='Sales', values='[[\"2024-01-15\",\"Widget\",50]]' adds one row at the bottom."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "values": str},
)
async def append_rows(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range", "values")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        range_ = str(args["range"]).strip()

        raw = args["values"]
        if isinstance(raw, list):
            values = raw
        else:
            raw_str = str(raw).strip()
            try:
                values = json.loads(raw_str)
            except (json.JSONDecodeError, ValueError):
                import ast
                values = ast.literal_eval(raw_str)

        if not isinstance(values, list):
            return {"content": [{"type": "text", "text": "Error: values must be a 2D array"}]}

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
                    f"✓ Appended {updates.get('updatedRows', len(values))} rows to "
                    f"{updates.get('updatedRange', range_)}."
                ),
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "insert_rows",
    (
        "Insert blank rows at a specific position. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, insert_index (0-based row), count (how many rows). "
        "RETURNS: success message. "
        "USE THIS: to make room for new data in the middle of a sheet. "
        "EXAMPLE: insert_index=2, count=3 inserts 3 blank rows at row 3."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "insert_index": int, "count": int},
)
async def insert_rows(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        insert_index = int(args.get("insert_index", 0))
        count = int(args.get("count", 1))

        if count < 1:
            return {"content": [{"type": "text", "text": "Error: count must be at least 1"}]}

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": insert_index,
                    "endIndex": insert_index + count,
                }
            }
        }])
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Inserted {count} row(s) at position {insert_index} in '{sheet_name}'."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "delete_rows",
    (
        "Delete rows from a sheet. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, start_row (1-based), end_row (1-based, inclusive). "
        "RETURNS: success message. "
        "USE THIS: to remove unwanted rows. "
        "EXAMPLE: start_row=5, end_row=7 deletes rows 5, 6, and 7."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "start_row": int, "end_row": int},
)
async def delete_rows(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "start_row", "end_row")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        start_row = int(args["start_row"])
        end_row = int(args["end_row"])

        if start_row < 1 or end_row < 1 or start_row > end_row:
            return {"content": [{"type": "text", "text": "Error: start_row and end_row must be positive, start ≤ end"}]}

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": start_row - 1,  # 0-based
                    "endIndex": end_row,  # exclusive
                }
            }
        }])
        count = end_row - start_row + 1
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Deleted {count} row(s) (rows {start_row}-{end_row}) from '{sheet_name}'."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "insert_columns",
    (
        "Insert blank columns at a specific position. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, insert_index (0-based column), count. "
        "RETURNS: success message. "
        "USE THIS: to add new data columns in the middle. "
        "EXAMPLE: insert_index=2, count=2 inserts 2 blank columns at column C."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "insert_index": int, "count": int},
)
async def insert_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        insert_index = int(args.get("insert_index", 0))
        count = int(args.get("count", 1))

        if count < 1:
            return {"content": [{"type": "text", "text": "Error: count must be at least 1"}]}

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": insert_index,
                    "endIndex": insert_index + count,
                }
            }
        }])
        col_letter = chr(65 + insert_index) if insert_index < 26 else f"({insert_index})"
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Inserted {count} column(s) at position {col_letter} in '{sheet_name}'."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "delete_columns",
    (
        "Delete columns from a sheet. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, start_col (1-based), end_col (1-based, inclusive). "
        "RETURNS: success message. "
        "USE THIS: to remove unwanted columns. "
        "EXAMPLE: start_col=3, end_col=5 deletes columns C, D, E."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "start_col": int, "end_col": int},
)
async def delete_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "start_col", "end_col")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        start_col = int(args["start_col"])
        end_col = int(args["end_col"])

        if start_col < 1 or end_col < 1 or start_col > end_col:
            return {"content": [{"type": "text", "text": "Error: column indices must be positive"}]}

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col - 1,  # 0-based
                    "endIndex": end_col,  # exclusive
                }
            }
        }])
        count = end_col - start_col + 1
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Deleted {count} column(s) from '{sheet_name}'."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "clear_range",
    (
        "Clear all values from a range (keeps formatting). "
        "REQUIRED: user_id, spreadsheet_id, range (with sheet name, e.g. 'Sheet1!A1:D100'). "
        "RETURNS: success message. "
        "USE THIS: to wipe data without losing colors/borders. "
        "EXAMPLE: range='Sheet1!A2:Z100' clears everything below header."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str},
)
async def clear_range(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        range_ = str(args["range"]).strip()

        if "!" not in range_:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: range must include sheet name (e.g., 'Sheet1!A1'), got '{range_}'"
                }]
            }

        svc = _sheets(user_id)
        svc.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_, body={}
        ).execute()
        return {"content": [{"type": "text", "text": f"✓ Cleared values in range {range_}."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error clearing range: {e}"}]}


@tool(
    "add_worksheet",
    (
        "Add a new worksheet tab to a spreadsheet. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name. OPTIONAL: rows (default 1000), cols (26). "
        "RETURNS: sheet_id. "
        "EXAMPLE: user_id=123, spreadsheet_id=abc, sheet_name='Summary'."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "rows": int, "cols": int},
)
async def add_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
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
        _clear_cache(spreadsheet_id)
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "sheet_name": new_props["title"],
                    "sheet_id": new_props["sheetId"],
                    "message": f"✓ Worksheet '{sheet_name}' added successfully.",
                }, indent=2),
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding worksheet: {e}"}]}


@tool(
    "delete_worksheet",
    (
        "Delete a worksheet tab. ⚠️  WARNING: This is permanent. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name. "
        "RETURNS: success message."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str},
)
async def delete_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        _batch_update(svc, spreadsheet_id, [{"deleteSheet": {"sheetId": sheet_id}}])
        _clear_cache(spreadsheet_id)
        return {"content": [{"type": "text", "text": f"✓ Worksheet '{sheet_name}' deleted."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting worksheet: {e}"}]}


@tool(
    "rename_worksheet",
    (
        "Rename a worksheet tab. "
        "REQUIRED: user_id, spreadsheet_id, old_name, new_name. "
        "RETURNS: success message."
    ),
    {"user_id": int, "spreadsheet_id": str, "old_name": str, "new_name": str},
)
async def rename_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "old_name", "new_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        old_name = str(args["old_name"]).strip()
        new_name = str(args["new_name"]).strip()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, old_name)
        _batch_update(svc, spreadsheet_id, [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "title": new_name},
                "fields": "title",
            }
        }])
        _clear_cache(spreadsheet_id)
        return {"content": [{"type": "text", "text": f"✓ Worksheet renamed from '{old_name}' to '{new_name}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error renaming worksheet: {e}"}]}


@tool(
    "duplicate_worksheet",
    (
        "Duplicate an existing worksheet tab. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name. OPTIONAL: new_name. "
        "RETURNS: metadata about the new sheet."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "new_name": str},
)
async def duplicate_worksheet(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        new_name = str(args.get("new_name", f"Copy of {sheet_name}")).strip()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        result = _batch_update(svc, spreadsheet_id, [{
            "duplicateSheet": {
                "sourceSheetId": sheet_id,
                "newSheetName": new_name,
            }
        }])
        new_props = result["replies"][0]["duplicateSheet"]["properties"]
        _clear_cache(spreadsheet_id)
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Worksheet '{sheet_name}' duplicated as '{new_props['title']}' (sheetId={new_props['sheetId']}).",
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error duplicating worksheet: {e}"}]}


@tool(
    "sort_range",
    (
        "Sort rows in a range by a specific column. "
        "REQUIRED: user_id, spreadsheet_id, range (with sheet name, e.g. 'Sheet1!A2:D100'). "
        "REQUIRED: sort_column (1-based, e.g. 2 for column B). "
        "OPTIONAL: ascending (default true). "
        "TIP: exclude header row from range. "
        "EXAMPLE: range='Data!A2:E100', sort_column=1, ascending=false."
    ),
    {"user_id": int, "spreadsheet_id": str, "range": str, "sort_column": int, "ascending": bool},
)
async def sort_range(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "range", "sort_column")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        range_full = str(args["range"]).strip()
        sort_col = int(args["sort_column"]) - 1  # 0-based
        ascending = bool(args.get("ascending", True))

        sheet_name, cell_range = _validate_full_a1_notation(range_full)
        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
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
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Range '{range_full}' sorted by column {sort_col + 1} ({'ascending' if ascending else 'descending'})."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "find_and_replace",
    (
        "Find and replace text across a spreadsheet. "
        "REQUIRED: user_id, spreadsheet_id, find, replacement. "
        "OPTIONAL: sheet_name (searches all if omitted). "
        "RETURNS: number of occurrences changed. "
        "EXAMPLE: find='Old Name', replacement='New Name', sheet_name='Contacts'."
    ),
    {"user_id": int, "spreadsheet_id": str, "find": str, "replacement": str,
     "sheet_name": str, "match_case": bool, "match_entire_cell": bool},
)
async def find_and_replace(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "find", "replacement")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        find_text = str(args["find"]).strip()
        replacement_text = str(args["replacement"])
        match_case = bool(args.get("match_case", False))
        match_entire_cell = bool(args.get("match_entire_cell", False))

        svc = _sheets(user_id)
        req: dict[str, Any] = {
            "find": find_text,
            "replacement": replacement_text,
            "matchCase": match_case,
            "matchEntireCell": match_entire_cell,
            "searchByRegex": False,
            "includeFormulas": False,
        }

        if "sheet_name" in args and str(args["sheet_name"]).strip():
            sheet_name = str(args["sheet_name"]).strip()
            sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
            req["range"] = {"sheetId": sheet_id}

        result = _batch_update(svc, spreadsheet_id, [{"findReplace": req}])
        stats = result["replies"][0].get("findReplace", {})
        return {
            "content": [{
                "type": "text",
                "text": f"✓ Replaced {stats.get('occurrencesChanged', 0)} occurrence(s) of '{find_text}' with '{replacement_text}'."
            }]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


# ---------------------------------------------------------------------------
# ── FORMAT TOOLS ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@tool(
    "format_cells",
    (
        "Apply colors, fonts, and styles to a range. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range (A1 notation WITHOUT sheet name). "
        "OPTIONAL: background_color (#hex), text_color (#hex), bold, italic, font_size, font_family. "
        "EXAMPLE: sheet_name='Sales', range='A1:D1', background_color='#4285F4', bold=true."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
     "background_color": str, "text_color": str, "bold": bool, "italic": bool,
     "strikethrough": bool, "underline": bool, "font_size": int, "font_family": str},
)
async def format_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

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
            return {"content": [{"type": "text", "text": "Validation Error: No formatting options provided."}]}

        _batch_update(svc, spreadsheet_id, [{
            "repeatCell": {
                "range": grid_range,
                "cell": {"userEnteredFormat": cell_fmt},
                "fields": ",".join(fields_list),
            }
        }])
        return {"content": [{"type": "text", "text": f"✓ Formatting applied to '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "set_borders",
    (
        "Add borders to a range. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range (A1 notation WITHOUT sheet name). "
        "OPTIONAL: sides ('all', 'outer', 'inner', 'top', etc.), style ('SOLID', 'DOUBLE'), color (#hex). "
        "EXAMPLE: sides='outer', style='SOLID_MEDIUM', color='#000000'."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
     "sides": str, "style": str, "color": str},
)
async def set_borders(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)

        sides_str = str(args.get("sides", "all")).lower()
        style = str(args.get("style", "SOLID")).upper()
        color = _hex_to_color(str(args.get("color", "#000000")))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

        border_obj = {"style": style, "color": color}
        no_border = {"style": "NONE"}

        all_sides = {"top", "bottom", "left", "right", "inner_horizontal", "inner_vertical"}
        if sides_str == "all":
            active = all_sides
        elif sides_str == "outer":
            active = {"top", "bottom", "left", "right"}
        elif sides_str in ("inner", "inside"):
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
        return {"content": [{"type": "text", "text": f"✓ Borders ({sides_str}) applied to '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "merge_cells",
    (
        "Merge multiple cells into one. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range (A1 notation WITHOUT sheet name). "
        "OPTIONAL: merge_type ('MERGE_ALL', 'MERGE_COLUMNS', 'MERGE_ROWS'). "
        "EXAMPLE: range='A1:D1', merge_type='MERGE_ALL'."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str, "merge_type": str},
)
async def merge_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)
        merge_type = str(args.get("merge_type", "MERGE_ALL")).upper()

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

        _batch_update(svc, spreadsheet_id, [{
            "mergeCells": {"range": grid_range, "mergeType": merge_type}
        }])
        return {"content": [{"type": "text", "text": f"✓ Cells merged in '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "unmerge_cells",
    (
        "Unmerge cells in a range. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range. "
        "RETURNS: success message."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str},
)
async def unmerge_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

        _batch_update(svc, spreadsheet_id, [{"unmergeCells": {"range": grid_range}}])
        return {"content": [{"type": "text", "text": f"✓ Cells unmerged in '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "freeze_rows_columns",
    (
        "Freeze the top N rows or left N columns. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name. OPTIONAL: frozen_rows, frozen_cols. "
        "RETURNS: success message. "
        "EXAMPLE: frozen_rows=1 freezes the header row."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "frozen_rows": int, "frozen_cols": int},
)
async def freeze_rows_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        rows = int(args.get("frozen_rows", 0))
        cols = int(args.get("frozen_cols", 0))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        }])
        return {"content": [{"type": "text", "text": f"✓ View frozen in '{sheet_name}' ({rows} rows, {cols} cols)."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "set_column_width",
    (
        "Set width of columns in pixels. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, start_column (1-based). "
        "OPTIONAL: end_column (inclusive), width_pixels (default 100). "
        "EXAMPLE: start_column=1, end_column=3, width_pixels=150."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str,
     "start_column": int, "end_column": int, "width_pixels": int},
)
async def set_column_width(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "start_column")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        start_col = int(args["start_column"]) - 1
        end_col = int(args.get("end_column", args["start_column"]))
        width = int(args.get("width_pixels", 100))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

        _batch_update(svc, spreadsheet_id, [{
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }])
        return {"content": [{"type": "text", "text": f"✓ Column width(s) set to {width}px in '{sheet_name}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "set_row_height",
    (
        "Set height of rows in pixels. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, start_row (1-based). "
        "OPTIONAL: end_row (inclusive), height_pixels (default 21). "
        "EXAMPLE: start_row=1, end_row=1, height_pixels=40."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str,
     "start_row": int, "end_row": int, "height_pixels": int},
)
async def set_row_height(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "start_row")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        start_row = int(args["start_row"]) - 1
        end_row = int(args.get("end_row", args["start_row"]))
        height = int(args.get("height_pixels", 21))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

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
        return {"content": [{"type": "text", "text": f"✓ Row height(s) set to {height}px in '{sheet_name}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "auto_resize_columns",
    (
        "Fit column widths to content automatically. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name. "
        "OPTIONAL: start_column, end_column (inclusive, default all). "
        "EXAMPLE: sheet_name='Data', start_column=1, end_column=5."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "start_column": int, "end_column": int},
)
async def auto_resize_columns(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        start_col = int(args.get("start_column", 1)) - 1
        end_col = int(args.get("end_column", 26))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)

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
        return {"content": [{"type": "text", "text": f"✓ Columns auto-resized in '{sheet_name}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "set_number_format",
    (
        "Apply a number format to a range. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range, format_type. "
        "format_type: 'number', 'currency', 'percent', 'date', 'text', or custom pattern like '#,##0.00'. "
        "EXAMPLE: range='B2:B10', format_type='currency'."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str, "format_type": str},
)
async def set_number_format(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range", "format_type")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)
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

        pattern, nft = presets.get(fmt_type, (args["format_type"], "NUMBER"))

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

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
        return {"content": [{"type": "text", "text": f"✓ Number format '{pattern}' applied to '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


@tool(
    "align_cells",
    (
        "Set cell alignment and wrapping strategy. "
        "REQUIRED: user_id, spreadsheet_id, sheet_name, range. "
        "OPTIONAL: horizontal ('LEFT', 'CENTER', 'RIGHT'), vertical ('TOP', 'MIDDLE', 'BOTTOM'). "
        "OPTIONAL: wrap_strategy ('WRAP', 'CLIP', 'OVERFLOW_CELL'). "
        "EXAMPLE: horizontal='CENTER', wrap_strategy='WRAP'."
    ),
    {"user_id": int, "spreadsheet_id": str, "sheet_name": str, "range": str,
     "horizontal": str, "vertical": str, "wrap_strategy": str},
)
async def align_cells(args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name", "range")
        user_id = int(args["user_id"])
        spreadsheet_id = str(args["spreadsheet_id"]).strip()
        sheet_name = str(args["sheet_name"]).strip()
        range_str = str(args["range"]).strip()
        _validate_a1_notation(range_str)

        svc = _sheets(user_id)
        sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
        grid_range = _a1_to_grid_range(sheet_id, range_str)

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
            return {"content": [{"type": "text", "text": "Validation Error: No alignment options provided."}]}

        _batch_update(svc, spreadsheet_id, [{
            "repeatCell": {
                "range": grid_range,
                "cell": {"userEnteredFormat": cell_fmt},
                "fields": ",".join(fields_list),
            }
        }])
        return {"content": [{"type": "text", "text": f"✓ Alignment applied to '{sheet_name}!{range_str}'."}]}
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Validation Error: {e}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


# ---------------------------------------------------------------------------
# ── VISUAL TOOLS ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _parse_a1_to_source_range(svc, spreadsheet_id: str, full_range: str) -> dict:
    """
    Parse 'SheetName!A1:C10' into a Sheets API GridRange dict.
    Uses cache for sheet IDs.
    """
    sheet_name, cell_range = _validate_full_a1_notation(full_range)
    sheet_id = _get_sheet_id_cached(svc, spreadsheet_id, sheet_name)
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
