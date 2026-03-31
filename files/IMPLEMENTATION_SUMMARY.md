# Google Chat Integration - Your Backend Implementation

## TL;DR: What You Need to Do

1. **Copy code:** Add `gchat_webhook_integration.py` functions to your `api.py`
2. **Get credentials:** Download `service-account.json` from Google Cloud
3. **Configure bot:** Register bot in Google Chat API Configuration
4. **Set env vars:** Add `GCHAT_BOT_NAME`, `GCHAT_BOT_USER_ID`, `GCHAT_WEBHOOK_URL` to `.env`
5. **Test:** Mention bot in Google Chat → see it respond with Claude's answer
6. **Deploy:** Update webhook URL when you go live

---

## How Your Backend Handles It

### Your Existing Flow (Unchanged)
```
Frontend → POST /chat → Your Agent → Response
(Users log in, create sessions, chat normally)
```

### New Google Chat Flow (Added)
```
Google Chat @mention → POST /webhooks/google-chat 
    ↓
Extract message + find/create session
    ↓
INSERT message to your messages table
    ↓
Call your existing _run_agent_in_thread()
    ↓
Get response from Claude agent
    ↓
INSERT response to messages table
    ↓
Send response back to Google Chat via API
    ↓
User sees bot's response in Google Chat!
```

### Data Flow

```
Google Chat Space (Group or DM)
    ↓
Webhook @ /webhooks/google-chat
    ↓
Query: users table (find user by email)
    ↓
Query/Create: sessions table (one session per space)
    ↓
INSERT/SELECT: messages table (full context)
    ↓
_run_agent_in_thread() (your existing function!)
    ↓
Claude SDK Client (with Mem0, MCP servers, etc.)
    ↓
Response saved to messages table
    ↓
Google Chat API sends message back
```

---

## Files You Have

### From This Integration
1. **`gchat_webhook_integration.py`** — Drop into api.py (or import)
2. **`SETUP_GCHAT_FOR_YOUR_BACKEND.md`** — Step-by-step guide

### Your Existing Files (Unchanged)
- **`api.py`** — Just add the webhook code
- **`database.py`** — Already handles it perfectly
- **`agent_config.py`** — Already configured for agent
- **`memory.py`** — Already saves to Mem0
- **`service-account.json`** — Download from Google Cloud

---

## The Key Integration Points

### 1. User Lookup
```python
user = get_user_by_email(sender_email)  # Your existing function!
if not user:
    # User must log in first
    return None
```

### 2. Session Management
```python
session = get_or_create_gchat_session(space_name, sender_email)
session_id = session["id"]
```

### 3. Message Storage
```python
add_message(session_id, role="user", content=message)  # Your existing function!
```

### 4. Context Retrieval
```python
history = get_messages(session_id)  # Your existing function!
# Full conversation history for Claude!
```

### 5. Agent Execution
```python
reply, new_session_id = _run_agent_in_thread(
    session_id, cleaned_message, user_id, 
    claude_session_id, history, [], []
)  # YOUR EXISTING FUNCTION! No changes needed.
```

### 6. Response Saving
```python
add_message(session_id, role="assistant", content=reply)  # Your existing function!
```

### 7. Response Sending
```python
send_gchat_message(space_name, reply)  # NEW function (provided)
```

---

## What Google Chat Sessions Look Like in Your DB

### Personal/DM Chat

```
Scenario: Alice uses bot in her DM

users table:
id | email              | username
42 | alice@company.com  | alice_smith

sessions table:
id | user_id | title                  | claude_session_id
10 | 42      | Google Chat: alice@... | sess_abc123xyz

messages table:
id | session_id | role      | content
100| 10         | user      | analyze my emails
101| 10         | assistant | I found 5 unread emails...
102| 10         | user      | summarize them
103| 10         | assistant | Here's a summary...
```

When Alice sends another message in same DM:
→ Finds session_id = 10 (REUSE)
→ Fetches all 4 previous messages (full context!)
→ Claude responds with awareness of full conversation

---

### Group Chat

