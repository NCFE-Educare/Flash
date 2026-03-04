"""Gmail MCP tools — per-user OAuth 2.0 with tokens stored in SQLite."""

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_gmail_tokens, get_gmail_tokens, save_gmail_tokens

# ---------------------------------------------------------------------------
# Load .env (same pattern as the rest of the project)
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
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/gmail/callback"
)

# Scopes requested from Google:
#   gmail.modify  → read, compose, send, label management, move to trash/spam/inbox
#   settings.basic → view email settings (read-only)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# In-memory store: state string → Flow object
# The Flow holds the PKCE code_verifier generated during authorization_url().
# We reuse the same Flow in exchange_gmail_code so Google accepts the token request.
_pending_flows: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# OAuth helpers (called by api.py endpoints — NOT MCP tools)
# ---------------------------------------------------------------------------

def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def get_gmail_auth_url(user_id: int) -> str:
    """
    Generate the Google OAuth consent-screen URL for a given user.
    The user_id is embedded in the state param (base64-encoded JSON)
    so we know which user to save tokens for when Google calls back.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env"
        )

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )

    # Encode user_id into state so we can retrieve it in the callback
    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",   # always ask consent so we always get a refresh_token
        state=state,
    )

    # Save the flow so exchange_gmail_code can reuse the same PKCE code_verifier
    _pending_flows[state] = flow

    return auth_url


def exchange_gmail_code(code: str, state: str) -> dict | None:
    """
    Exchange the OAuth authorization code for access + refresh tokens.
    Saves the tokens to the database and returns {"email": ..., "user_id": ...}.
    Returns None on failure.
    """
    try:
        # Restore base64 padding that was stripped
        padding = 4 - len(state) % 4
        padded = state + ("=" * padding if padding != 4 else "")
        state_data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        user_id = int(state_data["user_id"])
    except Exception:
        return None

    try:
        # Reuse the original Flow object so the PKCE code_verifier is intact.
        # If the server restarted between connect and callback, fall back to a new flow.
        flow = _pending_flows.pop(state, None)
        if flow is None:
            flow = Flow.from_client_config(
                _client_config(),
                scopes=SCOPES,
                redirect_uri=GOOGLE_REDIRECT_URI,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Fetch the user's Gmail address to store alongside the tokens
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        gmail_email = profile["emailAddress"]

        # Store expiry as naive UTC ISO string (no +00:00) so _get_service
        # can set it back on Credentials without a timezone mismatch.
        if creds.expiry:
            expiry_str = creds.expiry.replace(tzinfo=None).isoformat()
        else:
            expiry_str = datetime.utcnow().isoformat()

        save_gmail_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            gmail_email=gmail_email,
        )
        return {"email": gmail_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Gmail OAuth ERROR] exchange_gmail_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal service builder (used by all MCP tools)
# ---------------------------------------------------------------------------

def _get_service(user_id: int):
    """
    Build an authenticated Gmail API service for the given user.
    Silently refreshes the access token if it has expired.
    Raises ValueError if the user has not connected Gmail yet.
    """
    token_data = get_gmail_tokens(user_id)
    if not token_data:
        raise ValueError(
            f"Gmail is not connected for this account. "
            "Please call GET /auth/gmail/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    # Attach stored expiry so google-auth can determine if a refresh is needed.
    # google-auth internally compares creds.expiry using datetime.utcnow() which
    # is timezone-naive, so creds.expiry MUST also be naive (no tzinfo).
    try:
        expiry = datetime.fromisoformat(token_data["token_expiry"])
        if expiry.tzinfo is not None:
            # Convert to naive UTC by stripping tzinfo
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
        save_gmail_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            gmail_email=token_data["gmail_email"],
        )

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Email body decoder
# ---------------------------------------------------------------------------

def _decode_body(payload: dict) -> str:
    """
    Recursively walk a Gmail API message payload and return plain text.
    Handles simple text/plain, text/html, and multipart/* messages.
    """
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html).strip()

    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text

    return ""


# ---------------------------------------------------------------------------
# MIME message builders
# ---------------------------------------------------------------------------

def _build_raw(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _build_reply_raw(
    to: str, subject: str, body: str, thread_message_id: str
) -> str:
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["In-Reply-To"] = thread_message_id
    msg["References"] = thread_message_id
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "list_emails",
    (
        "List emails from a user's Gmail. "
        "user_id: required — always pass the current user's ID. "
        "max_results: optional int (default 10). "
        "unread_only: optional bool — pass true to show only unread (default false). "
        "label: optional string — INBOX (default), SENT, SPAM, TRASH, or any custom label name."
    ),
    {"user_id": int, "max_results": int, "unread_only": bool, "label": str},
)
async def list_emails(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        max_results = int(args.get("max_results", 10))
        unread_only = _parse_bool(args.get("unread_only", False))
        label = str(args.get("label", "INBOX")).upper()

        service = _get_service(user_id)
        query = "is:unread" if unread_only else ""

        result = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q=query,
            labelIds=[label],
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return {"content": [{"type": "text", "text": "No emails found."}]}

        emails = []
        for msg in messages:
            m = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            hdrs = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            emails.append({
                "id": m["id"],
                "thread_id": m["threadId"],
                "from": hdrs.get("From", ""),
                "to": hdrs.get("To", ""),
                "subject": hdrs.get("Subject", "(no subject)"),
                "date": hdrs.get("Date", ""),
                "snippet": m.get("snippet", ""),
                "unread": "UNREAD" in m.get("labelIds", []),
            })

        return {"content": [{"type": "text", "text": json.dumps(emails, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing emails: {e}"}]}


@tool(
    "get_email",
    (
        "Get the full content (body) of a specific email by its ID. "
        "Use list_emails or search_emails first to obtain the ID. "
        "user_id: required. email_id: required."
    ),
    {"user_id": int, "email_id": str},
)
async def get_email(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_id = str(args["email_id"])

        service = _get_service(user_id)
        m = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()

        hdrs = {h["name"]: h["value"] for h in m["payload"]["headers"]}
        body = _decode_body(m["payload"])

        email = {
            "id": m["id"],
            "thread_id": m["threadId"],
            "from": hdrs.get("From", ""),
            "to": hdrs.get("To", ""),
            "subject": hdrs.get("Subject", "(no subject)"),
            "date": hdrs.get("Date", ""),
            "message_id_header": hdrs.get("Message-ID", ""),
            "labels": m.get("labelIds", []),
            "body": body[:5000] if body else "(empty body)",
        }
        return {"content": [{"type": "text", "text": json.dumps(email, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting email: {e}"}]}


@tool(
    "search_emails",
    (
        "Search emails using Gmail search syntax. "
        "Examples: 'from:boss@company.com', 'subject:invoice', 'has:attachment', "
        "'after:2024/01/01', 'is:unread', 'label:important'. "
        "user_id: required. query: required. max_results: optional (default 10)."
    ),
    {"user_id": int, "query": str, "max_results": int},
)
async def search_emails(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        query = str(args["query"])
        max_results = int(args.get("max_results", 10))

        service = _get_service(user_id)
        result = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return {"content": [{"type": "text", "text": f"No emails found for: '{query}'"}]}

        emails = []
        for msg in messages:
            m = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            hdrs = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            emails.append({
                "id": m["id"],
                "thread_id": m["threadId"],
                "from": hdrs.get("From", ""),
                "to": hdrs.get("To", ""),
                "subject": hdrs.get("Subject", "(no subject)"),
                "date": hdrs.get("Date", ""),
                "snippet": m.get("snippet", ""),
                "unread": "UNREAD" in m.get("labelIds", []),
            })

        return {"content": [{"type": "text", "text": json.dumps(emails, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error searching emails: {e}"}]}


@tool(
    "send_email",
    (
        "Compose and immediately send an email from the user's Gmail account. "
        "user_id: required. to: required (recipient email address). "
        "subject: required. body: required (plain text)."
    ),
    {"user_id": int, "to": str, "subject": str, "body": str},
)
async def send_email(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        to = str(args["to"])
        subject = str(args["subject"])
        body = str(args["body"])

        service = _get_service(user_id)
        raw = _build_raw(to=to, subject=subject, body=body)
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Email sent successfully to {to}! Message ID: {result['id']}",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error sending email: {e}"}]}


@tool(
    "create_draft",
    (
        "Save an email as a draft without sending it. "
        "Good for when the user wants to review before sending. "
        "user_id: required. to: required. subject: required. body: required."
    ),
    {"user_id": int, "to": str, "subject": str, "body": str},
)
async def create_draft(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        to = str(args["to"])
        subject = str(args["subject"])
        body = str(args["body"])

        service = _get_service(user_id)
        raw = _build_raw(to=to, subject=subject, body=body)
        result = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Draft saved successfully! Draft ID: {result['id']}. The email has NOT been sent yet.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating draft: {e}"}]}


@tool(
    "reply_to_email",
    (
        "Reply to an existing email, keeping it in the same thread. "
        "Use get_email first to get the email_id, thread_id, and message_id_header. "
        "user_id: required. email_id: required. thread_id: required. body: required."
    ),
    {"user_id": int, "email_id": str, "thread_id": str, "body": str},
)
async def reply_to_email(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_id = str(args["email_id"])
        thread_id = str(args["thread_id"])
        body = str(args["body"])

        service = _get_service(user_id)

        # Fetch original to get From / Subject / Message-ID headers for proper threading
        original = service.users().messages().get(
            userId="me",
            id=email_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Message-ID"],
        ).execute()
        hdrs = {h["name"]: h["value"] for h in original["payload"]["headers"]}

        raw = _build_reply_raw(
            to=hdrs.get("From", ""),
            subject=hdrs.get("Subject", ""),
            body=body,
            thread_message_id=hdrs.get("Message-ID", email_id),
        )
        result = service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Reply sent to {hdrs.get('From', '')}! Message ID: {result['id']}",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error replying to email: {e}"}]}


@tool(
    "mark_as_read",
    (
        "Mark one or more emails as read or unread. "
        "email_ids: required — comma-separated list of message IDs. "
        "mark_as: optional — 'read' (default) or 'unread'. "
        "user_id: required."
    ),
    {"user_id": int, "email_ids": str, "mark_as": str},
)
async def mark_as_read(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_ids = [e.strip() for e in str(args["email_ids"]).split(",") if e.strip()]
        mark_as = str(args.get("mark_as", "read")).lower()

        service = _get_service(user_id)
        add_labels = [] if mark_as == "read" else ["UNREAD"]
        remove_labels = ["UNREAD"] if mark_as == "read" else []

        for eid in email_ids:
            service.users().messages().modify(
                userId="me",
                id=eid,
                body={"addLabelIds": add_labels, "removeLabelIds": remove_labels},
            ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Marked {len(email_ids)} email(s) as {mark_as}.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error marking emails: {e}"}]}


@tool(
    "move_to_trash",
    (
        "Move one or more emails to trash. "
        "email_ids: required — comma-separated list of message IDs. "
        "user_id: required."
    ),
    {"user_id": int, "email_ids": str},
)
async def move_to_trash(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_ids = [e.strip() for e in str(args["email_ids"]).split(",") if e.strip()]

        service = _get_service(user_id)
        for eid in email_ids:
            service.users().messages().trash(userId="me", id=eid).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Moved {len(email_ids)} email(s) to trash.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error moving to trash: {e}"}]}


@tool(
    "archive_email",
    (
        "Archive one or more emails — removes from Inbox but keeps in All Mail. "
        "email_ids: required — comma-separated list of message IDs. "
        "user_id: required."
    ),
    {"user_id": int, "email_ids": str},
)
async def archive_email(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_ids = [e.strip() for e in str(args["email_ids"]).split(",") if e.strip()]

        service = _get_service(user_id)
        for eid in email_ids:
            service.users().messages().modify(
                userId="me", id=eid, body={"removeLabelIds": ["INBOX"]}
            ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Archived {len(email_ids)} email(s).",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error archiving emails: {e}"}]}


@tool(
    "add_label",
    (
        "Add a label to an email. Use list_labels to see available label IDs. "
        "user_id: required. email_id: required. label_id: required (e.g. 'IMPORTANT' or a custom label ID)."
    ),
    {"user_id": int, "email_id": str, "label_id": str},
)
async def add_label(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        email_id = str(args["email_id"])
        label_id = str(args["label_id"])

        service = _get_service(user_id)
        service.users().messages().modify(
            userId="me", id=email_id, body={"addLabelIds": [label_id]}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": f"Label '{label_id}' added to email {email_id}.",
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding label: {e}"}]}


@tool(
    "list_labels",
    (
        "List all Gmail labels for the user (system labels like INBOX, SENT, "
        "SPAM, TRASH, and any custom labels the user has created). "
        "Use the returned IDs when calling add_label. "
        "user_id: required."
    ),
    {"user_id": int},
)
async def list_labels(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        service = _get_service(user_id)
        result = service.users().labels().list(userId="me").execute()
        labels = [
            {"id": lb["id"], "name": lb["name"]}
            for lb in result.get("labels", [])
        ]
        return {"content": [{"type": "text", "text": json.dumps(labels, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing labels: {e}"}]}


@tool(
    "get_gmail_profile",
    (
        "Get the connected Gmail account's email address, total messages, "
        "and threads count. Useful to confirm which Gmail account is connected. "
        "user_id: required."
    ),
    {"user_id": int},
)
async def get_gmail_profile(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        service = _get_service(user_id)
        profile = service.users().getProfile(userId="me").execute()
        return {"content": [{"type": "text", "text": json.dumps(profile, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting Gmail profile: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server — register all tools here
# ---------------------------------------------------------------------------

gmail_tools_server = create_sdk_mcp_server(
    name="gmail_tools",
    version="1.0.0",
    tools=[
        list_emails,
        get_email,
        search_emails,
        send_email,
        create_draft,
        reply_to_email,
        mark_as_read,
        move_to_trash,
        archive_email,
        add_label,
        list_labels,
        get_gmail_profile,
    ],
)
