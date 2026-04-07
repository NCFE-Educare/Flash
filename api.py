"""FastAPI app with signup, login, sessions, protected chat, and Gmail OAuth."""

import asyncio
import concurrent.futures
import json
import os
import shutil
import sys
import threading
import traceback
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from google.oauth2 import service_account
from googleapiclient.discovery import build
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from auth import create_access_token, decode_access_token, hash_password, verify_password
from database import (
    _get_conn,
    _rows,
    add_message,
    create_session,
    create_user,
    delete_calendar_tokens,
    delete_docs_tokens,
    delete_drive_tokens,
    delete_gmail_tokens,
    delete_meet_tokens,
    delete_reminder,
    delete_session,
    delete_sheets_tokens,
    delete_slides_tokens,
    delete_forms_tokens,
    get_calendar_tokens,
    get_docs_tokens,
    get_drive_tokens,
    get_due_reminders,
    get_gmail_tokens,
    get_meet_tokens,
    get_messages,
    get_pending_reminders_for_user,
    get_reminders_for_user,
    get_session,
    get_sessions_for_user,
    get_sheets_tokens,
    get_slides_tokens,
    get_forms_tokens,
    get_user_by_email,
    get_user_by_id,
    get_session_by_title,
    init_db,
    mark_reminder_delivered,
    rename_session,
    save_claude_session_id,
    get_or_create_gchat_session,
    # Kanban imports
    create_workspace,
    get_workspace,
    get_user_workspaces,
    update_workspace,
    delete_workspace,
    is_workspace_member,
    is_workspace_owner,
    get_workspace_members,
    add_workspace_member,
    remove_workspace_member,
    create_workspace_invitation,
    get_invitation_by_token,
    accept_invitation,
    decline_invitation,
    get_pending_invitations_by_email,
    get_workspace_columns,
    create_column,
    update_column,
    delete_column,
    create_task,
    get_task,
    get_workspace_tasks,
    update_task,
    delete_task,
    create_task_comment,
    get_task_comments,
    delete_task_comment,
    create_task_attachment,
    get_task_attachments,
    delete_task_attachment,
    get_workspace_activity,
    get_workspace_analytics,
    get_user_analytics,
    get_global_analytics,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="EduCare Bots API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Uploaded images are stored in cwd/uploads/ so the agent can read them too
UPLOADS_DIR = Path(__file__).parent / "cwd" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

ALLOWED_DOC_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "text/plain",
}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".pptx", ".ppt", ".txt"}
MAX_DOC_SIZE_MB = 20
MAX_DOCS_PER_UPLOAD = 5
MAX_EXTRACTED_TEXT_CHARS = 100_000

# ---------------------------------------------------------------------------
# Response-done notification channel (SSE)
# ---------------------------------------------------------------------------
# When an agent response completes (streaming or non-streaming), we broadcast
# to all connected /chat/notifications clients for that user so the frontend
# can refresh the session even if the user switched away.
_notification_listeners: dict[int, list[Queue]] = {}
_notification_lock = threading.Lock()


def _broadcast_response_done(user_id: int, session_id: int) -> None:
    """Thread-safe: notify all connected clients that a response is done for this session."""
    event = {"event": "response_done", "type": "response_done", "session_id": session_id}
    with _notification_lock:
        listeners = _notification_listeners.get(user_id, [])
    for q in listeners:
        try:
            q.put(event)
        except Exception:
            pass


def _broadcast_reminder(user_id: int, reminder: dict) -> None:
    """Thread-safe: push a reminder to all connected clients for this user."""
    event = {
        "event": "reminder",
        "type": "reminder",
        "reminder_id": reminder["id"],
        "message": reminder["message"],
        "remind_at": reminder["remind_at"],
    }
    with _notification_lock:
        listeners = _notification_listeners.get(user_id, [])
    for q in listeners:
        try:
            q.put(event)
        except Exception:
            pass


def _run_reminder_worker() -> None:
    """Check for due reminders and push them to connected clients."""
    due = get_due_reminders()
    for r in due:
        user_id = r["user_id"]
        if mark_reminder_delivered(r["id"]):
            _broadcast_reminder(user_id, r)


@app.on_event("startup")
def on_startup():
    init_db()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(_run_reminder_worker, "interval", minutes=1, id="reminder_worker")
    scheduler.start()


# ---------------------------------------------------------------------------
# Google Chat Service & Webhook Helpers
# ---------------------------------------------------------------------------

def get_gchat_service():
    """Initialize Google Chat API client using service account credentials."""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            'service-account.json',
            scopes=['https://www.googleapis.com/auth/chat.bot']
        )
        return build('chat', 'v1', credentials=credentials)
    except FileNotFoundError:
        print("[GChat] service-account.json not found — bot cannot send messages")
        return None
    except Exception as e:
        print(f"[GChat] Error initializing service: {e}")
        return None


def send_gchat_message(space_name: str, text: str) -> bool:
    """Send a message to a Google Chat space using the service account bot."""
    service = get_gchat_service()
    if not service:
        print("[GChat] Service not initialized — cannot send message")
        return False
    try:
        service.spaces().messages().create(
            parent=space_name,
            body={"text": text}
        ).execute()
        print(f"[GChat] Message sent to {space_name}")
        return True
    except Exception as e:
        print(f"[GChat] Failed to send message: {e}")
        return False


def get_or_create_gchat_session(space_name: str, sender_email: str, is_group: bool = False) -> dict | None:
    """Get or create a session for a Google Chat space."""
    user = get_user_by_email(sender_email)
    if not user:
        print(f"[GChat] User {sender_email} not found in DB")
        return None  # User must log in first to create account

    # Map spaces to session titles for isolation
    session_title = f"GChat: {space_name}"
    
    # For DMs, we tie it to the user. For groups, user_id is NULL.
    user_id = user["id"] if not is_group else None
    
    session = get_session_by_title(session_title, user_id)
    if session:
        return session
    
    # Create new session
    return create_session(
        user_id=user_id,
        title=session_title
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SessionCreateRequest(BaseModel):
    title: str = "New Chat"


class SessionRenameRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None   # omit to auto-create a new session
    image_urls: list[str] = []      # list of relative URLs returned by POST /upload
    document_urls: list[str] = []   # list of relative URLs returned by POST /upload/document


class ChatResponse(BaseModel):
    reply: str
    user: str
    session_id: int
    image_urls: list[str] = []
    document_urls: list[str] = []


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def get_current_user_or_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    token: Annotated[str | None, Query(alias="token")] = None,
) -> dict:
    """Auth for SSE: accepts Bearer header or ?token= query param (EventSource can't send headers)."""
    auth_token = credentials.credentials if credentials else token
    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials (use Authorization: Bearer or ?token=)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(auth_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/signup", response_model=TokenResponse, status_code=201, tags=["Auth"])
def signup(body: SignupRequest):
    """Register a new user and return a JWT."""
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    hashed = hash_password(body.password)
    user = create_user(email=body.email, username=body.username, hashed_password=hashed)

    if user is None:
        raise HTTPException(status_code=409, detail="Email or username already registered")

    token = create_access_token({"sub": str(user["id"]), "username": user["username"]})
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse, tags=["Auth"])
def login(body: LoginRequest):
    """Authenticate with email + password and return a JWT."""
    user = get_user_by_email(body.email)

    if user is None or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token({"sub": str(user["id"]), "username": user["username"]})
    return TokenResponse(access_token=token)


@app.get("/auth/me", tags=["Auth"])
def me(current_user: Annotated[dict, Depends(get_current_user)]):
    """Return the currently authenticated user's info."""
    return {"user_id": current_user["sub"], "username": current_user["username"]}


# ---------------------------------------------------------------------------
# Gmail OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/gmail/connect", tags=["Gmail"])
def gmail_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for the logged-in user.
    The frontend should open this URL in a browser/popup so the user can
    grant Gmail access. After approval Google redirects to /auth/gmail/callback.
    """
    from gmail_tools import get_gmail_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_gmail_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/gmail/callback", response_class=HTMLResponse, tags=["Gmail"])
def gmail_callback(code: str, state: str):
    """
    Google redirects the user's browser here after they approve (or deny) access.
    We exchange the code for tokens, save them, and show a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from gmail_tools import exchange_gmail_code
    result = exchange_gmail_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Gmail Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Gmail Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Gmail account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/gmail/status", tags=["Gmail"])
def gmail_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Gmail account."""
    user_id = int(current_user["sub"])
    tokens = get_gmail_tokens(user_id)
    return {
        "connected": tokens is not None,
        "gmail_email": tokens["gmail_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/gmail/disconnect", status_code=204, tags=["Gmail"])
def gmail_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Gmail tokens, disconnecting Gmail for this user."""
    user_id = int(current_user["sub"])
    delete_gmail_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Sheets OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/sheets/connect", tags=["Sheets"])
