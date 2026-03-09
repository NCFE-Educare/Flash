"""Google Slides MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

Two MCP servers are registered at the bottom:
  slides_data_server   → used by slides_data_agent   (create, list, add slide, insert text…)
  slides_format_server → used by slides_format_agent (text style, bullets, paragraph alignment…)

OAuth helpers (get_slides_auth_url / exchange_slides_code) are plain functions
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
from database import delete_slides_tokens, get_slides_tokens, save_slides_tokens

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
GOOGLE_SLIDES_REDIRECT_URI = os.environ.get(
    "GOOGLE_SLIDES_REDIRECT_URI", "http://localhost:8000/auth/slides/callback"
)

SLIDES_SCOPES = [
    "https://www.googleapis.com/auth/presentations",
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
            "redirect_uris": [GOOGLE_SLIDES_REDIRECT_URI],
        }
    }


def get_slides_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Slides access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SLIDES_SCOPES,
        redirect_uri=GOOGLE_SLIDES_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "slides"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_slides_code(code: str, state: str) -> dict | None:
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
                scopes=SLIDES_SCOPES,
                redirect_uri=GOOGLE_SLIDES_REDIRECT_URI,
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

        save_slides_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Slides OAuth ERROR] exchange_slides_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_slides_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Slides is not connected for this account. "
            "Please call GET /auth/slides/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SLIDES_SCOPES,
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
        save_slides_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _slides(user_id: int):
    """Build an authenticated Slides v1 service."""
    return build("slides", "v1", credentials=_get_credentials(user_id))


def _drive(user_id: int):
    """Build an authenticated Drive v3 service."""
    return build("drive", "v3", credentials=_get_credentials(user_id))


def _hex_to_rgb(hex_color: str) -> dict:
    """Convert hex like #4285F4 to Slides API rgbColor (0-1 floats)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return {"red": r, "green": g, "blue": b}
    return {"red": 0.0, "green": 0.0, "blue": 0.0}


# ---------------------------------------------------------------------------
# Slides Data MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "create_presentation",
    (
        "Create a new Google Slides presentation with the given title. "
        "Returns presentation_id, url, and title. user_id: required. title: required."
    ),
    {"user_id": int, "title": str},
)
async def create_presentation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        title = str(args["title"])

        drv = _drive(user_id)
        metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.presentation",
        }
        file = drv.files().create(body=metadata, fields="id,name,webViewLink").execute()
        pres_id = file["id"]
        url = file.get("webViewLink", f"https://docs.google.com/presentation/d/{pres_id}/edit")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "presentation_id": pres_id,
                    "title": file.get("name", title),
                    "url": url,
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating presentation: {e}"}]}


