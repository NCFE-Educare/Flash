# Google Chat Integration Setup for Your Backend

This guide walks you through integrating Google Chat with your existing FastAPI backend.

## What You Already Have

Your backend already has:
- ✅ `_run_agent_in_thread()` for processing messages
- ✅ `database.py` with users, sessions, messages tables
- ✅ `agent_config.py` with Claude agent, MCP servers, Mem0
- ✅ Authentication and session management
- ✅ Message persistence

**You just need to add the webhook handler!**

---

## Step 1: Get Google Cloud Setup (5 min)

### 1.1: Create Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or use existing: `EduCare Chat Bot`
3. Search for **"Service Accounts"**
4. Click **"Create Service Account"**
   - Name: `educare-chat-bot`
   - Click **"Create and Continue"**

### 1.2: Grant Permissions

1. On "Grant roles" page, select **"Chat Bot"** role
2. Click **"Continue"** → **"Done"**

### 1.3: Create Key

1. Click the service account you just created
2. Go to **"Keys"** tab
3. **"Add Key"** → **"Create new key"**
4. Choose **JSON**
5. Download the file → save as **`service-account.json`** in your project root

**Security:** Add to `.gitignore`:
```
service-account.json
```

### 1.4: Enable Google Chat API

1. Search for **"Google Chat API"** in Cloud Console
2. Click **"Enable"**

---

## Step 2: Register Your Bot (10 min)

### 2.1: Create Chat App

1. In Google Cloud Console, go to **Google Chat API**
2. Click **"Configuration"** tab
3. Fill in:
   - **App name:** `Claude Agent` (or your bot name)
   - **Avatar URL:** Any image URL
   - **Description:** `AI-powered assistant for team collaboration`

### 2.2: Configure Connection

Choose one method:

