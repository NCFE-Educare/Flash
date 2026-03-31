# Google Chat Integration for Your Backend
# Add this to your existing api.py file

"""
This module adds Google Chat webhook support to your existing FastAPI backend.
It reuses your existing:
- _run_agent_in_thread() function
- database.py functions (users, sessions, messages)
- agent_config.py system prompt with Mem0, MCP servers, etc.

The webhook receives @mentions in Google Chat and processes them through
your existing agent pipeline, saving context to the same messages table.
"""

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, HTTPException
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Import your existing database functions
from database import (
    add_message,
    create_session,
    get_message,
    get_messages,
    get_session,
    get_user_by_email,
    save_claude_session_id,
)

# ============================================================================
# Google Chat Service Account Setup
# ============================================================================

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
    """
    Send a message to a Google Chat space using the service account bot.
    
    Args:
        space_name: The space ID (e.g., 'spaces/ABC123xyz')
        text: The message text to send
        
    Returns:
        True if successful, False otherwise
    """
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


# ============================================================================
# Session Management for Google Chat
# ============================================================================

def get_or_create_gchat_session(space_name: str, sender_email: str, is_group: bool = False) -> dict | None:
    """
    Get or create a session for a Google Chat space.
    
    For personal/DM: session.user_id = user_id (tied to person)
    For groups: session.user_id = NULL (shared context)
    
    Args:
        space_name: The space ID (e.g., 'spaces/ABC123xyz')
        sender_email: Email of the person who sent the mention
        is_group: True if this is a group chat, False for DM
        
    Returns:
        Session dict or None if user not found/created
    """
    
    # Find or create the user
    user = get_user_by_email(sender_email)
    if not user:
        print(f"[GChat] User {sender_email} not found in DB")
        # User must log in first to create account
        return None
    
    user_id = user["id"]
    
    # For DMs: session tied to user + space
    if not is_group:
        session = get_session_by_space(space_name, user_id)
        if session:
            return session
        
        # Create new DM session
        session = create_session(
            user_id=user_id,
            title=f"Google Chat: {sender_email}"
        )
        return session
    
    # For groups: session shared (user_id = NULL)
    session = get_session_by_space(space_name, user_id=None)
    if session:
        return session
    
    # Create new group session
    session = create_session(
        user_id=None,  # Group session — no specific user
        title=f"Google Chat Group: {space_name}"
    )
    return session


def get_session_by_space(space_name: str, user_id: int | None = None) -> dict | None:
    """
    Fetch an existing session by space_name and optionally user_id.
    
    Query: sessions WHERE space_name LIKE '%space_name%' AND (user_id = ? OR user_id IS NULL)
    """
    from database import _get_conn, _row
    
    with _get_conn() as conn:
        if user_id is not None:
            # DM: specific user in this space
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title LIKE ? AND user_id = ? LIMIT 1",
                (f"%{space_name}%", user_id)
            )
        else:
            # Group: any session for this space (user_id = NULL)
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title LIKE ? AND user_id IS NULL LIMIT 1",
                (f"%{space_name}%",)
            )


# ============================================================================
# Google Chat Webhook Endpoint
# ============================================================================

@app.post("/webhooks/google-chat", tags=["Google Chat"])
async def google_chat_webhook(
    request_data: dict,
    background_tasks: BackgroundTasks
):
    """
    Webhook endpoint for Google Chat mentions.
    
    When a user types @agent-name in Google Chat:
    1. Google sends a POST to this endpoint
    2. We extract the mention and message
    3. We process it through your existing agent pipeline
    4. We save to your messages table (maintains context)
    5. We send response back to Google Chat
    
    No authentication needed — Google Chat webhook signature verification
    should be added for production (see comments below).
    """
    
    # TODO: In production, verify the X-Goog-Chat-Signature header
    # This ensures the request is actually from Google Chat
    # See: https://developers.google.com/chat/api/guides/webhooks#receive_events
    
    print(f"[GChat] Webhook received: {json.dumps(request_data, indent=2)}")
    
    # Validate it's a MESSAGE event
    if request_data.get("type") != "MESSAGE":
        print(f"[GChat] Ignoring non-MESSAGE event: {request_data.get('type')}")
        return {"status": "ok"}
    
    message_data = request_data.get("message", {})
    space_name = request_data.get("space", {}).get("name")
    sender_email = message_data.get("sender", {}).get("email")
    user_message_text = message_data.get("text", "").strip()
    
    if not space_name or not sender_email or not user_message_text:
        print(f"[GChat] Missing required fields")
        return {"status": "error", "message": "Missing fields"}
    
    print(f"[GChat] From: {sender_email}, Space: {space_name}")
    print(f"[GChat] Message: {user_message_text}")
    
    # ========================================================================
    # Check if bot was mentioned
    # ========================================================================
    
    bot_name = os.getenv("GCHAT_BOT_NAME", "agent")
    bot_user_id = os.getenv("GCHAT_BOT_USER_ID", "")
    
    mention_patterns = [
        f"@{bot_name}",
        f"<users/{bot_user_id}>" if bot_user_id else None,
        f"users/{bot_user_id}" if bot_user_id else None,
    ]
    mention_patterns = [p for p in mention_patterns if p]  # Remove None values
    
    is_mentioned = any(pattern in user_message_text for pattern in mention_patterns)
    
    if not is_mentioned:
        print(f"[GChat] Bot not mentioned, ignoring")
        return {"status": "ok"}
    
    print(f"[GChat] Bot mentioned! Processing...")
    
    # Clean the message (remove mention)
    cleaned_message = user_message_text
    for pattern in mention_patterns:
        cleaned_message = cleaned_message.replace(pattern, "").strip()
    
    # Determine if this is a group or DM
    is_group = not space_name.startswith("spaces/dm/")
    
    # Queue for background processing
    background_tasks.add_task(
        process_gchat_mention,
        space_name=space_name,
        sender_email=sender_email,
        cleaned_message=cleaned_message,
        is_group=is_group
    )
    
    # Return immediately (webhook timeout is ~3 seconds, processing happens in background)
    return {"status": "ok"}


