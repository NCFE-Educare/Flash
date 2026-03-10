"""Google Calendar MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_calendar_auth_url / exchange_calendar_code) are plain functions
called directly by api.py — they are NOT MCP tools.
"""

import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_calendar_tokens, get_calendar_tokens, save_calendar_tokens

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
GOOGLE_CALENDAR_REDIRECT_URI = os.environ.get(
    "GOOGLE_CALENDAR_REDIRECT_URI", "http://localhost:8000/auth/calendar/callback"
)

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
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
            "redirect_uris": [GOOGLE_CALENDAR_REDIRECT_URI],
        }
    }


def get_calendar_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Calendar access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=CALENDAR_SCOPES,
        redirect_uri=GOOGLE_CALENDAR_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "calendar"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_calendar_code(code: str, state: str) -> dict | None:
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
                scopes=CALENDAR_SCOPES,
                redirect_uri=GOOGLE_CALENDAR_REDIRECT_URI,
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

        save_calendar_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Calendar OAuth ERROR] exchange_calendar_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_calendar_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Calendar is not connected for this account. "
            "Please call GET /auth/calendar/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=CALENDAR_SCOPES,
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
        save_calendar_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _calendar(user_id: int):
    """Build an authenticated Calendar v3 service."""
    return build("calendar", "v3", credentials=_get_credentials(user_id))


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "list_calendars",
    (
        "List all calendars the user has access to. "
        "user_id: required. Returns calendar id, summary, primary flag."
    ),
    {"user_id": int},
)
async def list_calendars(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        cal = _calendar(user_id)
        result = cal.calendarList().list().execute()
        items = result.get("items", [])
        if not items:
            return {"content": [{"type": "text", "text": "No calendars found."}]}
        out = [
            {
                "id": c.get("id", ""),
                "summary": c.get("summary", ""),
                "primary": c.get("primary", False),
            }
            for c in items
        ]
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing calendars: {e}"}]}


@tool(
    "list_events",
    (
        "List events from a calendar. "
        "user_id: required. calendar_id: optional (default 'primary'). "
        "time_min: optional ISO datetime (e.g. 2025-03-09T00:00:00Z). "
        "time_max: optional ISO datetime. max_results: optional int (default 20). "
        "order_by: optional 'startTime' or 'updated'."
    ),
    {"user_id": int, "calendar_id": str, "time_min": str, "time_max": str, "max_results": int, "order_by": str},
)
async def list_events(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        calendar_id = str(args.get("calendar_id", "primary"))
        time_min = args.get("time_min")
        time_max = args.get("time_max")
        max_results = int(args.get("max_results", 20))
        order_by = str(args.get("order_by", "startTime"))

        cal = _calendar(user_id)
        req_params = {
            "calendarId": calendar_id,
            "maxResults": min(max_results, 250),
            "singleEvents": True,
        }
        if time_min:
            req_params["timeMin"] = time_min
        if time_max:
            req_params["timeMax"] = time_max
        if order_by in ("startTime", "updated"):
            req_params["orderBy"] = order_by

        result = cal.events().list(**req_params).execute()
        events = result.get("items", [])
        if not events:
            return {"content": [{"type": "text", "text": "No events found."}]}

        out = []
        for e in events:
            start = e.get("start", {}) or {}
            end = e.get("end", {}) or {}
            out.append({
                "id": e.get("id", ""),
                "summary": e.get("summary", "(No title)"),
                "start": start.get("dateTime") or start.get("date", ""),
                "end": end.get("dateTime") or end.get("date", ""),
                "description": (e.get("description") or "")[:200],
                "location": e.get("location", ""),
            })
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing events: {e}"}]}


@tool(
    "get_event",
    (
        "Get a single event by ID. "
        "user_id: required. event_id: required. calendar_id: optional (default 'primary')."
    ),
    {"user_id": int, "event_id": str, "calendar_id": str},
)
async def get_event(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        event_id = str(args["event_id"])
        calendar_id = str(args.get("calendar_id", "primary"))

        cal = _calendar(user_id)
        event = cal.events().get(calendarId=calendar_id, eventId=event_id).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "description": event.get("description"),
                    "location": event.get("location"),
                    "start": event.get("start"),
                    "end": event.get("end"),
                    "htmlLink": event.get("htmlLink"),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting event: {e}"}]}


@tool(
    "create_event",
    (
        "Create a new calendar event. "
        "user_id: required. summary: required (event title). "
        "start_datetime: required ISO string (e.g. 2025-03-10T14:00:00). "
        "end_datetime: required ISO string. "
        "description: optional. location: optional. "
        "calendar_id: optional (default 'primary'). time_zone: optional (default Asia/Kolkata). "
        "all_day: optional bool - if true, use date only (YYYY-MM-DD) for start/end. "
        "add_google_meet: optional bool - if true, automatically generates a Google Meet link and attaches it to the event. "
        "attendees: optional comma-separated list of email addresses to invite (e.g. 'a@b.com,c@d.com'). "
        "Google Calendar will send invite emails to all attendees automatically."
    ),
    {
        "user_id": int, "summary": str, "start_datetime": str, "end_datetime": str,
        "description": str, "location": str, "calendar_id": str, "time_zone": str,
        "all_day": bool, "add_google_meet": bool, "attendees": str,
    },
)
async def create_event(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        summary = str(args["summary"])
        start_datetime = str(args["start_datetime"])
        end_datetime = str(args["end_datetime"])
        description = args.get("description", "")
        location = args.get("location", "")
        calendar_id = str(args.get("calendar_id", "primary"))
        time_zone = str(args.get("time_zone", "Asia/Kolkata"))
        all_day = bool(args.get("all_day", False))
        add_google_meet = bool(args.get("add_google_meet", False))
        attendees_raw = args.get("attendees", "") or ""

        if not summary:
            return {"content": [{"type": "text", "text": "Error: summary is required."}]}

        if all_day:
            event_body = {
                "summary": summary,
                "description": description or None,
                "location": location or None,
                "start": {"date": start_datetime[:10]},
                "end": {"date": end_datetime[:10]},
            }
        else:
            event_body = {
                "summary": summary,
                "description": description or None,
                "location": location or None,
                "start": {"dateTime": start_datetime, "timeZone": time_zone},
                "end": {"dateTime": end_datetime, "timeZone": time_zone},
            }

        # Add attendees if provided
        attendee_emails = [e.strip() for e in attendees_raw.split(",") if e.strip()]
        if attendee_emails:
            event_body["attendees"] = [{"email": e} for e in attendee_emails]

        # Add Google Meet conference link if requested
        if add_google_meet:
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        cal = _calendar(user_id)
        # conferenceDataVersion=1 is required to generate Meet links
        # sendUpdates="all" sends invite emails to all attendees
        send_updates = "all" if attendee_emails else "none"
        event = cal.events().insert(
            calendarId=calendar_id,
            body=event_body,
            conferenceDataVersion=1 if add_google_meet else 0,
            sendUpdates=send_updates,
        ).execute()

        # Extract Meet link from response if it was created
        meet_link = None
        conference = event.get("conferenceData", {})
        entry_points = conference.get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break

        result = {
            "message": "Event created successfully.",
            "event_id": event.get("id"),
            "htmlLink": event.get("htmlLink"),
            "summary": event.get("summary"),
        }
        if meet_link:
            result["google_meet_link"] = meet_link
        if attendee_emails:
            result["invites_sent_to"] = attendee_emails

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating event: {e}"}]}


@tool(
    "update_event",
    (
        "Update an existing calendar event. "
        "user_id: required. event_id: required. "
        "summary: optional new title. start_datetime: optional. end_datetime: optional. "
        "description: optional. location: optional. calendar_id: optional (default 'primary'). "
        "time_zone: optional (default Asia/Kolkata). all_day: optional bool. "
        "add_google_meet: optional bool - if true, generates and attaches a Google Meet link to the event. "
        "attendees: optional comma-separated email list to add/replace guests (e.g. 'a@b.com,c@d.com'). "
        "send_updates: optional - 'all' to email all guests (default when attendees given), 'none' to skip emails."
    ),
    {
        "user_id": int, "event_id": str, "summary": str, "start_datetime": str, "end_datetime": str,
        "description": str, "location": str, "calendar_id": str, "time_zone": str,
        "all_day": bool, "add_google_meet": bool, "attendees": str, "send_updates": str,
    },
)
async def update_event(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        event_id = str(args["event_id"])
        calendar_id = str(args.get("calendar_id", "primary"))
        time_zone = str(args.get("time_zone", "Asia/Kolkata"))
        all_day = bool(args.get("all_day", False))
        add_google_meet = bool(args.get("add_google_meet", False))
        attendees_raw = args.get("attendees", "") or ""
        send_updates_param = str(args.get("send_updates", "")) or None

        cal = _calendar(user_id)
        existing = cal.events().get(calendarId=calendar_id, eventId=event_id).execute()

        if "summary" in args and args["summary"] is not None:
            existing["summary"] = str(args["summary"])
        if "description" in args and args["description"] is not None:
            existing["description"] = str(args["description"])
        if "location" in args and args["location"] is not None:
            existing["location"] = str(args["location"])
        if "start_datetime" in args and args["start_datetime"]:
            if all_day:
                existing["start"] = {"date": str(args["start_datetime"])[:10]}
            else:
                existing["start"] = {"dateTime": str(args["start_datetime"]), "timeZone": time_zone}
        if "end_datetime" in args and args["end_datetime"]:
            if all_day:
                existing["end"] = {"date": str(args["end_datetime"])[:10]}
            else:
                existing["end"] = {"dateTime": str(args["end_datetime"]), "timeZone": time_zone}

        # Add/replace attendees
        attendee_emails = [e.strip() for e in attendees_raw.split(",") if e.strip()]
        if attendee_emails:
            existing_attendees = existing.get("attendees", []) or []
            existing_emails = {a.get("email", "") for a in existing_attendees}
            for email in attendee_emails:
                if email not in existing_emails:
                    existing_attendees.append({"email": email})
            existing["attendees"] = existing_attendees

        # Add Google Meet conference link if requested and not already present
        if add_google_meet and not existing.get("conferenceData"):
            existing["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        # Determine send_updates
        if send_updates_param in ("all", "externalOnly", "none"):
            send_updates = send_updates_param
        else:
            send_updates = "all" if attendee_emails else "none"

        event = cal.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=existing,
            conferenceDataVersion=1 if add_google_meet else 0,
            sendUpdates=send_updates,
        ).execute()

        # Extract Meet link
        meet_link = None
        for ep in (event.get("conferenceData", {}) or {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break

        result = {
            "message": "Event updated successfully.",
            "event_id": event.get("id"),
            "htmlLink": event.get("htmlLink"),
        }
        if meet_link:
            result["google_meet_link"] = meet_link
        if attendee_emails:
            result["invites_sent_to"] = attendee_emails

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating event: {e}"}]}


@tool(
    "delete_event",
    (
        "Delete a calendar event. "
        "user_id: required. event_id: required. calendar_id: optional (default 'primary')."
    ),
    {"user_id": int, "event_id": str, "calendar_id": str},
)
async def delete_event(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        event_id = str(args["event_id"])
        calendar_id = str(args.get("calendar_id", "primary"))

        cal = _calendar(user_id)
        cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"content": [{"type": "text", "text": f"Event {event_id} deleted successfully."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting event: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

calendar_tools_server = create_sdk_mcp_server(
    name="calendar",
    version="1.0.0",
    tools=[
        list_calendars,
        list_events,
        get_event,
        create_event,
        update_event,
        delete_event,
    ],
)