@tool(
    "list_presentations",
    (
        "List the user's Google Slides presentations from Drive. "
        "max_results: optional int (default 20). Returns id, name, modified, url. user_id: required."
    ),
    {"user_id": int, "max_results": int},
)
async def list_presentations(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        max_results = int(args.get("max_results", 20))

        drv = _drive(user_id)
        result = drv.files().list(
            q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
            spaces="drive",
            fields="files(id,name,createdTime,modifiedTime,webViewLink)",
            pageSize=max_results,
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return {"content": [{"type": "text", "text": "No presentations found in Drive."}]}

        pres_list = [
            {
                "presentation_id": f["id"],
                "name": f["name"],
                "modified": f.get("modifiedTime", ""),
                "created": f.get("createdTime", ""),
                "url": f.get("webViewLink", ""),
            }
            for f in files
        ]
        return {"content": [{"type": "text", "text": json.dumps(pres_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing presentations: {e}"}]}


@tool(
    "get_presentation",
    (
        "Get full presentation structure including slides, shapes, and object IDs. "
        "user_id: required. presentation_id: required."
    ),
    {"user_id": int, "presentation_id": str},
)
async def get_presentation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])

        svc = _slides(user_id)
        pres = svc.presentations().get(presentationId=presentation_id).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "presentation_id": pres.get("presentationId"),
                    "title": pres.get("title", ""),
                    "slides": pres.get("slides", []),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting presentation: {e}"}]}


@tool(
    "get_presentation_info",
    (
        "Get simplified presentation info: slide IDs and shape object IDs for formatting. "
        "Use this before formatting tools to find objectIds. user_id: required. presentation_id: required."
    ),
    {"user_id": int, "presentation_id": str},
)
async def get_presentation_info(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])

        svc = _slides(user_id)
        pres = svc.presentations().get(presentationId=presentation_id).execute()
        slides_data = pres.get("slides", [])
        info = {"presentation_id": presentation_id, "title": pres.get("title", ""), "slides": []}
        for slide in slides_data:
            slide_id = slide.get("objectId", "")
            shapes = []
            for elem in slide.get("pageElements", []):
                obj_id = elem.get("objectId", "")
                shape = elem.get("shape")
                if shape:
                    shapes.append({"objectId": obj_id, "type": "shape"})
            info["slides"].append({"slideId": slide_id, "shapes": shapes})
        return {"content": [{"type": "text", "text": json.dumps(info, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting presentation info: {e}"}]}


@tool(
    "add_slide",
    (
        "Add a new slide to a presentation. Returns the new slide's objectId. "
        "user_id: required. presentation_id: required. "
        "insertion_index: optional int (0-based, default appends at end)."
    ),
    {"user_id": int, "presentation_id": str, "insertion_index": int},
)
async def add_slide(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        insertion_index = int(args.get("insertion_index", -1))

        svc = _slides(user_id)
        req = {"createSlide": {}}
        if insertion_index >= 0:
            req["createSlide"]["insertionIndex"] = insertion_index

        result = svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        replies = result.get("replies", [])
        if replies and "createSlide" in replies[0]:
            obj_id = replies[0]["createSlide"].get("objectId", "")
            return {"content": [{"type": "text", "text": json.dumps({"slide_object_id": obj_id, "message": "Slide added."}, indent=2)}]}
        return {"content": [{"type": "text", "text": "Slide added."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding slide: {e}"}]}


@tool(
    "insert_text",
    (
        "Insert text into a shape at a given index. "
        "object_id: the shape's objectId (from get_presentation or get_presentation_info). "
        "insertion_index: 0-based character index. text: the text to insert. "
        "user_id: required. presentation_id: required. object_id: required. insertion_index: int. text: required."
    ),
    {"user_id": int, "presentation_id": str, "object_id": str, "insertion_index": int, "text": str},
)
async def insert_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        object_id = str(args["object_id"])
        insertion_index = int(args["insertion_index"])
        text = str(args["text"])

        svc = _slides(user_id)
        req = {
            "insertText": {
                "objectId": object_id,
                "insertionIndex": insertion_index,
                "text": text,
            }
        }
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": f"Inserted {len(text)} characters into shape."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error inserting text: {e}"}]}


@tool(
    "replace_all_text",
    (
        "Replace all occurrences of text across the presentation. "
        "user_id: required. presentation_id: required. find_text: required. replace_text: required. match_case: optional bool (default false)."
    ),
    {"user_id": int, "presentation_id": str, "find_text": str, "replace_text": str, "match_case": bool},
)
async def replace_all_text(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        find_text = str(args["find_text"])
        replace_text = str(args["replace_text"])
        match_case = bool(args.get("match_case", False))

        svc = _slides(user_id)
        req = {
            "replaceAllText": {
                "containsText": {"text": find_text, "matchCase": match_case},
                "replaceText": replace_text,
            }
        }
        result = svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": f"Replaced '{find_text}' with '{replace_text}'."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error replacing text: {e}"}]}


@tool(
    "create_text_box",
    (
        "Create a text box shape on a slide. Returns the new shape's objectId. "
        "user_id: required. presentation_id: required. slide_object_id: required. "
        "text: optional initial text. x, y: position in EMU (1 inch = 914400 EMU). "
        "width, height: size in EMU (optional, default 200000 x 100000)."
    ),
    {"user_id": int, "presentation_id": str, "slide_object_id": str, "text": str, "x": int, "y": int, "width": int, "height": int},
)
async def create_text_box(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        slide_object_id = str(args["slide_object_id"])
        text = str(args.get("text", ""))
        x = int(args.get("x", 100000))
        y = int(args.get("y", 100000))
        width = int(args.get("width", 200000))
        height = int(args.get("height", 100000))

        svc = _slides(user_id)
        obj_id = "TextBox_" + uuid.uuid4().hex[:12]
        requests = [
            {
                "createShape": {
                    "objectId": obj_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_object_id,
                        "size": {"width": {"magnitude": width, "unit": "EMU"}, "height": {"magnitude": height, "unit": "EMU"}},
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": x,
                            "translateY": y,
                            "unit": "EMU",
                        },
                    },
                }
            },
            {
                "insertText": {
                    "objectId": obj_id,
                    "insertionIndex": 0,
                    "text": text,
                }
            },
        ]
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()
        return {"content": [{"type": "text", "text": json.dumps({"object_id": obj_id, "message": "Text box created."}, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating text box: {e}"}]}


# ---------------------------------------------------------------------------
# Slides Format MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "update_text_style",
    (
        "Update text style (bold, italic, font, size, color) in a shape. "
        "text_range_type: 'ALL' for entire shape, or 'FIXED_RANGE' with start_index and end_index. "
        "For FIXED_RANGE use 0-based character indices. "
        "Optional: bold, italic (bool), font_family, font_size_pt (float), foreground_color_hex (e.g. '#4285F4'), link_url. "
        "user_id: required. presentation_id: required. object_id: required. text_range_type: str. "
        "start_index, end_index: optional int for FIXED_RANGE."
    ),
    {"user_id": int, "presentation_id": str, "object_id": str, "text_range_type": str, "start_index": int, "end_index": int, "bold": bool, "italic": bool, "font_family": str, "font_size_pt": float, "foreground_color_hex": str, "link_url": str},
)
async def update_text_style(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        object_id = str(args["object_id"])
        text_range_type = str(args.get("text_range_type", "ALL")).upper()
        start_index = args.get("start_index")
        end_index = args.get("end_index")

        text_range = {"type": text_range_type}
        if text_range_type == "FIXED_RANGE" and start_index is not None and end_index is not None:
            text_range["startIndex"] = int(start_index)
            text_range["endIndex"] = int(end_index)

        style = {}
        fields = []
        if "bold" in args and args["bold"] is not None:
            style["bold"] = bool(args["bold"])
            fields.append("bold")
        if "italic" in args and args["italic"] is not None:
            style["italic"] = bool(args["italic"])
            fields.append("italic")
        if args.get("font_family"):
            style["fontFamily"] = str(args["font_family"])
            fields.append("fontFamily")
        if args.get("font_size_pt") is not None:
            style["fontSize"] = {"magnitude": float(args["font_size_pt"]), "unit": "PT"}
            fields.append("fontSize")
        if args.get("foreground_color_hex"):
            rgb = _hex_to_rgb(str(args["foreground_color_hex"]))
            style["foregroundColor"] = {"opaqueColor": {"rgbColor": rgb}}
            fields.append("foregroundColor")
        if args.get("link_url"):
            style["link"] = {"url": str(args["link_url"])}
            fields.append("link")

        if not fields:
            return {"content": [{"type": "text", "text": "No style fields specified. Provide at least one of: bold, italic, font_family, font_size_pt, foreground_color_hex, link_url."}]}

        svc = _slides(user_id)
        req = {
            "updateTextStyle": {
                "objectId": object_id,
                "textRange": text_range,
                "style": style,
                "fields": ",".join(fields),
            }
        }
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": f"Updated text style: {', '.join(fields)}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating text style: {e}"}]}


@tool(
    "create_paragraph_bullets",
    (
        "Add bullets to paragraphs in a shape. "
        "text_range_type: 'ALL' for entire shape. bullet_preset: e.g. 'BULLET_DISC_CIRCLE_SQUARE', 'BULLET_ARROW_DIAMOND_DISC', 'BULLET_CHECKBOX'. "
        "user_id: required. presentation_id: required. object_id: required. text_range_type: str. bullet_preset: optional str."
    ),
    {"user_id": int, "presentation_id": str, "object_id": str, "text_range_type": str, "bullet_preset": str},
)
async def create_paragraph_bullets(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        object_id = str(args["object_id"])
        text_range_type = str(args.get("text_range_type", "ALL")).upper()
        bullet_preset = str(args.get("bullet_preset", "BULLET_DISC_CIRCLE_SQUARE"))

        svc = _slides(user_id)
        req = {
            "createParagraphBullets": {
                "objectId": object_id,
                "textRange": {"type": text_range_type},
                "bulletPreset": bullet_preset,
            }
        }
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": "Added bullets to paragraphs."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error adding bullets: {e}"}]}


@tool(
    "delete_paragraph_bullets",
    (
        "Remove bullets from paragraphs in a shape. text_range_type: 'ALL' for entire shape. "
        "user_id: required. presentation_id: required. object_id: required. text_range_type: str."
    ),
    {"user_id": int, "presentation_id": str, "object_id": str, "text_range_type": str},
)
async def delete_paragraph_bullets(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        object_id = str(args["object_id"])
        text_range_type = str(args.get("text_range_type", "ALL")).upper()

        svc = _slides(user_id)
        req = {
            "deleteParagraphBullets": {
                "objectId": object_id,
                "textRange": {"type": text_range_type},
            }
        }
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": "Removed bullets from paragraphs."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error removing bullets: {e}"}]}


@tool(
    "update_paragraph_style",
    (
        "Update paragraph alignment. alignment: 'START', 'CENTER', 'END', 'JUSTIFIED'. "
        "user_id: required. presentation_id: required. object_id: required. text_range_type: str. alignment: str."
    ),
    {"user_id": int, "presentation_id": str, "object_id": str, "text_range_type": str, "alignment": str},
)
async def update_paragraph_style(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        presentation_id = str(args["presentation_id"])
        object_id = str(args["object_id"])
        text_range_type = str(args.get("text_range_type", "ALL")).upper()
        alignment = str(args.get("alignment", "START")).upper()

        if alignment not in ("START", "CENTER", "END", "JUSTIFIED"):
            return {"content": [{"type": "text", "text": "alignment must be START, CENTER, END, or JUSTIFIED."}]}

        svc = _slides(user_id)
        req = {
            "updateParagraphStyle": {
                "objectId": object_id,
                "textRange": {"type": text_range_type},
                "style": {"alignment": alignment},
                "fields": "alignment",
            }
        }
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [req]},
        ).execute()
        return {"content": [{"type": "text", "text": f"Updated paragraph alignment to {alignment}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating paragraph style: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

slides_data_server = create_sdk_mcp_server(
    name="slides_data",
    version="1.0.0",
    tools=[
        create_presentation,
        list_presentations,
        get_presentation,
        get_presentation_info,
        add_slide,
        insert_text,
        replace_all_text,
        create_text_box,
    ],
)

slides_format_server = create_sdk_mcp_server(
    name="slides_format",
    version="1.0.0",
    tools=[
        get_presentation_info,
        update_text_style,
        create_paragraph_bullets,
        delete_paragraph_bullets,
        update_paragraph_style,
    ],
)