# ============================================================================
# Background Task: Process the Mention
# ============================================================================

def process_gchat_mention(
    space_name: str,
    sender_email: str,
    cleaned_message: str,
    is_group: bool
):
    """
    Process the Google Chat mention through your existing agent pipeline.
    
    This runs in the background so the webhook can respond immediately.
    """
    import asyncio
    import concurrent.futures
    import sys
    
    print(f"\n[GChat Background] Processing mention from {sender_email}")
    
    try:
        # STEP 1: Get or create session
        session = get_or_create_gchat_session(
            space_name=space_name,
            sender_email=sender_email,
            is_group=is_group
        )
        
        if not session:
            error_msg = f"❌ User {sender_email} not logged in. Please log in to use the bot."
            send_gchat_message(space_name, error_msg)
            return
        
        session_id = session["id"]
        user_id = session["user_id"] if not is_group else 1  # For groups, use a dummy user_id
        
        print(f"[GChat Background] Session: {session_id}, User: {user_id}")
        
        # STEP 2: Save user's message to DB
        add_message(
            session_id=session_id,
            role="user",
            content=cleaned_message
        )
        
        # STEP 3: Fetch full conversation history for context
        history = get_messages(session_id)
        claude_session_id = session.get("claude_session_id")
        
        print(f"[GChat Background] Context: {len(history)} messages")
        
        # STEP 4: Run agent (reuse your existing function)
        # This is where your Claude agent does its magic
        loop = asyncio.get_event_loop() if hasattr(asyncio, 'get_event_loop') else None
        if not loop or loop.is_closed():
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = loop.run_in_executor(
                pool,
                _run_agent_in_thread,
                session_id,
                cleaned_message,
                user_id,
                claude_session_id,
                history,
                [],  # image_urls (Google Chat doesn't support yet)
                []   # document_urls
            )
            
            reply, new_claude_session_id = loop.run_until_complete(future)
        
        print(f"[GChat Background] Agent response: {reply[:100]}...")
        
        # STEP 5: Save assistant response to DB
        add_message(
            session_id=session_id,
            role="assistant",
            content=reply
        )
        
        # STEP 6: Save new session ID for resumption
        if new_claude_session_id:
            save_claude_session_id(session_id, new_claude_session_id)
        
        # STEP 7: Send response back to Google Chat
        send_gchat_message(space_name, reply)
        
        # STEP 8: Save to Mem0 long-term memory (best-effort)
        try:
            from memory import add_memory
            if not is_group or user_id != 1:
                add_memory(user_id, [
                    {"role": "user", "content": cleaned_message},
                    {"role": "assistant", "content": reply},
                ])
        except Exception as mem_err:
            print(f"[GChat] Mem0 error: {mem_err}")
        
        print(f"[GChat Background] ✅ Complete!")
        
    except Exception as e:
        print(f"[GChat Background] ERROR: {e}")
        import traceback
        traceback.print_exc()
        
        # Send error message to user
        error_msg = f"❌ Error processing request: {str(e)[:100]}"
        send_gchat_message(space_name, error_msg)


# ============================================================================
# Environment Variables Required
# ============================================================================

"""
Add these to your .env file:

GCHAT_BOT_NAME=agent
GCHAT_BOT_USER_ID=1234567890123456789
GCHAT_WEBHOOK_URL=https://your-domain.com/webhooks/google-chat

Also:
- Download service-account.json from Google Cloud Console
- Place it in the same directory as api.py
- Make sure it has Chat Bot role
"""


# ============================================================================
# Helper Function for Group vs DM Detection
# ============================================================================

def is_group_chat(space_name: str) -> bool:
    """
    Determine if a space is a group chat or DM.
    
    DMs have space_name like: 'spaces/dm/abc123xyz'
    Groups have space_name like: 'spaces/abc123xyz'
    """
    return not space_name.startswith("spaces/dm/")


# ============================================================================
# Optional: Query Helper (add to database.py if needed)
# ============================================================================

def get_session_by_space_and_user(space_name: str, user_id: int | None = None) -> dict | None:
    """
    This is already implemented above, but here's the pure DB function version:
    """
    from database import _get_conn, _row
    
    with _get_conn() as conn:
        if user_id is not None:
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title LIKE ? AND user_id = ? LIMIT 1",
                (f"%{space_name}%", user_id)
            )
        else:
            return _row(
                conn,
                "SELECT * FROM sessions WHERE title LIKE ? AND user_id IS NULL LIMIT 1",
                (f"%{space_name}%",)
            )