#### Option A: Webhook (Simpler)
- **Connection Settings:** Select **"HTTP"**
- **Webhook URL:** Leave blank for now (we'll update after deployment)
- For local testing, use **ngrok**: `ngrok http 8000`

#### Option B: Cloud Pub/Sub (More Reliable)
- Create a Pub/Sub topic: `projects/{PROJECT}/topics/gchat-messages`
- This is more complex — stick with webhooks for now

### 2.3: Permissions

Check these:
- ✅ **Join spaces and group conversations**
- ✅ **Direct message**
- ✅ **Create a message response space**

### 2.4: Get Bot User ID

After saving, you'll see:
```
Bot User ID: users/1234567890123456789
```

Save this — you'll need it!

---

## Step 3: Update Your Backend (15 min)

### 3.1: Copy the Webhook Code

Take the content from `gchat_webhook_integration.py` and add it to your `api.py`:

```python
# At the top of api.py, after imports:
from googleapiclient.discovery import build
from google.oauth2 import service_account

# ... your existing imports ...

# Then paste the entire contents of gchat_webhook_integration.py
# (the functions: get_gchat_service, send_gchat_message, get_or_create_gchat_session, google_chat_webhook, process_gchat_mention, etc.)
```

**Alternative:** Import it as a module:
```python
# In api.py
from gchat_webhook_integration import (
    get_gchat_service,
    send_gchat_message,
    google_chat_webhook,
    process_gchat_mention,
)

# Then include the webhook route
@app.post("/webhooks/google-chat", tags=["Google Chat"])
async def google_chat_webhook_handler(request_data: dict, background_tasks: BackgroundTasks):
    return await google_chat_webhook(request_data, background_tasks)
```

### 3.2: Update Your `.env` File

Add:
```env
GCHAT_BOT_NAME=agent
GCHAT_BOT_USER_ID=users/1234567890123456789  (from step 2.4)
GCHAT_WEBHOOK_URL=https://your-domain.com/webhooks/google-chat  (fill in later)
```

### 3.3: Place service-account.json

Copy the JSON file you downloaded in Step 1.3 to your project root (same directory as api.py).

---

## Step 4: Test Locally with ngrok (5 min)

### 4.1: Start Your Backend

```bash
python api.py
# or
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

### 4.2: Expose with ngrok

In another terminal:
```bash
ngrok http 8000
```

You'll see:
```
Forwarding     https://abc-123-xyz.ngrok.io -> http://localhost:8000
```

### 4.3: Update Webhook URL in Google Cloud

1. Go to Google Chat API → **Configuration**
2. **Connection Settings** → **Webhook URL**
3. Enter: `https://abc-123-xyz.ngrok.io/webhooks/google-chat`
4. Click **Save**

⚠️ **Note:** ngrok URL changes when it restarts. You'll need to update it each time, OR use a permanent URL (see "Deploy to Production" below).

---

## Step 5: Test in Google Chat (5 min)

### 5.1: Create a Test Space

1. Open Google Chat: https://chat.google.com
2. Click **"New conversation"** → **"Start a group conversation"**
3. Name: `test-bot`
4. Create

### 5.2: Add Bot to Space

1. In the space, click **"+"** (add members)
2. Search for your bot name (`Claude Agent` from step 2.1)
3. Click to add

### 5.3: Test the Mention

In the chat, type:
```
@agent hello! can you help me?
```

**What should happen:**
1. ✅ Webhook receives the message
2. ✅ Bot processes it through your Claude agent
3. ✅ Message appears in your `messages` table (session = group)
4. ✅ Bot responds with Claude's answer
5. ✅ Response saved to `messages` table

**Expected in Google Chat:**
```
Claude Agent Bot: Hello! I'm Claude, your AI assistant. I'd be happy to help you with...
```

### 5.4: Verify Context

Send another message:
```
@agent summarize what we just talked about
```

The bot should reference the previous message! (Because it's in the `messages` table with the same `session_id`)

---

## Step 6: Deploy to Production (Varies)

Your deployment target will determine the URL. Examples:

### Heroku
```bash
git push heroku main
# Your app gets URL: https://my-app-abc123.herokuapp.com

# Update webhook:
# https://my-app-abc123.herokuapp.com/webhooks/google-chat
```

### Railway
```bash
# Deploy and get URL from Railway dashboard
# Update webhook URL
```

### AWS / DigitalOcean / etc
```bash
# Deploy your app
# Get public URL
# Update webhook in Google Chat API → Configuration
```

**Important:** Make sure `service-account.json` is NOT committed to git. Add to `.gitignore` and load from environment variable:

```python
# In api.py (production version)
if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    )
else:
    credentials = service_account.Credentials.from_service_account_file('service-account.json')
```

---

## How It Works (With Your Existing Code)

### Personal/DM Chat

```
User: @agent analyze my emails
    ↓
Webhook receives
    ↓
Query: get_user_by_email('user@company.com') → user_id = 42
    ↓
Query: get_session(space_name, user_id=42)
    ↓
If exists: reuse session_id
If not: create_session(user_id=42, title='...')
    ↓
INSERT message → messages table (session_id = X)
    ↓
FETCH all messages from session (history = your context)
    ↓
Call _run_agent_in_thread(session_id, message, user_id, history...)
    ↓
INSERT response → messages table
    ↓
send_gchat_message(space, response)
    ↓
User sees response!
```

### Group Chat

```
Group: Q3Planning
  Members: Alice, Bob, Charlie + Bot

Bob: @agent what's the budget?
    ↓
Webhook receives
    ↓
Session: user_id = NULL (it's a group!)
    ↓
INSERT Bob's message → messages (session = group_session)
    ↓
FETCH ALL messages from group (Alice's messages + Bob's)
    ↓
Claude sees full group context
    ↓
Claude responds with awareness of group discussion
    ↓
Response visible to everyone
```

---

## Troubleshooting

### Bot doesn't respond

**Check logs:**
```bash
# Look for:
# [GChat] Webhook received
# [GChat] Bot mentioned
# [GChat Background] Processing...
```

**If no logs:**
- Webhook URL is wrong in Google Chat API → Configuration
- Bot wasn't actually mentioned (try: `@agent-name` exactly as configured)

**If logs show error:**
- service-account.json not found → Make sure it's in project root
- User not logged in → Have them create account first
- Agent error → Check full error in logs

### service-account.json issues

```bash
# Verify it exists
ls -la service-account.json

# Verify it's valid JSON
python -c "import json; json.load(open('service-account.json'))"

# Verify it has Chat Bot role
# Go to Google Cloud Console → Service Accounts → service-account
# Check "Roles" tab
```

### Context not persisting

**Check database:**
```bash
# Verify messages are being saved
sqlite3 educare.db "SELECT * FROM messages ORDER BY created_at DESC LIMIT 5;"

# Verify session exists
sqlite3 educare.db "SELECT * FROM sessions WHERE id = YOUR_SESSION_ID;"
```

### ngrok URL keeps changing

Use a ngrok permanent domain:
```bash
# Upgrade ngrok account (free tier doesn't have this)
# Or use a proper deployment (Heroku, Railway, etc.)
```

---

## Security Checklist

- [ ] `service-account.json` is in `.gitignore`
- [ ] `GCHAT_BOT_USER_ID` and `GCHAT_BOT_NAME` are in `.env`
- [ ] Webhook signature verification added (optional but recommended)
- [ ] Bot responses don't leak private user data
- [ ] Only logged-in users can trigger bot (check in process_gchat_mention)

---

## What Gets Stored Where

| What | Where | Why |
|------|-------|-----|
| User credentials | `users` table | Know who's chatting |
| Conversation threads | `sessions` table | Organize chats by space/group |
| All messages | `messages` table | Full context for Claude |
| Claude SDK session ID | `sessions.claude_session_id` | Resume multi-step operations |
| Mem0 memories | Mem0 platform | Long-term user understanding |

---

## Example: Multi-Message Conversation in Group

**Time: 9:00 AM**
```
Alice: @agent create a spreadsheet of Q3 sales
Bot: Created spreadsheet "Q3 Sales" (ID: 1f5QrcgQvyY...)
```

**Database state:**
```
messages table:
id | session_id | role | content
1  | 50         | user | create a spreadsheet of Q3 sales
2  | 50         | assistant | Created spreadsheet...
```

**Time: 9:30 AM (same group)**
```
Bob: @agent add my region's data to that spreadsheet
```

**Webhook processes:**
1. Fetch all messages from session 50 (has messages 1 and 2)
2. Claude sees: "create a spreadsheet..." and "Created spreadsheet..."
3. Claude understands which spreadsheet Bob means!
4. Claude delegates to sheets_data_agent with correct spreadsheet ID
5. Adds Bob's data

**Database state:**
```
messages table:
id | session_id | role | content
1  | 50         | user | create a spreadsheet of Q3 sales
2  | 50         | assistant | Created spreadsheet...
3  | 50         | user | add my region's data to that spreadsheet
4  | 50         | assistant | Added your region's data...
```

**No context flooding because:**
- Only 4 messages (not millions)
- All related to same spreadsheet
- Claude has full awareness

---

## Next Steps

1. ✅ Complete steps 1-6 above
2. ✅ Test in personal DM
3. ✅ Test in group chat
4. ✅ Deploy to production
5. ✅ Share bot with your team
6. Monitor logs and gather feedback

Good luck! 🚀
