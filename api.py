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

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from google.oauth2 import service_account
from googleapiclient.discovery import build
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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


class QuoteRequest(BaseModel):
    topic: str | None = None
    mood: str | None = None


class QuoteResponse(BaseModel):
    quote: str


class LessonPlanResponse(BaseModel):
    lesson_plan: str


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
# Quote Builder API
# ---------------------------------------------------------------------------

@app.post("/quote/generate", response_model=QuoteResponse, tags=["Tools"])
async def generate_quote_endpoint(
    body: QuoteRequest | None = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate an AI-powered two-liner quote.
    User can provide optional 'topic' and 'mood'.
    """
    from quote_service import generate_quote
    topic = body.topic if body else None
    mood = body.mood if body else None
    quote = await generate_quote(topic=topic, mood=mood)
    return QuoteResponse(quote=quote)


# ---------------------------------------------------------------------------
# Lesson Planner API
# ---------------------------------------------------------------------------

@app.post("/lesson-plan/generate", response_model=LessonPlanResponse, tags=["Tools"])
async def generate_lesson_plan_endpoint(
    grade: str = Form(...),
    topic: str = Form(...),
    criteria: str = Form(...),
    raw_text: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Generate a high-quality lesson plan.
    Inputs: grade, topic, criteria, and optional raw_text or file (PDF/DOCX).
    """
    from lesson_plan_service import generate_lesson_plan
    from document_parser import extract_text_from_file

    additional_context = raw_text or ""
    
    if file:
        # Save temporary file to extract text
        temp_path = UPLOADS_DIR / f"temp_lp_{uuid.uuid4().hex}_{file.filename}"
        try:
            content = await file.read()
            with open(temp_path, "wb") as f:
                f.write(content)
            
            # Extract text
            extracted_text = extract_text_from_file(temp_path)
            additional_context += f"\n\n[Extracted from uploaded file {file.filename}]:\n{extracted_text}"
        finally:
            if temp_path.exists():
                os.remove(temp_path)

    lesson_plan = await generate_lesson_plan(
        grade=grade, 
        topic=topic, 
        criteria=criteria, 
        additional_context=additional_context
    )
    return LessonPlanResponse(lesson_plan=lesson_plan)


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


# ---------------------------------------------------------------------------
# Quote Builder API
# ---------------------------------------------------------------------------

@app.post("/quote/generate", response_model=QuoteResponse, tags=["Tools"])
async def generate_quote_endpoint(
    body: QuoteRequest | None = None,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Generate an AI-powered two-liner quote.
    User can provide optional 'topic' and 'mood'.
    """
    from quote_service import generate_quote
    topic = body.topic if body else None
    mood = body.mood if body else None
    quote = await generate_quote(topic=topic, mood=mood)
    return QuoteResponse(quote=quote)


# ---------------------------------------------------------------------------
# Lesson Planner API
# ---------------------------------------------------------------------------

@app.post("/lesson-plan/generate", response_model=LessonPlanResponse, tags=["Tools"])
async def generate_lesson_plan_endpoint(
    grade: str = Form(...),
    topic: str = Form(...),
    criteria: str = Form(...),
    raw_text: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Generate a high-quality lesson plan.
    Inputs: grade, topic, criteria, and optional raw_text or file (PDF/DOCX).
    """
    from lesson_plan_service import generate_lesson_plan
    from document_parser import extract_text_from_file

    additional_context = raw_text or ""
    
    if file:
        # Save temporary file to extract text
        temp_path = UPLOADS_DIR / f"temp_lp_{uuid.uuid4().hex}_{file.filename}"
        try:
            content = await file.read()
            with open(temp_path, "wb") as f:
                f.write(content)
            
            # Extract text
            extracted_text = extract_text_from_file(temp_path)
            additional_context += f"\n\n[Extracted from uploaded file {file.filename}]:\n{extracted_text}"
        finally:
            if temp_path.exists():
                os.remove(temp_path)

    lesson_plan = await generate_lesson_plan(
        grade=grade, 
        topic=topic, 
        criteria=criteria, 
        additional_context=additional_context
    )
    return LessonPlanResponse(lesson_plan=lesson_plan)
