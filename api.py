"""FastAPI app with signup, login, sessions, protected chat, and Gmail OAuth."""

import asyncio
import concurrent.futures
import shutil
import sys
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from auth import create_access_token, decode_access_token, hash_password, verify_password
from database import (
    add_message,
    create_session,
    create_user,
    delete_gmail_tokens,
    delete_session,
    delete_sheets_tokens,
    get_gmail_tokens,
    get_messages,
    get_session,
    get_sessions_for_user,
    get_sheets_tokens,
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


class ChatResponse(BaseModel):
    reply: str
    user: str
    session_id: int
    image_urls: list[str] = []


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    payload = decode_access_token(credentials.credentials)
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

    # Serialize image list as JSON string for DB storage (empty list → None)
    import json as _json
    image_urls_json = _json.dumps(body.image_urls) if body.image_urls else None

    # Save the current user message (with optional images)
    add_message(session_id, role="user", content=body.message, image_url=image_urls_json)

    # Run the agent — tries native resume first, falls back to DB history if needed
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        reply, new_claude_session_id = await loop.run_in_executor(
            pool, _run_agent_in_thread, body.message, user_id, claude_session_id, history, body.image_urls
        )

    # Save the assistant reply
    add_message(session_id, role="assistant", content=reply)

    # Persist the SDK session ID so the next message in this session can resume
    if new_claude_session_id:
        save_claude_session_id(session_id, new_claude_session_id)

    return ChatResponse(reply=reply, user=current_user["username"], session_id=session_id, image_urls=body.image_urls)


# ---------------------------------------------------------------------------
# Agent runner (Windows ProactorEventLoop workaround)
# ---------------------------------------------------------------------------

def _run_agent_in_thread(
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str] | None = None,
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
            _run_agent(user_message, user_id, claude_session_id, history, image_urls or [])
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _build_context_from_db(history: list[dict], current_message: str) -> str:
    """
    Reconstruct full conversation context from DB messages (no cap).
    Used as fallback when the SDK transcript file is missing / session expired.
    """
    if not history:
        return current_message

    lines = [
        "[CONVERSATION HISTORY — use this to recall all prior context]",
        "",
    ]
    for msg in history:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        line = f"{role_label}: {msg['content']}"
        if msg.get("image_url"):
            import json as _json
            try:
                urls = _json.loads(msg["image_url"])
            except (ValueError, TypeError):
                urls = [msg["image_url"]]
            for url in urls:
                fname = url.lstrip("/").replace("uploads/", "", 1)
                line += f" [attached image: uploads/{fname}]"
        lines.append(line)

    lines += [
        "",
        "[CURRENT MESSAGE]",
        f"User: {current_message}",
    ]
    return "\n".join(lines)


async def _run_agent(
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
    image_urls: list[str] | None = None,
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
    import os
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
    )
    from gmail_tools import gmail_tools_server
    from sheets_tools import sheets_data_server, sheets_format_server, sheets_visual_server
    from sub_agent import (
        data_processor_agent,
        email_drafter_agent,
        gmail_agent,
        sheets_agent,
    )
    from tools import my_tools_server

    agent_cwd = Path(__file__).parent / "cwd"
    agent_cwd.mkdir(exist_ok=True)

    def _build_cli_env() -> dict[str, str]:
        env: dict[str, str] = {}
        if os.environ.get("USE_BEDROCK", "").strip() == "1":
            env["CLAUDE_CODE_USE_BEDROCK"] = "1"
            env["AWS_REGION"] = os.environ.get("AWS_REGION", "us-east-1")
        return env

    def _make_options(resume_id: str | None) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            mcp_servers={
                "my_tools": my_tools_server,
                "gmail_tools": gmail_tools_server,
                "sheets_data": sheets_data_server,
                "sheets_format": sheets_format_server,
                "sheets_visual": sheets_visual_server,
            },
            agents={
                "data_processor": data_processor_agent,
                "email_drafter": email_drafter_agent,
                "gmail_agent": gmail_agent,
                "sheets_agent": sheets_agent,
            },
            tools=["Skill", "Task", "Bash", "Read", "Write"],
            allowed_tools=["Skill", "Task", "Bash", "Read", "Write"],
            setting_sources=["project"],
            system_prompt=(
                f"You are working in a restricted directory. Always use RELATIVE paths. "
                f"Your working directory is: {agent_cwd}. "
                f"The current user's ID is: {user_id}. "

                "\n\n=== RESPONSE FORMATTING RULES (follow strictly) ===\n"
                "- Use Markdown to make responses readable and scannable.\n"
                "- Use **bold** for key terms, headings, and important points.\n"
                "- Use *italics* for subtle emphasis where helpful.\n"
                "- Use bullet lists (- or *) for options, steps, or multiple items.\n"
                "- Use numbered lists (1. 2. 3.) for ordered steps or procedures.\n"
                "- Use markdown tables when presenting structured data (columns/rows).\n"
                "- Use emojis sparingly (1–3 per response) for clarity — e.g. ✅ 📋 📊 — not in every sentence.\n"
                "- Keep responses concise and friendly. Avoid walls of text.\n"

                "\n=== DELEGATION RULES (always use Task tool, never handle yourself) ===\n"
                "- Mock data / file processing → delegate to 'data_processor' subagent\n"
                "- Drafting emails (no Gmail account needed) → delegate to 'email_drafter' subagent\n"
                f"- ANY Gmail task (read, search, send, reply, trash, labels, profile, etc.) → "
                f"delegate to 'gmail_agent' subagent. Always include 'user_id={user_id}' in the task prompt.\n"
                f"- ANY Google Sheets task (create spreadsheet, read/write data, formatting, charts, "
                f"conditional formatting, dropdowns, sparklines, worksheet management, etc.) → "
                f"delegate to 'sheets_agent' subagent. Always include 'user_id={user_id}' in the task prompt."
            ),
            permission_mode="bypassPermissions",
            cwd=str(agent_cwd),
            env=_build_cli_env(),
            resume=resume_id,
        )

    async def _execute(options: ClaudeAgentOptions, msg: str) -> tuple[str, str | None]:
        text_chunks: list[str] = []
        new_sid: str | None = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(msg)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_chunks.append(block.text)
                elif isinstance(message, ResultMessage):
                    new_sid = message.session_id
        raw = "\n".join(text_chunks) if text_chunks else "(no response)"
        # Decode literal escape sequences some models emit
        raw = raw.replace("\\n", "\n").replace("\\t", "\t")
        # Return raw markdown so frontend can render bold, lists, tables, etc.
        return raw, new_sid

    # If the user attached images, append a note so the agent reads them
    effective_message = user_message
    if image_urls:
        paths = ", ".join(
            f"uploads/{url.lstrip('/').replace('uploads/', '', 1)}"
            for url in image_urls
        )
        count = len(image_urls)
        label = "image" if count == 1 else f"{count} images"
        effective_message = (
            f"{user_message}\n\n"
            f"[The user has attached {label}. Use the Read tool to view "
            f"{'it' if count == 1 else 'each one'} at the relative "
            f"{'path' if count == 1 else 'paths'}: {paths}]"
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
