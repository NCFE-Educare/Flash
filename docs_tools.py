"""Google Docs MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_docs_auth_url / exchange_docs_code) are plain functions
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

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_docs_tokens, get_docs_tokens, save_docs_tokens

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
GOOGLE_DOCS_REDIRECT_URI = os.environ.get(
    "GOOGLE_DOCS_REDIRECT_URI", "http://localhost:8000/auth/docs/callback"
)

DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
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
            "redirect_uris": [GOOGLE_DOCS_REDIRECT_URI],
        }
    }


def get_docs_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Docs access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=DOCS_SCOPES,
        redirect_uri=GOOGLE_DOCS_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "docs"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_docs_code(code: str, state: str) -> dict | None:
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
                scopes=DOCS_SCOPES,
                redirect_uri=GOOGLE_DOCS_REDIRECT_URI,
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

        save_docs_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Docs OAuth ERROR] exchange_docs_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_docs_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Docs is not connected for this account. "
            "Please call GET /auth/docs/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=DOCS_SCOPES,
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
        save_docs_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _docs(user_id: int):
    """Build an authenticated Docs v1 service."""
    return build("docs", "v1", credentials=_get_credentials(user_id))


def _drive(user_id: int):
    """Build an authenticated Drive v3 service."""
    return build("drive", "v3", credentials=_get_credentials(user_id))


def _extract_text_from_document(doc: dict) -> str:
    """Traverse document structure and extract plain text from body content."""
    parts: list[str] = []
    body = doc.get("body") or {}
    content = body.get("content") or []

    for elem in content:
        para = elem.get("paragraph")
        if not para:
            continue
        for el in para.get("elements") or []:
            text_run = el.get("textRun")
            if text_run:
                parts.append(text_run.get("content") or "")

    return "".join(parts)


def _get_body_end_index(doc: dict) -> int:
    """Return the index at the end of the document body (for appending text)."""
    body = doc.get("body") or {}
    content = body.get("content") or []
    if not content:
        return 1
    max_end = 1
    for elem in content:
        end = elem.get("endIndex")
        if end is not None:
            max_end = max(max_end, end)
    return max_end  # Insert at end to append text


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "create_document",
    (
        "Create a new Google Doc with the given title. "
        "Returns document_id, url, and title. user_id: required. title: required."
    ),
    {"user_id": int, "title": str},
)
async def create_document(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        title = str(args["title"])

        drv = _drive(user_id)
        metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        file = drv.files().create(body=metadata, fields="id,name,webViewLink").execute()
        doc_id = file["id"]
        url = file.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "document_id": doc_id,
                    "title": file.get("name", title),
                    "url": url,
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating document: {e}"}]}


@tool(
    "list_documents",
    (
        "List the user's Google Docs from Drive. "
        "max_results: optional int (default 20). Returns id, name, modified, url. user_id: required."
    ),
    {"user_id": int, "max_results": int},
)
async def list_documents(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        max_results = int(args.get("max_results", 20))

        drv = _drive(user_id)
        result = drv.files().list(
            q="mimeType='application/vnd.google-apps.document' and trashed=false",
            spaces="drive",
            fields="files(id,name,createdTime,modifiedTime,webViewLink)",
            pageSize=max_results,
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": "No documents found in Drive."}]}

        docs = [
            {
                "document_id": f["id"],
                "name": f["name"],
                "modified": f.get("modifiedTime", ""),
                "created": f.get("createdTime", ""),
                "url": f.get("webViewLink", ""),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(docs, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing documents: {e}"}]}


@tool(
    "get_document",
    (
        "Read the full content of a Google Doc. Returns title and body text. "
        "user_id: required. document_id: required."
    ),
    {"user_id": int, "document_id": str},
)
async def get_document(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        document_id = str(args["document_id"])

        svc = _docs(user_id)
        doc = svc.documents().get(documentId=document_id).execute()
        title = doc.get("title", "")
        text = _extract_text_from_document(doc)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "document_id": document_id,
                    "title": title,
                    "content": text,
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting document: {e}"}]}


@tool(
    "insert_text",
    (
        "Insert text at a specific index in a Google Doc. "
        "user_id: required. document_id: required. index: int (1-based, start of body). text: required."
    ),
    {"user_id": int, "document_id": str, "index": int, "text": str},
)
async def insert_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        document_id = str(args["document_id"])
        index = int(args["index"])
        text = str(args["text"])

        svc = _docs(user_id)
        requests = [{
            "insertText": {
                "location": {"index": index},
                "text": text,
            }
        }]
        svc.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()
        return {"content": [{"type": "text", "text": f"Inserted {len(text)} characters at index {index}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error inserting text: {e}"}]}


@tool(
    "replace_text",
    (
        "Replace all occurrences of text in a Google Doc. "
        "user_id: required. document_id: required. find_text: required. replace_text: required. match_case: optional bool (default false)."
    ),
    {"user_id": int, "document_id": str, "find_text": str, "replace_text": str, "match_case": bool},
)
async def replace_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        document_id = str(args["document_id"])
        find_text = str(args["find_text"])
        replace_text = str(args["replace_text"])
        match_case = bool(args.get("match_case", False))

        svc = _docs(user_id)
        requests = [{
            "replaceAllText": {
                "containsText": {"text": find_text, "matchCase": match_case},
                "replaceText": replace_text,
            }
        }]
        result = svc.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()
        revs = result.get("replies", [])
        return {"content": [{"type": "text", "text": f"Replaced '{find_text}' with '{replace_text}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error replacing text: {e}"}]}


@tool(
    "append_text",
    (
        "Append text to the end of a Google Doc. "
        "user_id: required. document_id: required. text: required."
    ),
    {"user_id": int, "document_id": str, "text": str},
)
async def append_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        document_id = str(args["document_id"])
        text = str(args["text"])

        svc = _docs(user_id)
        doc = svc.documents().get(documentId=document_id).execute()
        index = _get_body_end_index(doc)

        requests = [{
            "insertText": {
                "location": {"index": index},
                "text": text,
            }
        }]
        svc.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()
        return {"content": [{"type": "text", "text": f"Appended {len(text)} characters to the document."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error appending text: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

docs_server = create_sdk_mcp_server(
    name="docs",
    version="1.0.0",
    tools=[
        create_document,
        list_documents,
        get_document,
        insert_text,
        replace_text,
        append_text,
    ],
)
