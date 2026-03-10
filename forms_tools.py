"""Google Forms MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_forms_auth_url / exchange_forms_code) are plain functions
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
from database import delete_forms_tokens, get_forms_tokens, save_forms_tokens

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
GOOGLE_FORMS_REDIRECT_URI = os.environ.get(
    "GOOGLE_FORMS_REDIRECT_URI", "http://localhost:8000/auth/forms/callback"
)

FORMS_SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
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
            "redirect_uris": [GOOGLE_FORMS_REDIRECT_URI],
        }
    }


def get_forms_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Forms access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=FORMS_SCOPES,
        redirect_uri=GOOGLE_FORMS_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "forms"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_forms_code(code: str, state: str) -> dict | None:
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
                scopes=FORMS_SCOPES,
                redirect_uri=GOOGLE_FORMS_REDIRECT_URI,
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

        save_forms_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Forms OAuth ERROR] exchange_forms_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_forms_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Forms is not connected for this account. "
            "Please call GET /auth/forms/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=FORMS_SCOPES,
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
        save_forms_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _forms(user_id: int):
    """Build an authenticated Forms v1 service."""
    return build("forms", "v1", credentials=_get_credentials(user_id))


def _drive(user_id: int):
    """Build an authenticated Drive v3 service."""
    return build("drive", "v3", credentials=_get_credentials(user_id))


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "create_form",
    (
        "Create a new empty Google Form with the given title. "
        "Returns form_id, form_url, and title. user_id: required. title: required. "
        "document_title: optional (defaults to title)."
    ),
    {"user_id": int, "title": str, "document_title": str},
)
async def create_form(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        title = str(args["title"])
        document_title = str(args.get("document_title", title))

        svc = _forms(user_id)
        body = {
            "info": {
                "title": title,
                "documentTitle": document_title,
            }
        }
        form = svc.forms().create(body=body).execute()
        form_id = form.get("formId", "")
        form_url = f"https://docs.google.com/forms/d/{form_id}/edit"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "form_id": form_id,
                    "title": title,
                    "form_url": form_url,
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating form: {e}"}]}


@tool(
    "list_forms",
    (
        "List the user's Google Forms from Drive. "
        "max_results: optional int (default 20). Returns id, name, modified, url. user_id: required."
    ),
    {"user_id": int, "max_results": int},
)
async def list_forms(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        max_results = int(args.get("max_results", 20))

        drv = _drive(user_id)
        result = drv.files().list(
            q="mimeType='application/vnd.google-apps.form' and trashed=false",
            spaces="drive",
            fields="files(id,name,createdTime,modifiedTime,webViewLink)",
            pageSize=max_results,
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": "No forms found in Drive."}]}

        forms_list = [
            {
                "form_id": f["id"],
                "name": f["name"],
                "modified": f.get("modifiedTime", ""),
                "created": f.get("createdTime", ""),
                "url": f.get("webViewLink", f"https://docs.google.com/forms/d/{f['id']}/edit"),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(forms_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing forms: {e}"}]}


@tool(
    "get_form",
    (
        "Get full form structure including title, description, and all items (questions). "
        "user_id: required. form_id: required."
    ),
    {"user_id": int, "form_id": str},
)
async def get_form(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        form_id = str(args["form_id"])

        svc = _forms(user_id)
        form = svc.forms().get(formId=form_id).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "formId": form.get("formId"),
                    "info": form.get("info", {}),
                    "items": form.get("items", []),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting form: {e}"}]}


@tool(
    "add_question",
    (
        "Add a question to a Google Form. user_id: required. form_id: required. "
        "question_type: 'text' (short answer), 'paragraph' (long text), 'multiple_choice', 'checkbox', 'dropdown', 'linear_scale'. "
        "title: the question text. required: optional bool. index: 0-based position (default 0). "
        "For multiple_choice/checkbox/dropdown: options: comma-separated choices e.g. 'Yes,No,Maybe'. "
        "For linear_scale: low, high (ints, default 1-5), low_label, high_label optional."
    ),
    {"user_id": int, "form_id": str, "question_type": str, "title": str, "required": bool, "index": int, "options": str, "low": int, "high": int, "low_label": str, "high_label": str},
)
async def add_question(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        form_id = str(args["form_id"])
        question_type = str(args.get("question_type", "text")).lower()
        title = str(args["title"])
        required = bool(args.get("required", False))
        index = int(args.get("index", 0))

        item = {"title": title}
        question = {"required": required}

        if question_type == "text":
            question["textQuestion"] = {"paragraph": False}
        elif question_type == "paragraph":
            question["textQuestion"] = {"paragraph": True}
        elif question_type in ("multiple_choice", "checkbox", "dropdown"):
            opts_str = str(args.get("options", ""))
            options = [{"value": o.strip()} for o in opts_str.split(",") if o.strip()] if opts_str else []
            if not options:
                return {"content": [{"type": "text", "text": "multiple_choice/checkbox/dropdown require 'options' (comma-separated)."}]}
            choice_type = "RADIO" if question_type == "multiple_choice" else "CHECKBOX" if question_type == "checkbox" else "DROP_DOWN"
            question["choiceQuestion"] = {"type": choice_type, "options": options}
        elif question_type == "linear_scale":
            low = int(args.get("low", 1))
            high = int(args.get("high", 5))
            scale = {"low": low, "high": high}
            if args.get("low_label"):
                scale["lowLabel"] = str(args["low_label"])
            if args.get("high_label"):
                scale["highLabel"] = str(args["high_label"])
            question["scaleQuestion"] = scale
        else:
            question["textQuestion"] = {"paragraph": False}

        item["questionItem"] = {"question": question}

        svc = _forms(user_id)
        requests = [{"createItem": {"item": item, "location": {"index": index}}}]
        svc.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
        return {"content": [{"type": "text", "text": f"Added {question_type} question at index {index}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding question: {e}"}]}


@tool(
    "update_form_info",
    (
        "Update form title and/or description. user_id: required. form_id: required. "
        "title: optional new title. description: optional new description."
    ),
    {"user_id": int, "form_id": str, "title": str, "description": str},
)
async def update_form_info(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        form_id = str(args["form_id"])
        title = args.get("title")
        description = args.get("description")

        if not title and description is None:
            return {"content": [{"type": "text", "text": "Provide at least title or description."}]}

        info = {}
        if title is not None:
            info["title"] = str(title)
        if description is not None:
            info["description"] = str(description)

        svc = _forms(user_id)
        requests = [{"updateFormInfo": {"info": info, "updateMask": ",".join(info.keys())}}]
        svc.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
        return {"content": [{"type": "text", "text": f"Updated form info: {', '.join(info.keys())}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating form info: {e}"}]}


@tool(
    "delete_form_item",
    (
        "Delete an item (question) from a form by its 0-based index. "
        "user_id: required. form_id: required. index: required (0-based, use get_form to see item order)."
    ),
    {"user_id": int, "form_id": str, "index": int},
)
async def delete_form_item(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        form_id = str(args["form_id"])
        index = int(args["index"])

        svc = _forms(user_id)
        requests = [{"deleteItem": {"location": {"index": index}}}]
        svc.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
        return {"content": [{"type": "text", "text": f"Deleted item at index {index}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting item: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

forms_server = create_sdk_mcp_server(
    name="forms",
    version="1.0.0",
    tools=[
        create_form,
        list_forms,
        get_form,
        add_question,
        update_form_info,
        delete_form_item,
    ],
)
