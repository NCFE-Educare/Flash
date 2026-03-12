"""FastAPI app with signup, login, sessions, protected chat, and Gmail OAuth."""

import asyncio
import concurrent.futures
import json
import shutil
import sys
import threading
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from auth import create_access_token, decode_access_token, hash_password, verify_password
from database import (
    add_message,
    create_session,
    create_user,
    delete_calendar_tokens,
    delete_docs_tokens,
    delete_drive_tokens,
    delete_gmail_tokens,
    delete_meet_tokens,
    delete_session,
    delete_sheets_tokens,
    delete_slides_tokens,
    delete_forms_tokens,
    get_calendar_tokens,
    get_docs_tokens,
    get_drive_tokens,
    get_gmail_tokens,
    get_meet_tokens,
    get_messages,
    get_session,
    get_sessions_for_user,
    get_sheets_tokens,
    get_slides_tokens,
    get_forms_tokens,
    get_user_by_email,
    init_db,
    rename_session,
    save_claude_session_id,
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
    event = {"type": "response_done", "session_id": session_id}
    with _notification_lock:
        listeners = _notification_listeners.get(user_id, [])
    for q in listeners:
        try:
            q.put(event)
        except Exception:
            pass


@app.on_event("startup")
def on_startup():
    init_db()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


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
                elif event_type == "thinking":
                    yield _sse_event("thinking", {"content": item["content"]})
                elif event_type == "text":
                    yield _sse_event("text", {"content": item["content"]})
                elif event_type == "tool_start":
                    yield _sse_event("tool_start", {"tool": item["tool"]})
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
    Server-Sent Events stream for response-done notifications.
    Connect and keep open. When an agent response completes (in any session),
    you receive: event: response_done, data: {"type":"response_done","session_id":N}
    Use this to refresh the session's messages when the user switched away.
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
                    yield _sse_event("response_done", item)
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
        last_turn_texts: list[str] = []
        sid: str | None = None
        in_tool = False
        current_tool: str | None = None
        amk = _agent_model_keys(opts)
        delegation_stack: list[str] = []
        pending_delegation: str | None = None
        trace_session_start(session_id, user_message)

        async with ClaudeSDKClient(options=opts) as client:
            await client.query(msg)
            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    event = message.event
                    event_type = event.get("type")

                    if event_type == "content_block_start":
                        content_block = event.get("content_block", {})
                        if content_block.get("type") == "tool_use":
                            current_tool = content_block.get("name", "Tool")
                            in_tool = True
                            queue.put({"type": "tool_start", "tool": current_tool})

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta" and not in_tool:
                            chunk = delta.get("text", "")
                            if chunk:
                                queue.put({"type": "thinking", "content": chunk})

                    elif event_type == "content_block_stop":
                        if in_tool and current_tool:
                            queue.put({"type": "tool_end", "tool": current_tool})
                            in_tool = False
                            current_tool = None

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
                    has_tool_use = any(isinstance(b, ToolUseBlock) for b in message.content)
                    turn_texts: list[str] = []
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
                        elif isinstance(block, TextBlock) and block.text:
                            if agent_label != "Main agent":
                                trace_write(f"  --- {agent_label} response ---")
                            trace_write(block.text)
                            assistant_texts.append(block.text)
                            turn_texts.append(block.text)
                    if not has_tool_use and turn_texts:
                        last_turn_texts = turn_texts
                elif isinstance(message, ResultMessage):
                    sid = message.session_id
                    trace_write(f"[Result] turns={message.num_turns} duration={message.duration_ms}ms")

        final_texts = last_turn_texts if last_turn_texts else assistant_texts
        raw = "\n".join(final_texts) if final_texts else "(no response)"
        raw = raw.replace("\\n", "\n").replace("\\t", "\t")

        add_message(session_id, role="assistant", content=raw)
        if sid:
            save_claude_session_id(session_id, sid)

        _broadcast_response_done(user_id, session_id)

        for chunk in final_texts:
            queue.put({"type": "text", "content": chunk})

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
