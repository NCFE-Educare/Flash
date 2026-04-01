"""Google Drive MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_drive_auth_url / exchange_drive_code) are plain functions
called directly by api.py — they are NOT MCP tools.
"""

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_drive_tokens, get_drive_tokens, save_drive_tokens

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_REDIRECT_URI = os.environ.get(
    "GOOGLE_DRIVE_REDIRECT_URI", "http://localhost:8000/auth/drive/callback"
)

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

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
            "redirect_uris": [GOOGLE_DRIVE_REDIRECT_URI],
        }
    }


def get_drive_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Drive access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=DRIVE_SCOPES,
        redirect_uri=GOOGLE_DRIVE_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "drive"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_drive_code(code: str, state: str) -> dict | None:
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
                scopes=DRIVE_SCOPES,
                redirect_uri=GOOGLE_DRIVE_REDIRECT_URI,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        drive_svc = build("drive", "v3", credentials=creds)
        about = drive_svc.about().get(fields="user").execute()
        google_email = about["user"]["emailAddress"]

        expiry_str = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else datetime.utcnow().isoformat()
        )

        save_drive_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Drive OAuth ERROR] exchange_drive_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_drive_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Drive is not connected for this account. "
            "Please call GET /auth/drive/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=DRIVE_SCOPES,
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
        save_drive_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _drive(user_id: int):
    """Build an authenticated Drive v3 service."""
    return build("drive", "v3", credentials=_get_credentials(user_id))


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "search_drive",
    (
        "Search Google Drive by name or full-text. "
        "user_id: required. query: required (e.g. 'report', 'meeting notes'). "
        "search_type: optional - 'name' (name contains query) or 'fulltext' (content search). Default 'name'. "
        "mime_type: optional filter (e.g. 'application/vnd.google-apps.document' for Docs). "
        "max_results: optional int (default 20)."
    ),
    {"user_id": int, "query": str, "search_type": str, "mime_type": str, "max_results": int},
)
async def search_drive(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        query = str(args["query"]).strip()
        search_type = str(args.get("search_type", "name")).lower()
        mime_type = args.get("mime_type")
        max_results = int(args.get("max_results", 20))

        if not query:
            return {"content": [{"type": "text", "text": "Error: query is required."}]}

        drv = _drive(user_id)

        if search_type == "fulltext":
            q_parts = [f"fullText contains '{query.replace(chr(39), chr(39)+chr(39))}'"]
        else:
            q_parts = [f"name contains '{query.replace(chr(39), chr(39)+chr(39))}'"]

        q_parts.append("trashed=false")
        if mime_type:
            q_parts.append(f"mimeType='{mime_type}'")

        q = " and ".join(q_parts)

        result = drv.files().list(
            q=q,
            spaces="drive",
            fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
            pageSize=min(max_results, 100),
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": f"No files found matching '{query}'."}]}

        items = [
            {
                "file_id": f["id"],
                "name": f["name"],
                "mimeType": f.get("mimeType", ""),
                "modified": f.get("modifiedTime", ""),
                "url": f.get("webViewLink", ""),
                "size": f.get("size"),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(items, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error searching Drive: {e}"}]}


@tool(
    "list_drive_files",
    (
        "List files in Google Drive with optional filters. "
        "user_id: required. mime_type: optional (e.g. 'application/vnd.google-apps.document'). "
        "max_results: optional int (default 20). order_by: optional (e.g. 'modifiedTime desc')."
    ),
    {"user_id": int, "mime_type": str, "max_results": int, "order_by": str},
)
async def list_drive_files(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        mime_type = args.get("mime_type")
        max_results = int(args.get("max_results", 20))
        order_by = str(args.get("order_by", "modifiedTime desc"))

        drv = _drive(user_id)
        q = "trashed=false"
        if mime_type:
            q += f" and mimeType='{mime_type}'"

        result = drv.files().list(
            q=q,
            spaces="drive",
            fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
            pageSize=min(max_results, 100),
            orderBy=order_by,
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": "No files found in Drive."}]}

        items = [
            {
                "file_id": f["id"],
                "name": f["name"],
                "mimeType": f.get("mimeType", ""),
                "modified": f.get("modifiedTime", ""),
                "url": f.get("webViewLink", ""),
                "size": f.get("size"),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(items, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing Drive files: {e}"}]}


@tool(
    "get_file_metadata",
    (
        "Get metadata for a specific file in Google Drive. "
        "user_id: required. file_id: required (the Drive file ID)."
    ),
    {"user_id": int, "file_id": str},
)
async def get_file_metadata(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        file_id = str(args["file_id"])

        drv = _drive(user_id)
        f = drv.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,createdTime,webViewLink,webContentLink",
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "file_id": f["id"],
                    "name": f["name"],
                    "mimeType": f.get("mimeType", ""),
                    "size": f.get("size"),
                    "modifiedTime": f.get("modifiedTime", ""),
                    "createdTime": f.get("createdTime", ""),
                    "webViewLink": f.get("webViewLink", ""),
                    "webContentLink": f.get("webContentLink", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting file metadata: {e}"}]}


@tool(
    "create_folder",
    (
        "Create a folder in Google Drive. "
        "user_id: required. name: required. parent_id: optional (folder ID to create inside)."
    ),
    {"user_id": int, "name": str, "parent_id": str},
)
async def create_folder(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        name = str(args["name"])
        parent_id = args.get("parent_id")

        drv = _drive(user_id)
        metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            metadata["parents"] = [parent_id]

        folder = drv.files().create(body=metadata, fields="id,name,webViewLink").execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "folder_id": folder["id"],
                    "name": folder["name"],
                    "url": folder.get("webViewLink", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating folder: {e}"}]}


@tool(
    "create_file_from_text",
    (
        "Create a text file in Google Drive from string content. "
        "user_id: required. content: required. name: required. "
        "mime_type: optional (default text/plain). parent_id: optional folder ID."
    ),
    {"user_id": int, "content": str, "name": str, "mime_type": str, "parent_id": str},
)
async def create_file_from_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        content = str(args["content"])
        name = str(args["name"])
        mime_type = str(args.get("mime_type", "text/plain"))
        parent_id = args.get("parent_id")

        drv = _drive(user_id)
        metadata = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]

        media = MediaIoBaseUpload(
            BytesIO(content.encode("utf-8")),
            mimetype=mime_type,
            resumable=False,
        )
        f = drv.files().create(body=metadata, media_body=media, fields="id,name,webViewLink").execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "file_id": f["id"],
                    "name": f["name"],
                    "url": f.get("webViewLink", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating file: {e}"}]}


@tool(
    "delete_file",
    (
        "Move a file or folder to trash in Google Drive. "
        "user_id: required. file_id: required."
    ),
    {"user_id": int, "file_id": str},
)
async def delete_file(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        file_id = str(args["file_id"])

        drv = _drive(user_id)
        drv.files().update(fileId=file_id, body={"trashed": True}).execute()
        return {"content": [{"type": "text", "text": f"File {file_id} moved to trash."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting file: {e}"}]}


@tool(
    "move_file",
    (
        "Move a file or folder to a different folder in Google Drive. "
        "user_id: required. file_id: required. target_folder_id: required (destination folder ID)."
    ),
    {"user_id": int, "file_id": str, "target_folder_id": str},
)
async def move_file(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        file_id = str(args["file_id"])
        target_folder_id = str(args["target_folder_id"])

        drv = _drive(user_id)
        file = drv.files().get(fileId=file_id, fields="parents").execute()
        previous_parents = ",".join(file.get("parents", []))

        drv.files().update(
            fileId=file_id,
            addParents=target_folder_id,
            removeParents=previous_parents,
        ).execute()
        return {"content": [{"type": "text", "text": f"File moved to folder {target_folder_id}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error moving file: {e}"}]}


@tool(
    "rename_file",
    (
        "Rename a file or folder in Google Drive. "
        "user_id: required. file_id: required. new_name: required."
    ),
    {"user_id": int, "file_id": str, "new_name": str},
)
async def rename_file(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        file_id = str(args["file_id"])
        new_name = str(args["new_name"])

        drv = _drive(user_id)
        drv.files().update(fileId=file_id, body={"name": new_name}).execute()
        return {"content": [{"type": "text", "text": f"File renamed to '{new_name}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error renaming file: {e}"}]}


@tool(
    "share_file",
    (
        "Share a file or folder in Google Drive with another person or make it link-shareable. "
        "user_id: required. file_id: required. "
        "share_with_email: email to share with (for specific person). "
        "role: 'reader' (view only), 'writer' (can edit), or 'commenter' (can comment). Default 'reader'. "
        "share_with_anyone: if true, make link shareable for anyone (omit share_with_email). "
        "send_notification: optional bool (default True) - send email to the person."
    ),
    {"user_id": int, "file_id": str, "share_with_email": str, "role": str, "share_with_anyone": bool, "send_notification": bool},
)
async def share_file(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        file_id = str(args["file_id"])
        share_with_email = args.get("share_with_email")
        role = str(args.get("role", "reader")).lower()
        share_with_anyone = bool(args.get("share_with_anyone", False))
        send_notification = args.get("send_notification", True)

        if role not in ("reader", "writer", "commenter"):
            role = "reader"

        drv = _drive(user_id)

        if share_with_anyone:
            body = {"type": "anyone", "role": role}
            perm = drv.permissions().create(
                fileId=file_id,
                body=body,
                fields="id",
            ).execute()
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "message": "File is now link-shareable.",
                        "role": role,
                        "anyone_with_link": True,
                    }, indent=2),
                }]
            }
        elif share_with_email:
            body = {
                "type": "user",
                "role": role,
                "emailAddress": share_with_email.strip(),
            }
            perm = drv.permissions().create(
                fileId=file_id,
                body=body,
                fields="id",
                sendNotificationEmail=send_notification,
            ).execute()
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "message": f"File shared with {share_with_email} as {role}.",
                        "permission_id": perm.get("id", ""),
                    }, indent=2),
                }]
            }
        else:
            return {"content": [{"type": "text", "text": "Error: provide share_with_email or set share_with_anyone=true."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error sharing file: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

drive_server = create_sdk_mcp_server(
    name="drive",
    version="1.0.0",
    tools=[
        search_drive,
        list_drive_files,
        get_file_metadata,
        create_folder,
        create_file_from_text,
        delete_file,
        move_file,
        rename_file,
        share_file,
    ],
)
