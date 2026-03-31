# Google Chat Integration - Quick Reference (30 min setup)

## 1️⃣ Google Cloud Setup (10 min)

```
Console → Service Accounts → Create → Role: Chat Bot → Create Key (JSON)
Save as: service-account.json (root directory)

Console → Google Chat API → Enable

Console → Google Chat API → Configuration
  - App name: Claude Agent
  - Permissions: Join spaces ✓, Direct message ✓
  - Copy: Bot User ID (you'll need this)
```

## 2️⃣ Your Code (5 min)

Copy `gchat_webhook_integration.py` into your `api.py`:

```python
# Add imports at top
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Add these functions to api.py:
# - get_gchat_service()
# - send_gchat_message()
# - get_or_create_gchat_session()
# - google_chat_webhook() <- @app.post endpoint
# - process_gchat_mention()

# Now your API has: POST /webhooks/google-chat
```

## 3️⃣ Environment Variables (2 min)

Add to `.env`:
```env
GCHAT_BOT_NAME=agent
GCHAT_BOT_USER_ID=users/1234567890123456789
GCHAT_WEBHOOK_URL=https://your-domain.com/webhooks/google-chat
```

## 4️⃣ Local Testing (10 min)

```bash
# Terminal 1: Start your backend
python api.py

# Terminal 2: Expose with ngrok
ngrok http 8000
# Copy the URL: https://abc-123-xyz.ngrok.io

# Update Google Cloud Console → Google Chat API → Configuration
# Webhook URL: https://abc-123-xyz.ngrok.io/webhooks/google-chat
```

## 5️⃣ Test in Google Chat (3 min)

```
Google Chat:
  1. Create space: test-bot
  2. Add bot (search by app name)
  3. Type: @agent hello
  4. ✓ Bot responds with Claude's answer
```

---

## How It Works (TL;DR)

```
User @mentions bot in Google Chat
    ↓
POST /webhooks/google-chat (your endpoint)
    ↓
Find user → get/create session → save message
    ↓
Call your existing _run_agent_in_thread()
    ↓
Claude agent responds (with Mem0, MCP, etc.)
    ↓
Save response → Send back to Google Chat
    ↓
User sees bot's answer!
```

---

## Database Tables Used

| Table | What | Example |
|-------|------|---------|
| `users` | Who's using the bot | email: alice@company.com |
| `sessions` | Conversation threads | title: "Google Chat space ABC123" |
| `messages` | All messages (user + bot) | Provides full context to Claude |

---

## Files You Get

| File | What |
|------|------|
| `gchat_webhook_integration.py` | Copy into api.py |
| `SETUP_GCHAT_FOR_YOUR_BACKEND.md` | Detailed step-by-step guide |
| `IMPLEMENTATION_SUMMARY.md` | Complete overview |
| `GCHAT_GROUP_CHAT_EXPLAINED.md` | DM vs Group differences |

---

## What You Need (Total)

- ✅ service-account.json (download from Google Cloud)
- ✅ Bot registered in Google Chat API (5 min setup)
- ✅ Webhook code added to api.py (~50 lines, already provided)
- ✅ 3 environment variables in .env
- ✅ That's it! Uses your existing DB, agent, Mem0, MCP servers

---

## Deployment

### Local Testing
```bash
ngrok http 8000
# Update webhook URL in Google Cloud Console
```

### Production (Example: Heroku)
```bash
git push heroku main
# Get: https://my-app.herokuapp.com
# Update webhook URL in Google Cloud Console
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check logs for `[GChat]`. Is webhook URL updated? |
| User not found error | User must log in to your web app first |
| No context from previous messages | Messages should be in `messages` table |
| service-account.json not found | Make sure it's in project root, not a subfolder |

---

## Success Check

```
✓ Mentioned bot in Google Chat
✓ Got response with Claude's answer
✓ Full conversation context available
✓ Works in groups and DMs
✓ All data in your database
✓ Ready for production!
```

---

## Key Points

- **Reuses your existing code** — No changes to agent, database, or auth
- **Personal + Group** — Works in DMs and team groups
- **Full context** — Previous messages available to Claude
- **Mem0 integration** — Long-term memory works automatically
- **MCP servers** — Gmail, Sheets, Docs, etc. all work
- **Production ready** — Can scale to your team size

---

## What Happens in the Background

When Alice types `@agent what's Q3 budget?`:

1. Google Chat sends webhook POST to your server (3 sec timeout)
2. Your code extracts message + finds user
3. Returns `{"status": "ok"}` immediately
4. Background task starts processing (happens async)
5. Fetches conversation history from `messages` table
6. Calls Claude agent (with Mem0, MCP, etc.)
7. Claude responds (5-15 sec total)
8. Response saved to `messages` table
9. Sent to Google Chat via API
10. Alice sees response appear in Google Chat

**Total time:** ~10 seconds from mention to response visible

---

## Next Steps

1. Copy files from outputs
2. Follow SETUP_GCHAT_FOR_YOUR_BACKEND.md (steps 1-5)
3. Test locally with ngrok
4. Deploy to production
5. Share bot with your team
6. Celebrate! 🎉

---

## Need Help?

Read:
- SETUP_GCHAT_FOR_YOUR_BACKEND.md (detailed guide)
- IMPLEMENTATION_SUMMARY.md (complete overview)
- gchat_webhook_integration.py (code with comments)
- GCHAT_GROUP_CHAT_EXPLAINED.md (DM vs groups)

Check logs:
```bash
# Look for [GChat] prefixed messages
python api.py 2>&1 | grep GChat
```

Verify database:
```bash
sqlite3 educare.db
> SELECT * FROM sessions;
> SELECT * FROM messages WHERE session_id = YOUR_ID;
```

Good luck! Your Google Chat bot is ready! 🚀