def sheets_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Sheets + Drive access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/sheets/callback.
    """
    from sheets_tools import get_sheets_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_sheets_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/sheets/callback", response_class=HTMLResponse, tags=["Sheets"])
def sheets_callback(code: str, state: str):
    """
    Google redirects here after the user approves Sheets access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from sheets_tools import exchange_sheets_code
    result = exchange_sheets_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Sheets Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Sheets Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Sheets account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/sheets/status", tags=["Sheets"])
def sheets_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Sheets account."""
    user_id = int(current_user["sub"])
    tokens = get_sheets_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/sheets/disconnect", status_code=204, tags=["Sheets"])
def sheets_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Sheets tokens, disconnecting Google Sheets for this user."""
    user_id = int(current_user["sub"])
    delete_sheets_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Docs OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/docs/connect", tags=["Docs"])
def docs_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Docs + Drive access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/docs/callback.
    """
    from docs_tools import get_docs_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_docs_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/docs/callback", response_class=HTMLResponse, tags=["Docs"])
def docs_callback(code: str, state: str):
    """
    Google redirects here after the user approves Docs access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from docs_tools import exchange_docs_code
    result = exchange_docs_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Docs Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Docs Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Docs account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/docs/status", tags=["Docs"])
def docs_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Docs account."""
    user_id = int(current_user["sub"])
    tokens = get_docs_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/docs/disconnect", status_code=204, tags=["Docs"])
def docs_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Docs tokens, disconnecting Google Docs for this user."""
    user_id = int(current_user["sub"])
    delete_docs_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Drive OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/drive/connect", tags=["Drive"])
def drive_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Drive access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/drive/callback.
    """
    from drive_tools import get_drive_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_drive_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/drive/callback", response_class=HTMLResponse, tags=["Drive"])
def drive_callback(code: str, state: str):
    """
    Google redirects here after the user approves Drive access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from drive_tools import exchange_drive_code
    result = exchange_drive_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Drive Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Drive Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Drive account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/drive/status", tags=["Drive"])
def drive_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Drive account."""
    user_id = int(current_user["sub"])
    tokens = get_drive_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/drive/disconnect", status_code=204, tags=["Drive"])
def drive_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Drive tokens, disconnecting Google Drive for this user."""
    user_id = int(current_user["sub"])
    delete_drive_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Calendar OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/calendar/connect", tags=["Calendar"])
def calendar_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Calendar access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/calendar/callback.
    """
    from calendar_tools import get_calendar_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_calendar_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/calendar/callback", response_class=HTMLResponse, tags=["Calendar"])
def calendar_callback(code: str, state: str):
    """
    Google redirects here after the user approves Calendar access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from calendar_tools import exchange_calendar_code
    result = exchange_calendar_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Calendar Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Calendar Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Calendar account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/calendar/status", tags=["Calendar"])
def calendar_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Calendar account."""
    user_id = int(current_user["sub"])
    tokens = get_calendar_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/calendar/disconnect", status_code=204, tags=["Calendar"])
def calendar_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Calendar tokens, disconnecting Google Calendar for this user."""
    user_id = int(current_user["sub"])
    delete_calendar_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Meet OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/meet/connect", tags=["Meet"])
def meet_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Meet access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/meet/callback.
    """
    from meet_tools import get_meet_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_meet_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/meet/callback", response_class=HTMLResponse, tags=["Meet"])
def meet_callback(code: str, state: str):
    """
    Google redirects here after the user approves Meet access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from meet_tools import exchange_meet_code
    result = exchange_meet_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Meet Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Meet Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Meet account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/meet/status", tags=["Meet"])
def meet_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Meet account."""
    user_id = int(current_user["sub"])
    tokens = get_meet_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/meet/disconnect", status_code=204, tags=["Meet"])
def meet_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Meet tokens, disconnecting Google Meet for this user."""
    user_id = int(current_user["sub"])
    delete_meet_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Slides OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/slides/connect", tags=["Slides"])
def slides_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Slides + Drive access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/slides/callback.
    """
    from slides_tools import get_slides_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_slides_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/slides/callback", response_class=HTMLResponse, tags=["Slides"])
def slides_callback(code: str, state: str):
    """
    Google redirects here after the user approves Slides access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from slides_tools import exchange_slides_code
    result = exchange_slides_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Slides Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Slides Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Slides account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/slides/status", tags=["Slides"])
def slides_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Slides account."""
    user_id = int(current_user["sub"])
    tokens = get_slides_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/slides/disconnect", status_code=204, tags=["Slides"])
def slides_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Slides tokens, disconnecting Google Slides for this user."""
    user_id = int(current_user["sub"])
    delete_slides_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Forms OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/forms/connect", tags=["Forms"])
def forms_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Forms + Drive access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/forms/callback.
    """
    from forms_tools import get_forms_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_forms_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/forms/callback", response_class=HTMLResponse, tags=["Forms"])
def forms_callback(code: str, state: str):
    """
    Google redirects here after the user approves Forms access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from forms_tools import exchange_forms_code
    result = exchange_forms_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Forms Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a1a1a;">Google Forms Connected!</h2>
                <p style="color:#555;">Connected account:<br><strong>{result['email']}</strong></p>
                <p style="color:#888;font-size:14px;">You can close this tab and return to the chatbot.</p>
            </div>
        </body>
        </html>
        """)

    return HTMLResponse(
        status_code=400,
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#1a1a1a;">Connection Failed</h2>
                <p style="color:#555;">Could not connect your Google Forms account. Please try again.</p>
            </div>
        </body>
        </html>
        """,
    )


@app.get("/auth/forms/status", tags=["Forms"])
def forms_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Forms account."""
    user_id = int(current_user["sub"])
    tokens = get_forms_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/forms/disconnect", status_code=204, tags=["Forms"])
def forms_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Forms tokens, disconnecting Google Forms for this user."""
    user_id = int(current_user["sub"])
    delete_forms_tokens(user_id)


# ---------------------------------------------------------------------------
# Google Classroom OAuth
# ---------------------------------------------------------------------------

