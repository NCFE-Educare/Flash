"""Google Meet MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_meet_auth_url / exchange_meet_code) are plain functions
called directly by api.py — they are NOT MCP tools.
"""

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_meet_tokens, get_meet_tokens, save_meet_tokens

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
GOOGLE_MEET_REDIRECT_URI = os.environ.get(
    "GOOGLE_MEET_REDIRECT_URI", "http://localhost:8000/auth/meet/callback"
)

MEET_SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/userinfo.email",
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
            "redirect_uris": [GOOGLE_MEET_REDIRECT_URI],
        }
    }


def get_meet_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Meet access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=MEET_SCOPES,
        redirect_uri=GOOGLE_MEET_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "meet"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_meet_code(code: str, state: str) -> dict | None:
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
                scopes=MEET_SCOPES,
                redirect_uri=GOOGLE_MEET_REDIRECT_URI,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Get user's email via userinfo API
        import urllib.request
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        with urllib.request.urlopen(req) as resp:
            userinfo = json.loads(resp.read().decode())
        google_email = userinfo.get("email", "unknown")

        expiry_str = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else datetime.utcnow().isoformat()
        )

        save_meet_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Meet OAuth ERROR] exchange_meet_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_meet_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Meet is not connected for this account. "
            "Please call GET /auth/meet/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=MEET_SCOPES,
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
        save_meet_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _meet_request(user_id: int, method: str, url: str, json_body: dict | None = None) -> dict:
    """Make an authenticated request to the Google Meet API."""
    creds = _get_credentials(user_id)
    session = AuthorizedSession(creds)
    if method == "POST":
        resp = session.post(url, json=json_body or {})
    elif method == "GET":
        resp = session.get(url)
    elif method == "PATCH":
        resp = session.patch(url, json=json_body or {})
    else:
        raise ValueError(f"Unsupported method: {method}")
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "create_meet_space",
    (
        "Create a new Google Meet meeting space. "
        "user_id: required. Returns meeting URI (join link), meeting code, and space name. "
        "Anyone with the link can join the meeting."
    ),
    {"user_id": int},
)
async def create_meet_space(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        result = _meet_request(user_id, "POST", "https://meet.googleapis.com/v2/spaces", {})
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "message": "Google Meet space created successfully.",
                    "meeting_uri": result.get("meetingUri", ""),
                    "meeting_code": result.get("meetingCode", ""),
                    "space_name": result.get("name", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating Meet space: {e}"}]}


@tool(
    "get_meet_space",
    (
        "Get details of a Google Meet space by space name or meeting code. "
        "user_id: required. space_id: required — use 'spaces/XXX' format or meeting code (e.g. abc-defg-hij)."
    ),
    {"user_id": int, "space_id": str},
)
async def get_meet_space(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        space_id = str(args["space_id"]).strip()
        if not space_id.startswith("spaces/"):
            space_id = f"spaces/{space_id}"
        url = f"https://meet.googleapis.com/v2/{space_id}"
        result = _meet_request(user_id, "GET", url)
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "name": result.get("name"),
                    "meeting_uri": result.get("meetingUri"),
                    "meeting_code": result.get("meetingCode"),
                    "config": result.get("config"),
                    "active_conference": result.get("activeConference"),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting Meet space: {e}"}]}


@tool(
    "get_meet_profile",
    (
        "Get the connected Google Meet account's email. "
        "Useful to confirm which account is connected for Meet."
    ),
    {"user_id": int},
)
async def get_meet_profile(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        tokens = get_meet_tokens(user_id)
        if not tokens:
            return {"content": [{"type": "text", "text": "Google Meet is not connected for this account."}]}
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "google_email": tokens.get("google_email"),
                    "connected_at": tokens.get("connected_at"),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting Meet profile: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

meet_tools_server = create_sdk_mcp_server(
    name="meet",
    version="1.0.0",
    tools=[
        create_meet_space,
        get_meet_space,
        get_meet_profile,
    ],
)
