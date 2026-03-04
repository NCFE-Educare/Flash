"""FastAPI app with signup, login, sessions, protected chat, and Gmail OAuth."""

import asyncio
import concurrent.futures
import sys
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

from auth import create_access_token, decode_access_token, hash_password, verify_password
from database import (
    add_message,
    create_session,
    create_user,
    delete_gmail_tokens,
    delete_session,
    get_gmail_tokens,
    get_messages,
    get_session,
    get_sessions_for_user,
    get_user_by_email,
    init_db,
    rename_session,
    save_claude_session_id,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="EduCare Bots API", version="1.0.0")


@app.on_event("startup")
def on_startup():
    init_db()


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


class ChatResponse(BaseModel):
    reply: str
    user: str
    session_id: int


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

    # Save the current user message
    add_message(session_id, role="user", content=body.message)

    # Run the agent — tries native resume first, falls back to DB history if needed
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        reply, new_claude_session_id = await loop.run_in_executor(
            pool, _run_agent_in_thread, body.message, user_id, claude_session_id, history
        )

    # Save the assistant reply
    add_message(session_id, role="assistant", content=reply)

    # Persist the SDK session ID so the next message in this session can resume
    if new_claude_session_id:
        save_claude_session_id(session_id, new_claude_session_id)

    return ChatResponse(reply=reply, user=current_user["username"], session_id=session_id)


# ---------------------------------------------------------------------------
# Agent runner (Windows ProactorEventLoop workaround)
# ---------------------------------------------------------------------------

def _run_agent_in_thread(
    user_message: str,
    user_id: int,
    claude_session_id: str | None,
    history: list[dict],
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
            _run_agent(user_message, user_id, claude_session_id, history)
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
        lines.append(f"{role_label}: {msg['content']}")

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
    from sub_agent import data_processor_agent, email_drafter_agent, gmail_agent
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
            },
            agents={
                "data_processor": data_processor_agent,
                "email_drafter": email_drafter_agent,
                "gmail_agent": gmail_agent,
            },
            tools=["Skill", "Task", "Bash", "Read", "Write"],
            allowed_tools=["Skill", "Task", "Bash", "Read", "Write"],
            setting_sources=["project"],
            system_prompt=(
                f"You are working in a restricted directory. Always use RELATIVE paths. "
                f"Your working directory is: {agent_cwd}. "
                f"The current user's ID is: {user_id}. "
                "IMPORTANT — always delegate using the Task tool, never handle these yourself:\n"
                "- Mock data / file processing → delegate to 'data_processor' subagent\n"
                "- Drafting emails (no Gmail account needed) → delegate to 'email_drafter' subagent\n"
                f"- ANY Gmail task (read, search, send, reply, trash, labels, profile, etc.) → "
                f"delegate to 'gmail_agent' subagent. Always include 'user_id={user_id}' in the task prompt."
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
        reply = "\n".join(text_chunks) if text_chunks else "(no response)"
        return reply, new_sid

    # ── PRIMARY: native SDK session resumption ──────────────────────────────
    if claude_session_id:
        try:
            return await _execute(_make_options(claude_session_id), user_message)
        except Exception as primary_err:
            print(
                f"\n[Memory] SDK resume failed for session '{claude_session_id}': "
                f"{primary_err}\n"
                f"[Memory] Falling back to full DB history reconstruction.\n"
            )

    # ── FALLBACK: rebuild full context from DB messages (no cap) ────────────
    full_message = _build_context_from_db(history, user_message)
    return await _execute(_make_options(None), full_message)