@app.get("/auth/classroom/connect", tags=["Classroom"])
def classroom_connect(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Generate the Google OAuth URL for Classroom access.
    The frontend should open this URL in a browser/popup so the user can
    grant access. After approval Google redirects to /auth/classroom/callback.
    """
    from classroom_tools import get_classroom_auth_url
    user_id = int(current_user["sub"])
    try:
        auth_url = get_classroom_auth_url(user_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/classroom/callback", response_class=HTMLResponse, tags=["Classroom"])
def classroom_callback(code: str, state: str):
    """
    Google redirects here after the user approves Classroom access.
    Exchanges the code for tokens, saves them, and shows a success page.
    This endpoint does NOT require a JWT — it is called by Google's redirect.
    """
    from classroom_tools import exchange_classroom_code
    result = exchange_classroom_code(code, state)
    if result:
        return HTMLResponse(content=f"""
        <html>
        <head><title>Google Classroom Connected</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">✅</div>
                <h2 style="color:#1a73e8;">Google Classroom Connected!</h2>
                <p style="color:#5f6368;">Account: <strong>{result['email']}</strong></p>
                <p style="color:#5f6368;font-size:14px;">You can close this window and return to the app.</p>
            </div>
        </body>
        </html>
        """)
    return HTMLResponse(
        content="""
        <html>
        <head><title>Connection Failed</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9fafb;">
            <div style="max-width:400px;margin:auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                <div style="font-size:48px;">❌</div>
                <h2 style="color:#d93025;">Connection Failed</h2>
                <p style="color:#5f6368;">Could not connect Google Classroom. Please try again.</p>
            </div>
        </body>
        </html>
        """,
        status_code=400,
    )


@app.get("/auth/classroom/status", tags=["Classroom"])
def classroom_status(current_user: Annotated[dict, Depends(get_current_user)]):
    """Check whether the logged-in user has connected their Google Classroom account."""
    from database import get_classroom_tokens
    user_id = int(current_user["sub"])
    tokens = get_classroom_tokens(user_id)
    return {
        "connected": tokens is not None,
        "google_email": tokens["google_email"] if tokens else None,
        "connected_at": tokens["connected_at"] if tokens else None,
    }


@app.delete("/auth/classroom/disconnect", status_code=204, tags=["Classroom"])
def classroom_disconnect(current_user: Annotated[dict, Depends(get_current_user)]):
    """Remove the stored Classroom tokens, disconnecting Google Classroom for this user."""
    from database import delete_classroom_tokens
    user_id = int(current_user["sub"])
    delete_classroom_tokens(user_id)


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.post("/sessions", status_code=201, tags=["Sessions"])
def new_session(
    body: SessionCreateRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Create a new chat session for the logged-in user."""
    user_id = int(current_user["sub"])
    session = create_session(user_id=user_id, title=body.title)
    return session


@app.get("/sessions", tags=["Sessions"])
def list_sessions(current_user: Annotated[dict, Depends(get_current_user)]):
    """List all sessions for the logged-in user (newest first)."""
    user_id = int(current_user["sub"])
    return get_sessions_for_user(user_id)


@app.get("/sessions/{session_id}", tags=["Sessions"])
def get_session_detail(
    session_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get a session and all its messages."""
    user_id = int(current_user["sub"])
    session = get_session(session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_messages(session_id)
    return {**session, "messages": messages}


@app.patch("/sessions/{session_id}", tags=["Sessions"])
def rename_session_endpoint(
    session_id: int,
    body: SessionRenameRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Rename a session title."""
    user_id = int(current_user["sub"])
    session = rename_session(session_id, user_id, body.title)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.delete("/sessions/{session_id}", status_code=204, tags=["Sessions"])
def delete_session_endpoint(
    session_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a session and all its messages."""
    user_id = int(current_user["sub"])
    if not delete_session(session_id, user_id):
        raise HTTPException(status_code=404, detail="Session not found")


# ---------------------------------------------------------------------------
# Image upload endpoint
# ---------------------------------------------------------------------------

@app.post("/upload", tags=["Chat"])
async def upload_images(
    files: Annotated[list[UploadFile], File()],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Upload one or more images to attach to a chat message.
    Returns { "image_urls": ["/uploads/<filename>", ...] } which you pass to POST /chat.
    Supported formats: JPEG, PNG, GIF, WEBP (max 10 MB each, max 10 images).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per upload.")

    image_urls: list[str] = []
    for file in files:
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{file.content_type}' for '{file.filename}'. Allowed: JPEG, PNG, GIF, WEBP.",
            )

        suffix = Path(file.filename or "upload").suffix.lower()
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            suffix = ".jpg"

        filename = f"{uuid.uuid4().hex}{suffix}"
        dest = UPLOADS_DIR / filename

        contents = await file.read()
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"File '{file.filename}' is too large. Maximum size is 10 MB.")

        with open(dest, "wb") as f:
            f.write(contents)

        image_urls.append(f"/uploads/{filename}")

    return {"image_urls": image_urls}


# ---------------------------------------------------------------------------
# Document upload endpoint (PDF, DOCX, PPTX, TXT)
# ---------------------------------------------------------------------------

@app.post("/upload/document", tags=["Chat"])
async def upload_documents(
    files: Annotated[list[UploadFile], File()],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Upload one or more documents to attach to a chat message.
    Returns { "document_urls": ["/uploads/<filename>", ...] } which you pass to POST /chat.
    Supported formats: PDF, DOCX, PPTX, PPT, TXT (max 20 MB each, max 5 documents).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > MAX_DOCS_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_DOCS_PER_UPLOAD} documents per upload.",
        )

    document_urls: list[str] = []
    max_bytes = MAX_DOC_SIZE_MB * 1024 * 1024

    for file in files:
        content_type = file.content_type or ""
        suffix = Path(file.filename or "upload").suffix.lower()

        if content_type not in ALLOWED_DOC_TYPES and suffix not in ALLOWED_DOC_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{content_type}' for '{file.filename}'. "
                f"Allowed: PDF, DOCX, PPTX, PPT, TXT.",
            )
        if suffix not in ALLOWED_DOC_EXTENSIONS:
            suffix = ".txt"

        filename = f"{uuid.uuid4().hex}{suffix}"
        dest = UPLOADS_DIR / filename

        contents = await file.read()
        if len(contents) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File '{file.filename}' is too large. Maximum size is {MAX_DOC_SIZE_MB} MB.",
            )

        with open(dest, "wb") as f:
            f.write(contents)

        document_urls.append(f"/uploads/{filename}")

    return {"document_urls": document_urls}


@app.get("/stream-test", response_class=HTMLResponse, tags=["Chat"])
def stream_test_page():
    """Serve a simple HTML page to verify streaming works (use fetch + getReader, not res.text())."""
    path = Path(__file__).parent / "stream_test.html"
    return path.read_text(encoding="utf-8")


@app.get("/uploads/{filename}", tags=["Chat"])
async def get_upload(filename: str):
    """
    Serve an uploaded image file.
    This explicit route ensures CORS headers are applied (unlike StaticFiles mounts).
    """
    file_path = UPLOADS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(file_path)


# ---------------------------------------------------------------------------
# Chat endpoint (protected + session-aware)
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    body: ChatRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Send a message to the EduCare agent.
    - Pass `session_id` to continue an existing session.
    - Omit `session_id` (or pass null) to auto-create a new session.
    Both the user message and the agent reply are saved to the session.
    """
    user_id = int(current_user["sub"])

    # Resolve or create the session — always keep the full session dict
    # so we can read the stored claude_session_id for conversation resumption
    if body.session_id is not None:
        session = get_session(body.session_id, user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        title = body.message[:60] + ("…" if len(body.message) > 60 else "")
        session = create_session(user_id=user_id, title=title)

    session_id = session["id"]

    # The Claude SDK session ID lets the SDK resume the exact conversation
    # transcript on its end — full unlimited memory, no manual history needed
    claude_session_id: str | None = session.get("claude_session_id")

    # Always load DB history — used as fallback if the SDK transcript is missing
    history = get_messages(session_id)

    # Serialize image and document lists as JSON for DB storage
    import json as _json
    image_urls_json = _json.dumps(body.image_urls) if body.image_urls else None
    document_urls_json = _json.dumps(body.document_urls) if body.document_urls else None

    # Save the current user message (with optional images and documents)
    add_message(
        session_id, role="user", content=body.message,
        image_url=image_urls_json, document_url=document_urls_json,
    )

    # Run the agent — tries native resume first, falls back to DB history if needed
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        reply, new_claude_session_id = await loop.run_in_executor(
            pool,
            _run_agent_in_thread,
            session_id,
            body.message,
            user_id,
            claude_session_id,
            history,
            body.image_urls or [],
            body.document_urls or [],
        )

    # Save the assistant reply
    add_message(session_id, role="assistant", content=reply)

    # Persist the SDK session ID so the next message in this session can resume
    if new_claude_session_id:
        save_claude_session_id(session_id, new_claude_session_id)

    # Save conversation exchange to Mem0 long-term memory (best-effort)
    try:
        from memory import add_memory
        add_memory(user_id, [
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": reply},
        ])
    except Exception:
        pass

    _broadcast_response_done(user_id, session_id)

    return ChatResponse(
        reply=reply,
        user=current_user["username"],
        session_id=session_id,
        image_urls=body.image_urls,
        document_urls=body.document_urls,
    )


# ---------------------------------------------------------------------------
# Chat streaming endpoint (SSE)
# ---------------------------------------------------------------------------

@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(
    body: ChatRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Stream chat responses via Server-Sent Events.
    Same request body as /chat. Events: text (chunk), tool_start, tool_end, done.
    """
    user_id = int(current_user["sub"])

    if body.session_id is not None:
        session = get_session(body.session_id, user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        title = body.message[:60] + ("…" if len(body.message) > 60 else "")
        session = create_session(user_id=user_id, title=title)

    session_id = session["id"]
    claude_session_id: str | None = session.get("claude_session_id")
    history = get_messages(session_id)

    import json as _json
    image_urls_json = _json.dumps(body.image_urls) if body.image_urls else None
    document_urls_json = _json.dumps(body.document_urls) if body.document_urls else None
    add_message(
        session_id, role="user", content=body.message,
        image_url=image_urls_json, document_url=document_urls_json,
    )

    queue: Queue = Queue()

    def run_streaming_agent():
        _run_agent_streaming_in_thread(
            queue=queue,
            session_id=session_id,
            user_message=body.message,
            user_id=user_id,
            claude_session_id=claude_session_id,
            history=history,
            image_urls=body.image_urls or [],
            document_urls=body.document_urls or [],
        )

    thread = threading.Thread(target=run_streaming_agent)
    thread.start()

    def _sse_event(event_type: str, data: dict) -> bytes:
        """Format SSE event as bytes for immediate flush."""
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")

    def event_generator():
        """Sync generator — yields bytes for unbuffered streaming."""
        try:
            while True:
                item = queue.get()
                event_type = item.get("type")
                if event_type == "done":
                    yield _sse_event("done", {
                        "reply": item["reply"],
                        "session_id": session_id,
                        "user": current_user["username"],
                        "image_urls": body.image_urls or [],
                        "document_urls": body.document_urls or [],
                    })
                    break
                elif event_type == "thinking_start":
                    yield _sse_event("thinking_start", {})
                elif event_type == "thinking":
                    yield _sse_event("thinking", {"content": item["content"]})
                elif event_type == "text":
                    yield _sse_event("text", {"content": item["content"]})
                elif event_type == "tool_start":
                    yield _sse_event("tool_start", {"tool": item["tool"]})
                elif event_type == "tool_input":
                    yield _sse_event("tool_input", {"tool": item["tool"], "input": item["input"]})
                elif event_type == "tool_end":
                    yield _sse_event("tool_end", {"tool": item["tool"]})
        finally:
            thread.join(timeout=1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Response-done notification channel (SSE)
# ---------------------------------------------------------------------------

@app.get("/chat/notifications", tags=["Chat"])
async def chat_notifications(
    current_user: Annotated[dict, Depends(get_current_user_or_token)],
):
    """
    Server-Sent Events stream for notifications.
    Connect and keep open. Events:
    - response_done: agent response completed, data: {"type":"response_done","session_id":N}
    - reminder: a reminder is due, data: {"type":"reminder","message":"...","remind_at":"...","reminder_id":N}
    Sends a heartbeat every 2s to keep the connection alive.
    """
    user_id = int(current_user["sub"])
    my_queue: Queue = Queue()

    with _notification_lock:
        if user_id not in _notification_listeners:
            _notification_listeners[user_id] = []
        _notification_listeners[user_id].append(my_queue)

    def _sse_event(event_type: str, data: dict) -> bytes:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")

    def event_generator():
        try:
            while True:
                try:
                    item = my_queue.get(timeout=2)
                    event_type = item.get("event", item.get("type", "response_done"))
                    yield _sse_event(event_type, item)
                except Empty:
                    yield _sse_event("heartbeat", {"ts": str(uuid.uuid4())[:8]})
        finally:
            with _notification_lock:
                if user_id in _notification_listeners:
                    lst = _notification_listeners[user_id]
                    if my_queue in lst:
                        lst.remove(my_queue)
                    if not lst:
                        del _notification_listeners[user_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Reminders API
# ---------------------------------------------------------------------------

@app.get("/reminders", tags=["Reminders"])
def list_reminders(
    current_user: Annotated[dict, Depends(get_current_user)],
    include_delivered: bool = Query(False, description="Include past/delivered reminders"),
):
    """List reminders for the logged-in user."""
    user_id = int(current_user["sub"])
    rows = get_reminders_for_user(user_id, include_delivered=include_delivered)
    return [{"id": r["id"], "remind_at": r["remind_at"], "message": r["message"], "delivered": bool(r["delivered"])} for r in rows]


@app.get("/reminders/pending", tags=["Reminders"])
def list_pending_reminders(current_user: Annotated[dict, Depends(get_current_user)]):
    """List undelivered reminders (for showing when user opens the app)."""
    user_id = int(current_user["sub"])
    rows = get_pending_reminders_for_user(user_id)
    return [{"id": r["id"], "remind_at": r["remind_at"], "message": r["message"]} for r in rows]


@app.delete("/reminders/{reminder_id}", status_code=204, tags=["Reminders"])
def cancel_reminder(
    reminder_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Cancel a reminder."""
    user_id = int(current_user["sub"])
    if not delete_reminder(reminder_id, user_id):
        raise HTTPException(status_code=404, detail="Reminder not found")


# ---------------------------------------------------------------------------
# Google Chat Webhook Endpoint & Processing
# ---------------------------------------------------------------------------

def _get_gchat_service():
    """Build the Google Chat discovery service using the service account."""
    scopes = ["https://www.googleapis.com/auth/chat.bot"]
    creds_path = "service-account.json"
    if not os.path.exists(creds_path):
        print(f"[GChat] {creds_path} not found — bot cannot send message")
        return None
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    return build("chat", "v1", credentials=creds)


def send_gchat_message(space_name: str, text: str):
    """Send a plain text message to a Google Chat space/DM."""
    try:
        service = _get_gchat_service()
        if not service:
            return
        
        # Google Chat expects the message body in a specific JSON format
        body = {"text": text}
        service.spaces().messages().create(parent=space_name, body=body).execute()
        print(f"[GChat] Message sent to {space_name}")
    except Exception as e:
        print(f"[GChat] Failed to send message: {e}")
        traceback.print_exc()


@app.post("/webhooks/google-chat", tags=["Google Chat"])
async def google_chat_webhook(
    request_data: dict,
    background_tasks: BackgroundTasks
):
    """Webhook endpoint for Google Chat mentions."""
    print(f"[GChat] Webhook received: {json.dumps(request_data, indent=2)}")
    
    # Google Chat can send data in different formats depending on if it's a "Bot" or an "App"
    # Format 1 (Standard Bot): message is at root.
    # Format 2 (GWS App/Add-on): message is inside chat.messagePayload.
    
    # Try Format 1
    message_data = request_data.get("message")
    space_data = request_data.get("space")
    
    # Fallback to Format 2
    if not message_data or not space_data:
        chat_payload = request_data.get("chat", {}).get("messagePayload", {})
        message_data = message_data or chat_payload.get("message", {})
        space_data = space_data or chat_payload.get("space", {})
    
    if not message_data:
        # If it's still not a message (e.g. just a join/ping), acknowledge and exit
        return {"status": "ok"}
        
    space_name = space_data.get("name")
    space_type = space_data.get("type", "SPACE")
    user_message_text = message_data.get("text", "").strip()
    
    # Extract names and emails
    sender_user_obj = message_data.get("sender", {})
    root_user_obj = request_data.get("chat", {}).get("user", {})
    
    sender_email = sender_user_obj.get("email") or root_user_obj.get("email")
    sender_name = sender_user_obj.get("displayName") or root_user_obj.get("displayName", "User")
    
    user_message_text = message_data.get("text", "").strip()
    
    print(f"[GChat] Received message from {sender_name} ({sender_email}) in {space_name} (Type: {space_type})")
    
    if not space_name or not sender_email or not user_message_text:
        print(f"[GChat] Skipping: Missing required fields (email={sender_email}, text={bool(user_message_text)})")
        return {"status": "error", "message": "Missing fields"}
    
    bot_name = os.getenv("GCHAT_BOT_NAME", "agent")
    bot_user_id = os.getenv("GCHAT_BOT_USER_ID", "")
    
    mention_patterns = [
        f"@{bot_name}",
        f"<users/{bot_user_id}>" if bot_user_id else None,
    ]
    mention_patterns = [p for p in mention_patterns if p]
    
    is_dm = (space_type == "DM")
    has_mention = any(pattern in user_message_text for pattern in mention_patterns)
    
    # In groups/spaces, we require a mention. In DMs, we process everything.
    if not is_dm and not has_mention:
        print(f"[GChat] Skipping message in {space_name}: No mention detected.")
        return {}
    
    print(f"[GChat] Processing message (is_dm={is_dm}, has_mention={has_mention})")
    
    # Clean the message
    cleaned_message = user_message_text
    for pattern in mention_patterns:
        cleaned_message = cleaned_message.replace(pattern, "").strip()
    
    background_tasks.add_task(
        process_gchat_mention,
        space_name=space_name,
        sender_email=sender_email,
        sender_name=sender_name,
        cleaned_message=cleaned_message,
        is_group=(not is_dm)
    )
    
    # Return empty JSON to acknowledge receipt (don't send status: ok, Google prefers {} or a message)
    return {}


def process_gchat_mention(
    space_name: str,
    sender_email: str,
    sender_name: str,
    cleaned_message: str,
    is_group: bool
):
    """Background task to process the Google Chat mention."""
    print(f"[GChat Background] Started for {sender_email} in {space_name}")
    try:
        # Bug #2 Fix: Identity Resolution & strict enrollment check
        sender_user = get_user_by_email(sender_email)
        if not sender_user:
            print(f"[GChat Background] Enroll Check Failed: {sender_email} not found in DB.")
            send_gchat_message(space_name, f"❌ User {sender_email} not found.\n\nPlease sign up at the web portal first to use Cortex in Google Chat.")
            return
        
        sender_user_id = sender_user["id"]
        
        # Resolve the session
        session = get_or_create_gchat_session(space_name, sender_email, is_group=is_group)
        if not session:
             # Safety fallback
             session = create_session(user_id=None, title=space_name)
             
        session_id = session["id"]
        print(f"[GChat Background] Using session_id {session_id} for user_id {sender_user_id}")
        
        # Prefix the message with the sender's name for collaborative spaces
        if is_group:
            display_message = f"[{sender_name}]: {cleaned_message}"
        else:
            display_message = cleaned_message
            
        print(f"[GChat Background] Adding message to DB: {display_message[:50]}...")
        add_message(session_id, role="user", content=display_message)
        
        history = get_messages(session_id)
        claude_session_id = session.get("claude_session_id")
        
        print(f"[GChat Background] Running agent reasoning...")
        # Bug #1 Fix: _run_agent_in_thread expects 7 parameters (added [], [])
        reply, new_claude_session_id = _run_agent_in_thread(
            session_id, display_message, sender_user_id, claude_session_id, history,
            [], [] # image_urls, document_urls
        )
        print(f"[GChat Background] Agent finished reasoning. Reply length: {len(reply)}")
        
        add_message(session_id, role="assistant", content=reply)
        
        if new_claude_session_id:
            save_claude_session_id(session_id, new_claude_session_id)
        
        send_gchat_message(space_name, reply)
        
        # Best-effort memory update
        try:
            from memory import add_memory as _mem0_add
            _mem0_add(sender_user_id, [
                {"role": "user", "content": cleaned_message},
                {"role": "assistant", "content": reply},
            ])
        except Exception:
            pass
            
    except Exception as e:
        # Bug #3 Fix: Use traceback to show what actually failed
        print(f"[GChat Background] CRITICAL ERROR: {e}")
        traceback.print_exc()
        try:
            send_gchat_message(space_name, f"❌ Error processing message: {str(e)[:100]}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Agent runner (Windows ProactorEventLoop workaround)
# ---------------------------------------------------------------------------

def _run_agent_in_thread(
    session_id: int,
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str] | None = None,
    document_urls: list[str] | None = None,
) -> tuple[str, str | None]:
    """
    Runs in a worker thread. Creates a fresh ProactorEventLoop (Windows-safe)
    and drives the async agent to completion.
    Returns (reply_text, new_claude_session_id).
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _run_agent(
                session_id, user_message, user_id, claude_session_id, history,
                image_urls or [], document_urls or [],
            )
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _run_agent_streaming_in_thread(
    queue: Queue,
    session_id: int,
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str],
    document_urls: list[str] | None = None,
) -> None:
    """
    Runs in a worker thread. Streams agent output via queue.
    Puts: {"type": "text", "content": str}, {"type": "tool_start", "tool": str},
    {"type": "tool_end", "tool": str}, {"type": "done", "reply": str, ...}.
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            _run_agent_streaming(
                queue, session_id, user_message, user_id, claude_session_id, history,
                image_urls, document_urls or [],
            )
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _extract_document_texts(document_urls: list[str]) -> str:
    """Extract text from uploaded documents and return formatted string for agent context."""
    if not document_urls:
        return ""
    from document_parser import extract_text_from_file

    parts: list[str] = []
    for url in document_urls:
        fname = url.lstrip("/").replace("uploads/", "", 1)
        path = UPLOADS_DIR / fname
        if not path.exists():
            parts.append(f"[Document {fname}: file not found]")
            continue
        try:
            text = extract_text_from_file(path)
            if len(text) > MAX_EXTRACTED_TEXT_CHARS:
                text = text[:MAX_EXTRACTED_TEXT_CHARS] + "\n\n[... truncated ...]"
            parts.append(f"--- Content of {fname} ---\n{text}")
        except Exception as e:
            parts.append(f"[Document {fname}: error extracting text — {e}]")
    return "\n\n".join(parts)


def _build_context_from_db(history: list[dict], current_message: str) -> str:
    """
    Reconstruct full conversation context from DB messages (no cap).
    Used as fallback when the SDK transcript file is missing / session expired.
    """
    if not history:
        return current_message

    import json as _json

    lines = [
        "[CONVERSATION HISTORY — use this to recall all prior context]",
        "",
    ]
    for msg in history:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        line = f"{role_label}: {msg['content']}"
        if msg.get("image_url"):
            try:
                urls = _json.loads(msg["image_url"])
            except (ValueError, TypeError):
                urls = [msg["image_url"]]
            for url in urls:
                fname = url.lstrip("/").replace("uploads/", "", 1)
                line += f" [attached image: uploads/{fname}]"
        if msg.get("document_url"):
            try:
                urls = _json.loads(msg["document_url"])
            except (ValueError, TypeError):
                urls = [msg["document_url"]]
            for url in urls:
                fname = url.lstrip("/").replace("uploads/", "", 1)
                line += f" [attached document: uploads/{fname}]"
        lines.append(line)

    lines += [
        "",
        "[CURRENT MESSAGE]",
        f"User: {current_message}",
    ]
    return "\n".join(lines)


async def _run_agent(
    session_id: int,
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str] | None = None,
    document_urls: list[str] | None = None,
) -> tuple[str, str | None]:
    """
    Async agent runner — must be called inside a ProactorEventLoop on Windows.

    Memory strategy (belt-and-suspenders):
      1. PRIMARY   — pass resume=claude_session_id to the SDK so it loads the
                     local transcript file. Full, uncapped, zero overhead.
      2. FALLBACK  — if the transcript is missing (server restart, migration, etc.)
                     the SDK raises an error. We catch it, rebuild the full
                     conversation from DB messages (no cap), and start a fresh
                     SDK session seeded with that history.

    Returns (reply_text, new_claude_session_id).
    """
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))

    from agent_config import make_agent_options
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    from agent_trace import session_start as trace_session_start, write as trace_write

    agent_cwd = Path(__file__).parent / "cwd"
    agent_cwd.mkdir(exist_ok=True)

    def _make_options(resume_id: str | None) -> ClaudeAgentOptions:
        return make_agent_options(agent_cwd=agent_cwd, user_id=user_id, resume_id=resume_id)

    def _agent_model_keys(options: ClaudeAgentOptions) -> dict[str, str]:
        """Map agent_name → model keyword for delegation-stack tracking."""
        out: dict[str, str] = {}
        if options.agents:
            for name, agent_def in options.agents.items():
                model = getattr(agent_def, "model", None)
                if model:
                    out[name] = model.lower()
        return out

    async def _execute(options: ClaudeAgentOptions, msg: str) -> tuple[str, str | None]:
        text_chunks: list[str] = []
        response_chunks: list[str] = []
        new_sid: str | None = None
        amk = _agent_model_keys(options)
        delegation_stack: list[str] = []
        pending_delegation: str | None = None
        trace_session_start(session_id, user_message)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(msg)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    msg_model = (message.model or "").lower()
                    if pending_delegation:
                        delegation_stack.append(pending_delegation)
                        pending_delegation = None
                    while delegation_stack:
                        top_model = amk.get(delegation_stack[-1], "")
                        if top_model and top_model in msg_model:
                            break
                        delegation_stack.pop()
                    agent_label = delegation_stack[-1] if delegation_stack else "Main agent"
                    trace_write(f"[Agent: {agent_label}]")
                    has_tool_use = any(isinstance(b, ToolUseBlock) for b in message.content)
                    turn_chunks: list[str] = []
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            if block.name == "Task":
                                agent_name = (
                                    (block.input or {}).get("subagent_type")
                                    or (block.input or {}).get("agent")
                                    or "subagent"
                                )
                                task = str((block.input or {}).get("prompt") or (block.input or {}).get("task") or (block.input or {}).get("description", ""))
                                task_preview = task[:80] + "..." if len(task) > 80 else task
                                trace_write(f"  → [Subagent] Delegating to '{agent_name}': {task_preview}")
                                pending_delegation = agent_name
                            elif block.name == "WebSearch":
                                query = (block.input or {}).get("query", "")
                                trace_write(f"  → [WebSearch] Searching: {query}")
                            else:
                                trace_write(f"  → [Tool] {block.name} {block.input}")
                        elif isinstance(block, TextBlock):
                            if agent_label != "Main agent":
                                trace_write(f"  --- {agent_label} response ---")
                            trace_write(block.text)
                            text_chunks.append(block.text)
                            turn_chunks.append(block.text)
                    if not has_tool_use and turn_chunks:
                        response_chunks = turn_chunks
                elif isinstance(message, ResultMessage):
                    new_sid = message.session_id
                    trace_write(f"[Result] turns={message.num_turns} duration={message.duration_ms}ms")
        final_chunks = response_chunks if response_chunks else text_chunks
        raw = "\n".join(final_chunks) if final_chunks else "(no response)"
        raw = raw.replace("\\n", "\n").replace("\\t", "\t")
        return raw, new_sid

    # Build effective message: user text + image note + extracted document text
    effective_message = user_message
    if image_urls:
        paths = ", ".join(
            f"uploads/{url.lstrip('/').replace('uploads/', '', 1)}"
            for url in image_urls
        )
        count = len(image_urls)
        label = "image" if count == 1 else f"{count} images"
        effective_message = (
            f"{effective_message}\n\n"
            f"[The user has attached {label}. Use the Read tool to view "
            f"{'it' if count == 1 else 'each one'} at the relative "
            f"{'path' if count == 1 else 'paths'}: {paths}]"
        )
    if document_urls:
        doc_text = _extract_document_texts(document_urls)
        if doc_text:
            effective_message = (
                f"{effective_message}\n\n"
                "[The user has attached the following document(s). The extracted text is below.]\n\n"
                f"{doc_text}"
            )

    # ── PRIMARY: native SDK session resumption ──────────────────────────────
    if claude_session_id:
        try:
            return await _execute(_make_options(claude_session_id), effective_message)
        except Exception as primary_err:
            print(
                f"\n[Memory] SDK resume failed for session '{claude_session_id}': "
                f"{primary_err}\n"
                f"[Memory] Falling back to full DB history reconstruction.\n"
            )

    # ── FALLBACK: rebuild full context from DB messages (no cap) ────────────
    full_message = _build_context_from_db(history, effective_message)
    return await _execute(_make_options(None), full_message)


async def _run_agent_streaming(
    queue: Queue,
    session_id: int,
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str],
    document_urls: list[str] | None = None,
) -> None:
    """
    Async streaming agent runner. Puts text/tool/done events into queue.
    """
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))

    from agent_config import make_agent_options
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )
    from claude_agent_sdk.types import StreamEvent

    from agent_trace import session_start as trace_session_start, write as trace_write

    agent_cwd = Path(__file__).parent / "cwd"
    agent_cwd.mkdir(exist_ok=True)

    def _make_options(resume_id: str | None) -> ClaudeAgentOptions:
        return make_agent_options(
            agent_cwd=agent_cwd,
            user_id=user_id,
            resume_id=resume_id,
            include_partial_messages=True,
        )

    def _agent_model_keys(options: ClaudeAgentOptions) -> dict[str, str]:
        """Map agent_name → model keyword for delegation-stack tracking."""
        out: dict[str, str] = {}
        if options.agents:
            for name, agent_def in options.agents.items():
                model = getattr(agent_def, "model", None)
                if model:
                    out[name] = model.lower()
        return out

    effective_message = user_message
    if image_urls:
        paths = ", ".join(
            f"uploads/{url.lstrip('/').replace('uploads/', '', 1)}"
            for url in image_urls
        )
        count = len(image_urls)
        label = "image" if count == 1 else f"{count} images"
        effective_message = (
            f"{effective_message}\n\n"
            f"[The user has attached {label}. Use the Read tool to view "
            f"{'it' if count == 1 else 'each one'} at the relative "
            f"{'path' if count == 1 else 'paths'}: {paths}]"
        )
    if document_urls:
        doc_text = _extract_document_texts(document_urls)
        if doc_text:
            effective_message = (
                f"{effective_message}\n\n"
                "[The user has attached the following document(s). The extracted text is below.]\n\n"
                f"{doc_text}"
            )

    async def _execute_streaming(opts: ClaudeAgentOptions, msg: str) -> None:
        assistant_texts: list[str] = []
        sid: str | None = None
        in_tool = False
        current_tool: str | None = None
        current_tool_input_str: str = ""
        amk = _agent_model_keys(opts)
        delegation_stack: list[str] = []
        pending_delegation: str | None = None
        trace_session_start(session_id, user_message)

        async with ClaudeSDKClient(options=opts) as client:
            await client.query(msg)
            async for message in client.receive_response():

                # ═══════════════════════════════════════════════════════
                # 1. HANDLE STREAM EVENTS (real-time deltas)
                # ═══════════════════════════════════════════════════════
                if isinstance(message, StreamEvent):
                    event = message.event
                    event_type = event.get("type")

                    # Handle block starts (thinking or tool use)
                    if event_type == "content_block_start":
                        content_block = event.get("content_block", {})
                        block_type = content_block.get("type")
                        
                        if block_type == "thinking":
                            queue.put({"type": "thinking_start"})
                        elif block_type == "tool_use":
                            current_tool = content_block.get("name", "Tool")
                            current_tool_input_str = ""
                            in_tool = True
                            queue.put({"type": "tool_start", "tool": current_tool})

                    # Handle deltas (text, thinking, or tool input)
                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type")
                        
                        if delta_type == "text_delta":
                            # ACTUAL RESPONSE TEXT - send immediately
                            chunk = delta.get("text", "")
                            if chunk:
                                queue.put({"type": "text", "content": chunk})
                                assistant_texts.append(chunk)
                        
                        elif delta_type == "thinking_delta":
                            # THINKING TEXT - send immediately
                            chunk = delta.get("thinking", "")
                            if chunk:
                                queue.put({"type": "thinking", "content": chunk})
                        
                        elif delta_type == "tool_use_delta":
                            # TOOL INPUT being built up (as string fragment)
                            input_delta = delta.get("input", "")
                            if input_delta:
                                current_tool_input_str += input_delta

                    # Handle block stops
                    elif event_type == "content_block_stop":
                        if in_tool and current_tool:
                            # Parse final tool input if possible, otherwise send raw
                            try:
                                parsed_input = json.loads(current_tool_input_str)
                            except Exception:
                                parsed_input = current_tool_input_str
                            
                            queue.put({
                                "type": "tool_input",
                                "tool": current_tool,
                                "input": parsed_input
                            })
                            queue.put({"type": "tool_end", "tool": current_tool})
                            in_tool = False
                            current_tool = None
                            current_tool_input_str = ""

                # ═══════════════════════════════════════════════════════
                # 2. HANDLE ASSISTANT MESSAGE (for delegation tracking)
                # ═══════════════════════════════════════════════════════
                elif isinstance(message, AssistantMessage):
                    msg_model = (message.model or "").lower()
                    if pending_delegation:
                        delegation_stack.append(pending_delegation)
                        pending_delegation = None
                    
                    while delegation_stack:
                        top_model = amk.get(delegation_stack[-1], "")
                        if top_model and top_model in msg_model:
                            break
                        delegation_stack.pop()
                    
                    agent_label = delegation_stack[-1] if delegation_stack else "Main agent"
                    trace_write(f"[Agent: {agent_label}]")
                    
                    # ONLY use this for tracing tool delegation, NOT for streaming chunks
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            if block.name == "Task":
                                agent_name = (
                                    (block.input or {}).get("subagent_type")
                                    or (block.input or {}).get("agent")
                                    or "subagent"
                                )
                                trace_write(f"  → [Subagent] {agent_name}")
                                pending_delegation = agent_name
                            elif block.name == "WebSearch":
                                query = (block.input or {}).get("query", "")
                                trace_write(f"  → [WebSearch] Searching: {query}")
                            else:
                                trace_write(f"  → [Tool] {block.name} {block.input}")
                        elif isinstance(block, TextBlock) and block.text:
                            if agent_label != "Main agent":
                                trace_write(f"  --- {agent_label} response ---")
                            trace_write(block.text)

                # ═══════════════════════════════════════════════════════
                # 3. HANDLE RESULT MESSAGE (completion)
                # ═══════════════════════════════════════════════════════
                elif isinstance(message, ResultMessage):
                    sid = message.session_id
                    trace_write(f"[Result] turns={message.num_turns} duration={message.duration_ms}ms")

        # Build final response from collected chunks
        raw = "".join(assistant_texts)
        if not raw:
            raw = "(no response)"
        
        # Strip escaped characters if any (matching original behavior if needed)
        # raw = raw.replace("\\n", "\n").replace("\\t", "\t")

        # Save and broadcast AFTER streaming completes
        add_message(session_id, role="assistant", content=raw)
        if sid:
            save_claude_session_id(session_id, sid)

        # Save conversation exchange to Mem0 long-term memory (best-effort)
        try:
            from memory import add_memory as _mem0_add
            _mem0_add(user_id, [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": raw},
            ])
        except Exception:
            pass

        _broadcast_response_done(user_id, session_id)

        # Put done event with final assembled response
        queue.put({
            "type": "done",
            "reply": raw,
            "session_id": session_id,
            "new_claude_session_id": sid,
        })

    if claude_session_id:
        try:
            await _execute_streaming(_make_options(claude_session_id), effective_message)
        except Exception as primary_err:
            print(
                f"\n[Memory] SDK resume failed for session '{claude_session_id}': "
                f"{primary_err}\n"
                f"[Memory] Falling back to full DB history reconstruction.\n"
            )
            full_message = _build_context_from_db(history, effective_message)
            await _execute_streaming(_make_options(None), full_message)
    else:
        full_message = _build_context_from_db(history, effective_message)
        await _execute_streaming(_make_options(None), full_message)