```
Scenario: Team (Alice, Bob, Charlie) in "Q3Planning" group

users table:
id | email              | username
42 | alice@company.com  | alice_smith
43 | bob@company.com    | bob_jones
44 | charlie@company.com| charlie_brown

sessions table:
id | user_id | title                  | claude_session_id
10 | NULL    | Google Chat Group: ... | sess_xyz789abc

messages table:
id | session_id | role      | content (from multiple people)
200| 10         | user      | @agent what's the Q3 budget?      (Alice)
201| 10         | assistant | Your Q3 budget is $500K
202| 10         | user      | when does Q3 start?                (Bob)
203| 10         | assistant | Q3 starts July 1st
204| 10         | user      | what are our KPIs?                 (Charlie)
205| 10         | assistant | Your KPIs are: Revenue, Users, ...
```

Key difference: `session.user_id = NULL` (group session, not one person)

When anyone mentions bot:
→ Finds session_id = 10 (SAME for whole group)
→ Fetches ALL messages (all team members' questions)
→ Claude has full group context!

---

## Database Queries for Google Chat

Your existing functions handle all of this, but here's the SQL:

### Find or Create Session

```sql
-- For DM
SELECT id FROM sessions 
WHERE title LIKE '%spaces/DM/ABC123%' 
AND user_id = 42
LIMIT 1;

-- If not found:
INSERT INTO sessions (user_id, title, created_at, updated_at)
VALUES (42, 'Google Chat DM spaces/DM/ABC123', NOW(), NOW());
```

```sql
-- For Group
SELECT id FROM sessions 
WHERE title LIKE '%spaces/ABC123%' 
AND user_id IS NULL
LIMIT 1;

-- If not found:
INSERT INTO sessions (user_id, title, created_at, updated_at)
VALUES (NULL, 'Google Chat Group spaces/ABC123', NOW(), NOW());
```

### Fetch Context

```sql
-- Get all messages in this session (order by date)
SELECT id, role, content, created_at 
FROM messages 
WHERE session_id = 10 
ORDER BY created_at ASC;

-- Result: Full conversation history!
```

### Save Messages

```sql
-- User message
INSERT INTO messages (session_id, role, content, created_at)
VALUES (10, 'user', 'what is X?', NOW());

-- Bot response
INSERT INTO messages (session_id, role, content, created_at)
VALUES (10, 'assistant', 'X is...', NOW());
```

---

## Environment Variables

Add to your `.env`:

```env
# Google Chat Bot Configuration
GCHAT_BOT_NAME=agent                                    # Your bot's @mention name
GCHAT_BOT_USER_ID=users/1234567890123456789            # From Google Cloud
GCHAT_WEBHOOK_URL=https://your-domain.com/webhooks/google-chat

# For local testing with ngrok:
GCHAT_WEBHOOK_URL=https://abc-123-xyz.ngrok.io/webhooks/google-chat
```

---

## What Actually Happens When Someone Mentions Your Bot

### Step 1: Google Chat → Your Server
```
Google Chat detects: @agent hello world
Google sends POST to: https://your-domain.com/webhooks/google-chat
Body contains:
{
  "type": "MESSAGE",
  "message": {
    "text": "@agent hello world",
    "sender": {
      "email": "alice@company.com"
    }
  },
  "space": {
    "name": "spaces/Q3PLAN789"
  }
}
```

### Step 2: Webhook Handler
```python
# Your code (from gchat_webhook_integration.py):

# 1. Extract data
space_name = "spaces/Q3PLAN789"
sender_email = "alice@company.com"
message_text = "@agent hello world"

# 2. Check if bot was mentioned
is_mentioned = ("@agent" in message_text)  # YES

# 3. Clean message (remove mention)
cleaned = "hello world"

# 4. Queue for background processing
background_tasks.add_task(process_gchat_mention, ...)

# 5. Return immediately (webhook timeout is ~3 sec)
return {"status": "ok"}
```

### Step 3: Background Processing
```python
def process_gchat_mention(space_name, sender_email, cleaned_message):
    
    # 1. Find user
    user = get_user_by_email("alice@company.com")
    user_id = user["id"]  # 42
    
    # 2. Get or create session
    session = get_or_create_gchat_session(space_name, sender_email)
    session_id = session["id"]  # 10
    
    # 3. Save user message
    add_message(session_id=10, role="user", content="hello world")
    
    # 4. Fetch context
    history = get_messages(session_id=10)
    # Returns: all previous messages in this session
    
    # 5. Get Claude's response
    reply, new_session_id = _run_agent_in_thread(
        session_id=10,
        user_message="hello world",
        user_id=42,
        claude_session_id=session.get("claude_session_id"),
        history=history,
        image_urls=[],
        document_urls=[]
    )
    # Your existing function does all the work!
    
    # 6. Save response
    add_message(session_id=10, role="assistant", content=reply)
    
    # 7. Send back to Google Chat
    send_gchat_message(space_name, reply)
```

### Step 4: User Sees Response
```
In Google Chat:
Alice (9:00 AM): @agent hello world
Claude Bot (9:00 AM): Hello! I'm Claude, your AI assistant...
```

**And that's it!** The entire cycle takes 5-15 seconds in the background.

---

## Security Model

### Authentication
- ✅ User must be logged in to your web app first
- ✅ Webhook doesn't authenticate (Google Chat does)
- ✅ Bot looks up user by email and checks if account exists
- ✅ If user not found → error message (they must log in first)

### Privacy
- ✅ Messages stored in your database (you control it)
- ✅ OAuth tokens stored encrypted (already in your system)
- ✅ Group responses are public (visible to all group members)
- ✅ DM responses are private (only visible to that user)

### Rate Limiting
- ✅ Your agent naturally rate-limits (processes one at a time)
- ✅ If overwhelmed, Google Chat will retry your webhook
- ✅ Failed messages are logged for debugging

---

## Testing Checklist

- [ ] service-account.json in project root
- [ ] .env has GCHAT_BOT_NAME, GCHAT_BOT_USER_ID, GCHAT_WEBHOOK_URL
- [ ] Webhook code added to api.py
- [ ] Backend starts without errors: `python api.py`
- [ ] ngrok running: `ngrok http 8000`
- [ ] Webhook URL updated in Google Chat API Configuration
- [ ] Google Chat bot added to test space
- [ ] Mention bot: `@agent hello`
- [ ] See response in Google Chat ✓
- [ ] Check logs for `[GChat] Webhook received` ✓
- [ ] Check database: message saved to messages table ✓
- [ ] Send follow-up: see previous context in response ✓

---

## Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| Bot doesn't respond | Check logs for `[GChat]` messages. Update webhook URL if needed. |
| "User not found" error | User must log in to your web app first. |
| No context from previous messages | Check messages table has messages saved for session_id. |
| service-account.json error | Make sure file is in project root, not in a subfolder. |
| ngrok URL keeps changing | Use permanent URL (deploy to production) or re-update ngrok URL. |
| Message takes too long | Agent is processing — can take 10-30 sec depending on complexity. |

---

## Deployment Checklist

When going to production:

- [ ] service-account.json loaded from env var or secure storage (NOT in git)
- [ ] GCHAT_BOT_NAME, GCHAT_BOT_USER_ID, GCHAT_WEBHOOK_URL in production .env
- [ ] Update webhook URL in Google Chat API Configuration to production URL
- [ ] Test in production space (create test space with team)
- [ ] Monitor logs for errors
- [ ] Set up alerts for webhook failures
- [ ] Document for your team how to use the bot

---

## Success Criteria

After implementation, you should have:

✅ Users can mention `@agent-name` in Google Chat (personal or group)
✅ Bot responds with Claude's answer
✅ Full conversation history is available (context windows don't flood)
✅ Works in both DM and group chats
✅ Responses are visible to appropriate people (private in DM, public in group)
✅ Mem0 saves long-term memory about each user
✅ Team can collaborate with the bot in real-time
✅ All data is in your SQLite database (you control everything)

---

## Next Level: Advanced Features

Once basic integration is working, you can add:

1. **Mention-specific responses** — Different behavior based on who mentions
2. **Threading** — Keep bot responses in threads (cleaner for groups)
3. **Slash commands** — `/bot summarize` instead of mentions
4. **File uploads** — Accept files from Google Chat
5. **Reactions** — Bot reacts to messages with ✓, ✗, etc.
6. **Scheduled messages** — Bot sends reminders
7. **Rate limiting** — Prevent abuse (slow down frequent requests)
8. **Analytics** — Track usage, popular queries, etc.

But for now, basic @mentions are perfect! 🚀

---

## Support

If you get stuck:

1. Check logs: Look for `[GChat]` prefixed messages
2. Verify webhook URL: Should match exactly in Google Chat API Configuration
3. Test with curl: `curl -X POST http://localhost:8000/webhooks/google-chat -H "Content-Type: application/json" -d '{"type":"MESSAGE",...}'`
4. Check database: `sqlite3 educare.db "SELECT * FROM sessions;"`
5. Read the code comments in `gchat_webhook_integration.py`

Good luck! Your bot is going to be awesome. 🎉