# ---------------------------------------------------------------------------
# Long-term memory endpoints (Mem0 Platform)
# ---------------------------------------------------------------------------

@app.get("/memory")
async def list_user_memories(current_user: dict = Depends(get_current_user)):
    """Return all stored long-term memories for the authenticated user."""
    from memory import get_all_memories
    user_id = int(current_user["sub"])
    memories = get_all_memories(user_id)
    return {"memories": memories}


@app.post("/memory/search")
async def search_user_memories(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """Semantic search over the user's long-term memories."""
    from memory import search_memory
    user_id = int(current_user["sub"])
    query = body.get("query", "")
    limit = body.get("limit", 10)
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    results = search_memory(user_id, query, limit=limit)
    return {"results": results}


@app.delete("/memory/{memory_id}")
async def delete_user_memory(
    memory_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a specific memory by its Mem0 ID."""
    from memory import delete_memory
    ok = delete_memory(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found or delete failed")
    return {"deleted": True}


@app.put("/memory/{memory_id}")
async def update_user_memory(
    memory_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """Update the text content of a specific memory."""
    from memory import update_memory
    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    ok = update_memory(memory_id, text)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found or update failed")
    return {"updated": True}


# ---------------------------------------------------------------------------
# Kanban Board System
# ---------------------------------------------------------------------------

# Pydantic models for request bodies
class CreateWorkspaceRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class InviteMemberRequest(BaseModel):
    email: EmailStr


class CreateColumnRequest(BaseModel):
    name: str
    position: int
    color: str = "#808080"


class UpdateColumnRequest(BaseModel):
    name: str | None = None
    position: int | None = None
    color: str | None = None


class CreateTaskRequest(BaseModel):
    column_id: int
    title: str
    description: str | None = None
    assignee_email: EmailStr | None = None
    priority: str = "medium"
    due_date: str | None = None
    position: int = 0


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    column_id: int | None = None
    assignee_email: EmailStr | None = None
    priority: str | None = None
    due_date: str | None = None
    position: int | None = None


class CreateCommentRequest(BaseModel):
    comment: str


# ---------------------------------------------------------------------------
# Workspace Endpoints
# ---------------------------------------------------------------------------

@app.post("/workspaces", status_code=201, tags=["Kanban - Workspaces"])
def create_workspace_endpoint(
    body: CreateWorkspaceRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a new workspace. User becomes the owner."""
    user_id = int(current_user["sub"])
    workspace = create_workspace(body.name, body.description, user_id)
    return {"workspace": workspace}


@app.get("/workspaces", tags=["Kanban - Workspaces"])
def list_user_workspaces(current_user: dict = Depends(get_current_user)):
    """List all workspaces the user is a member of."""
    user_id = int(current_user["sub"])
    workspaces = get_user_workspaces(user_id)
    return {"workspaces": workspaces}


@app.get("/workspaces/{workspace_id}", tags=["Kanban - Workspaces"])
def get_workspace_endpoint(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get workspace details. User must be a member."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    workspace = get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    members = get_workspace_members(workspace_id)
    columns = get_workspace_columns(workspace_id)
    tasks = get_workspace_tasks(workspace_id)

    return {
        "workspace": workspace,
        "members": members,
        "columns": columns,
        "tasks": tasks,
    }


@app.put("/workspaces/{workspace_id}", tags=["Kanban - Workspaces"])
def update_workspace_endpoint(
    workspace_id: int,
    body: UpdateWorkspaceRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update workspace details. User must be the owner."""
    user_id = int(current_user["sub"])

    if not is_workspace_owner(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Only workspace owner can update")

    workspace = update_workspace(workspace_id, body.name, body.description)
    return {"workspace": workspace}


@app.delete("/workspaces/{workspace_id}", tags=["Kanban - Workspaces"])
def delete_workspace_endpoint(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Delete workspace. User must be the owner."""
    user_id = int(current_user["sub"])

    if not is_workspace_owner(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Only workspace owner can delete")

    deleted = delete_workspace(workspace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return {"deleted": True}


# ---------------------------------------------------------------------------
# Workspace Members & Invitations
# ---------------------------------------------------------------------------

@app.get("/workspaces/{workspace_id}/members", tags=["Kanban - Members & Invitations"])
def list_workspace_members(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List all members of a workspace."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    members = get_workspace_members(workspace_id)
    return {"members": members}


@app.post("/workspaces/{workspace_id}/invite", tags=["Kanban - Members & Invitations"])
def invite_member(
    workspace_id: int,
    body: InviteMemberRequest,
    current_user: dict = Depends(get_current_user),
):
    """Invite a user to workspace by email. Sends notification email."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    workspace = get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Check if user exists
    invited_user = get_user_by_email(body.email)

    if invited_user:
        # User exists - add directly to workspace
        member = add_workspace_member(workspace_id, invited_user["id"])
        if not member:
            raise HTTPException(status_code=400, detail="User is already a member")

        # Send email notification
        from email_notifications import send_workspace_invitation_email
        workspace_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/workspaces/{workspace_id}"
        current_user_info = get_user_by_id(user_id)
        send_workspace_invitation_email(
            body.email,
            workspace["name"],
            current_user_info["username"] if current_user_info else "A team member",
            workspace_link,
            user_exists=True
        )

        return {
            "message": "User added to workspace",
            "member": member,
            "user_exists": True,
        }
    else:
        # User doesn't exist - create invitation
        from datetime import datetime, timedelta, timezone

        token = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        invitation = create_workspace_invitation(
            workspace_id, body.email, user_id, token, expires_at
        )

        # Send invitation email with signup link
        from email_notifications import send_workspace_invitation_email
        invitation_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/invitations/accept?token={token}"
        current_user_info = get_user_by_id(user_id)
        send_workspace_invitation_email(
            body.email,
            workspace["name"],
            current_user_info["username"] if current_user_info else "A team member",
            invitation_link,
            user_exists=False
        )

        return {
            "message": "Invitation sent",
            "invitation": invitation,
            "user_exists": False,
        }


@app.get("/invitations/accept", tags=["Kanban - Members & Invitations"])
def accept_invitation_endpoint(token: str, current_user: dict = Depends(get_current_user)):
    """Accept a workspace invitation."""
    user_id = int(current_user["sub"])

    invitation = get_invitation_by_token(token)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invitation["status"] != "pending":
        raise HTTPException(status_code=400, detail="Invitation already processed")

    # Check if expired
    from datetime import datetime, timezone
    expires_at = datetime.fromisoformat(invitation["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invitation expired")

    # Accept invitation
    accepted = accept_invitation(invitation["id"], user_id)
    if not accepted:
        raise HTTPException(status_code=500, detail="Failed to accept invitation")

    workspace = get_workspace(invitation["workspace_id"])
    return {
        "message": "Invitation accepted",
        "workspace": workspace,
    }


@app.get("/invitations/pending", tags=["Kanban - Members & Invitations"])
def list_pending_invitations(current_user: dict = Depends(get_current_user)):
    """List all pending invitations for the current user's email."""
    user_id = int(current_user["sub"])
    user = get_user_by_id(user_id)
    if not user:
        return {"invitations": []}

    invitations = get_pending_invitations_by_email(user["email"])
    return {"invitations": invitations}


@app.delete("/workspaces/{workspace_id}/members/{member_user_id}", tags=["Kanban - Members & Invitations"])
def remove_member(
    workspace_id: int,
    member_user_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Remove a member from workspace. Owner only."""
    user_id = int(current_user["sub"])

    if not is_workspace_owner(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Only workspace owner can remove members")

    removed = remove_workspace_member(workspace_id, member_user_id)
    if not removed:
        raise HTTPException(status_code=400, detail="Cannot remove member (might be owner)")

    return {"removed": True}


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

@app.get("/workspaces/{workspace_id}/columns", tags=["Kanban - Columns"])
def list_columns(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List all columns in workspace."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    columns = get_workspace_columns(workspace_id)
    return {"columns": columns}


@app.post("/workspaces/{workspace_id}/columns", status_code=201, tags=["Kanban - Columns"])
def create_column_endpoint(
    workspace_id: int,
    body: CreateColumnRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a custom column."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    column = create_column(workspace_id, body.name, body.position, body.color)
    return {"column": column}


@app.put("/columns/{column_id}", tags=["Kanban - Columns"])
def update_column_endpoint(
    column_id: int,
    body: UpdateColumnRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update column details."""
    # TODO: Add workspace membership check
    column = update_column(column_id, body.name, body.position, body.color)
    return {"column": column}


@app.delete("/columns/{column_id}", tags=["Kanban - Columns"])
def delete_column_endpoint(
    column_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Delete a column (only if no tasks in it)."""
    # TODO: Add workspace membership check
    deleted = delete_column(column_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Cannot delete column (may have tasks)")

    return {"deleted": True}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.get("/workspaces/{workspace_id}/tasks", tags=["Kanban - Tasks"])
def list_tasks(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List all tasks in workspace."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    tasks = get_workspace_tasks(workspace_id)
    return {"tasks": tasks}


@app.post("/workspaces/{workspace_id}/tasks", status_code=201, tags=["Kanban - Tasks"])
def create_task_endpoint(
    workspace_id: int,
    body: CreateTaskRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a new task."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    # Get assignee if email provided
    assignee_id = None
    if body.assignee_email:
        assignee = get_user_by_email(body.assignee_email)
        if not assignee:
            raise HTTPException(status_code=404, detail=f"User {body.assignee_email} not found")

        if not is_workspace_member(workspace_id, assignee["id"]):
            raise HTTPException(status_code=400, detail="Assignee is not a workspace member")

        assignee_id = assignee["id"]

    task = create_task(
        workspace_id,
        body.column_id,
        body.title,
        body.description,
        user_id,
        assignee_id,
        body.priority,
        body.due_date,
        body.position,
    )

    # Send email to assignee
    if assignee_id:
        from email_notifications import send_task_assigned_email
        assignee = get_user_by_email(body.assignee_email)
        reporter = get_user_by_id(user_id)
        workspace = get_workspace(workspace_id)

        task_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/tasks/{task['id']}"
        send_task_assigned_email(
            assignee["email"],
            task["title"],
            task["description"],
            reporter["username"] if reporter else "A team member",
            workspace["name"],
            task_link,
            task["priority"],
            task["due_date"]
        )

    return {"task": task}


@app.get("/tasks/{task_id}", tags=["Kanban - Tasks"])
def get_task_endpoint(
    task_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get task details with comments and attachments."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    comments = get_task_comments(task_id)
    attachments = get_task_attachments(task_id)

    return {
        "task": task,
        "comments": comments,
        "attachments": attachments,
    }


@app.patch("/tasks/{task_id}", tags=["Kanban - Tasks"])
def update_task_endpoint(
    task_id: int,
    body: UpdateTaskRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update task details (move columns, edit, reassign, etc.)."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    # Get assignee if email provided
    assignee_id = None
    if body.assignee_email is not None:
        if body.assignee_email:  # Not empty string
            assignee = get_user_by_email(body.assignee_email)
            if not assignee:
                raise HTTPException(status_code=404, detail=f"User {body.assignee_email} not found")
            assignee_id = assignee["id"]
        # else: empty string means unassign (assignee_id stays None)

    updated_task = update_task(
        task_id,
        title=body.title,
        description=body.description,
        column_id=body.column_id,
        assignee_id=assignee_id,
        priority=body.priority,
        due_date=body.due_date,
        position=body.position,
        user_id=user_id,
    )

    return {"task": updated_task}


@app.delete("/tasks/{task_id}", tags=["Kanban - Tasks"])
def delete_task_endpoint(
    task_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Delete a task."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    deleted = delete_task(task_id, user_id)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@app.post("/tasks/{task_id}/comments", status_code=201, tags=["Kanban - Comments"])
def add_comment(
    task_id: int,
    body: CreateCommentRequest,
    current_user: dict = Depends(get_current_user),
):
    """Add a comment to a task."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    comment = create_task_comment(task_id, user_id, body.comment)
    return {"comment": comment}


@app.get("/tasks/{task_id}/comments", tags=["Kanban - Comments"])
def list_comments(
    task_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List all comments for a task."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    comments = get_task_comments(task_id)
    return {"comments": comments}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@app.post("/tasks/{task_id}/attachments", status_code=201, tags=["Kanban - Attachments"])
async def upload_attachment(
    task_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload a file attachment to a task."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    # Save file
    file_ext = Path(file.filename).suffix if file.filename else ""
    safe_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = UPLOADS_DIR / safe_filename

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    file_url = f"/uploads/{safe_filename}"
    file_size = len(content)

    attachment = create_task_attachment(
        task_id, user_id, file.filename or safe_filename, file_url, file.content_type, file_size
    )

    return {"attachment": attachment}


@app.get("/tasks/{task_id}/attachments", tags=["Kanban - Attachments"])
def list_attachments(
    task_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List all attachments for a task."""
    user_id = int(current_user["sub"])

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not is_workspace_member(task["workspace_id"], user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    attachments = get_task_attachments(task_id)
    return {"attachments": attachments}


# ---------------------------------------------------------------------------
# Activity Log
# ---------------------------------------------------------------------------

@app.get("/workspaces/{workspace_id}/activity", tags=["Kanban - Activity"])
def get_activity(
    workspace_id: int,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Get recent activity log for a workspace."""
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    activity = get_workspace_activity(workspace_id, limit)
    return {"activity": activity}


# ---------------------------------------------------------------------------
# Analytics & Dashboard
# ---------------------------------------------------------------------------

@app.get("/workspaces/{workspace_id}/analytics", tags=["Kanban - Analytics"])
def get_workspace_analytics_endpoint(
    workspace_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Get comprehensive analytics for a workspace.

    Returns:
    - Task distribution by column (for Kanban view)
    - Task distribution by priority (pie chart)
    - Task distribution by assignee (bar chart)
    - Overdue tasks list
    - Completion rate
    - Tasks created over time (line chart)
    - Member activity stats
    """
    user_id = int(current_user["sub"])

    if not is_workspace_member(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    analytics = get_workspace_analytics(workspace_id)
    return analytics


@app.get("/analytics/me", tags=["Kanban - Analytics"])
def get_my_analytics(current_user: dict = Depends(get_current_user)):
    """
    Get analytics for the current user across all workspaces.

    Returns:
    - My assigned tasks
    - Tasks by status
    - Overdue count
    - Tasks by workspace
    - Activity stats (tasks created, comments, files)
    """
    user_id = int(current_user["sub"])
    analytics = get_user_analytics(user_id)
    return analytics


@app.get("/analytics/global", tags=["Kanban - Analytics"])
def get_global_analytics_endpoint(current_user: dict = Depends(get_current_user)):
    """
    Get system-wide analytics (for admins or overview dashboard).

    Returns:
    - Total workspaces, users, tasks
    - Most active workspaces
    - Most active users
    - Tasks trend over time
    """
    # Note: In a real app, you might want to restrict this to admins only
    analytics = get_global_analytics()
    return analytics


@app.get("/dashboard/summary", tags=["Kanban - Dashboard"])
def get_dashboard_summary(current_user: dict = Depends(get_current_user)):
    """
    Get a quick summary for the user's dashboard.

    Returns key metrics for quick overview.
    """
    user_id = int(current_user["sub"])

    with _get_conn() as conn:
        # My workspaces count
        workspaces_count = conn.execute(
            """
            SELECT COUNT(DISTINCT w.id)
            FROM workspaces w
            JOIN workspace_members wm ON w.id = wm.workspace_id
            WHERE wm.user_id = ?
            """,
            (user_id,),
        ).fetchone()[0]

        # My active tasks (not in Done)
        active_tasks = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ? AND c.name != 'Done'
            """,
            (user_id,),
        ).fetchone()[0]

        # My overdue tasks
        overdue_tasks = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ?
              AND t.due_date < datetime('now')
              AND c.name != 'Done'
            """,
            (user_id,),
        ).fetchone()[0]

        # My completed tasks (this week)
        completed_this_week = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks t
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ?
              AND c.name = 'Done'
              AND t.updated_at >= datetime('now', '-7 days')
            """,
            (user_id,),
        ).fetchone()[0]

        # Pending invitations
        user = get_user_by_id(user_id)
        pending_invitations = 0
        if user:
            from database import get_pending_invitations_by_email
            pending_invitations = len(get_pending_invitations_by_email(user["email"]))

        # Recent tasks assigned to me (next 5 due)
        recent_tasks = _rows(
            conn,
            """
            SELECT t.id, t.title, t.priority, t.due_date, w.name as workspace_name, c.name as column_name
            FROM tasks t
            JOIN workspaces w ON t.workspace_id = w.id
            JOIN board_columns c ON t.column_id = c.id
            WHERE t.assignee_id = ? AND c.name != 'Done'
            ORDER BY t.due_date ASC NULLS LAST
            LIMIT 5
            """,
            (user_id,),
        )

    return {
        "workspaces_count": workspaces_count,
        "active_tasks": active_tasks,
        "overdue_tasks": overdue_tasks,
        "completed_this_week": completed_this_week,
        "pending_invitations": pending_invitations,
        "upcoming_tasks": recent_tasks,
    }
