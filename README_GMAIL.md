# Gmail Integration — Setup Guide

This guide explains how to connect Gmail to EduCare Bots so users can read, search,
compose, and send emails directly through the AI chatbot.

---

## Table of Contents

1. [Install Python Dependencies](#1-install-python-dependencies)
2. [Google Cloud Console Setup](#2-google-cloud-console-setup)
3. [Configure Your .env File](#3-configure-your-env-file)
4. [How the OAuth Flow Works (User Side)](#4-how-the-oauth-flow-works-user-side)
5. [API Endpoints Reference](#5-api-endpoints-reference)
6. [Where the MCP Server Sits in the Architecture](#6-where-the-mcp-server-sits-in-the-architecture)
7. [What Users Can Say to the Chatbot](#7-what-users-can-say-to-the-chatbot)
8. [Going to Production](#8-going-to-production)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Install Python Dependencies

Run this once in your project folder:

```bash
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

These are official Google libraries — stable, well maintained.

---

## 2. Google Cloud Console Setup

> You do this **once**. Every user of your app shares the same Client ID.

### Step 1 — Create a Project

1. Go to https://console.cloud.google.com
2. Click the project dropdown at the top → **New Project**
3. Name it something like `EduCare Bots` → click **Create**

### Step 2 — Enable the Gmail API

1. In the left sidebar go to **APIs & Services → Library**
2. Search for **Gmail API**
3. Click it → click **Enable**

### Step 3 — Configure the OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (so any Google account can connect) → click **Create**
3. Fill in:
   - **App name**: `EduCare Bots`
   - **User support email**: your email
   - **Developer contact email**: your email
4. Click **Save and Continue**
5. On the **Scopes** screen, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/gmail.settings.basic`
6. Click **Save and Continue**
7. On the **Test users** screen:
   - While your app is in **Testing** mode (default), add the Gmail addresses that
     should be allowed to connect. Add your own email here first.
   - You can add up to 100 test users without going through Google's verification.
8. Click **Save and Continue** → **Back to Dashboard**

### Step 4 — Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. For **Application type** choose **Web application**
   > ⚠️ Do NOT choose "Desktop app" — that won't work for a web server.
4. Give it a name like `EduCare Bots Web Client`
5. Under **Authorized redirect URIs**, click **Add URI** and enter:
   ```
   http://localhost:8000/auth/gmail/callback
   ```
   (For production you'll add your real domain here too, see Section 8)
6. Click **Create**
7. A popup will show your **Client ID** and **Client Secret** — copy both.

---

## 3. Configure Your .env File

Open `.env` in the project root and fill in the two values you just copied:

```env
GOOGLE_CLIENT_ID=123456789-abcdefghijklmnop.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/gmail/callback
```

> Never commit these values to Git. The `.env` file should be in your `.gitignore`.

---

## 4. How the OAuth Flow Works (User Side)

Here is the exact sequence a user goes through to connect their Gmail:

```
1. User logs into your chatbot (gets a JWT token)

2. User calls:
   GET /auth/gmail/connect
   Authorization: Bearer <their JWT>

   Response:
   { "auth_url": "https://accounts.google.com/o/oauth2/auth?..." }

3. Frontend opens that URL in the browser (or a popup)

4. User sees Google's consent screen:
   "EduCare Bots wants to access your Gmail"
   ✅ Read your emails
   ✅ Compose and send emails
   ✅ Manage labels
   → User clicks "Allow"

5. Google redirects the browser to:
   http://localhost:8000/auth/gmail/callback?code=...&state=...

6. Your backend exchanges the code for tokens and saves them.
   The browser shows a success page:
   "✅ Gmail Connected! You can close this tab."

7. Done. The user can now say "check my email" in the chatbot.
```

From this point on, everything is invisible. The chatbot uses the stored tokens
automatically. When the access token expires (~1 hour), it refreshes silently
using the refresh token — the user never needs to reconnect.

---

## 5. API Endpoints Reference

All endpoints except `/auth/gmail/callback` require a JWT in the header:
`Authorization: Bearer <token>`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/auth/gmail/connect` | Get the Google OAuth URL to open in browser |
| `GET` | `/auth/gmail/callback` | Called by Google after user approves (no JWT needed) |
| `GET` | `/auth/gmail/status` | Check if the current user has Gmail connected |
| `DELETE` | `/auth/gmail/disconnect` | Remove stored tokens (disconnect Gmail) |

### Example: Connect Gmail

```bash
# 1. Get the auth URL
curl -X GET http://localhost:8000/auth/gmail/connect \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Response:
# { "auth_url": "https://accounts.google.com/o/oauth2/auth?..." }

# 2. Open that URL in a browser. After approving, Google calls your callback.

# 3. Check connection status
curl -X GET http://localhost:8000/auth/gmail/status \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Response:
# { "connected": true, "gmail_email": "user@gmail.com", "connected_at": "..." }
```

---

## 6. Where the MCP Server Sits in the Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User's Browser / App                  │
└────────────────────────┬────────────────────────────────┘
                         │  HTTP (JWT auth)
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI  (api.py)                      │
│                                                          │
│  POST /chat  ──► _run_agent(message, user_id)           │
│  GET  /auth/gmail/connect                                │
│  GET  /auth/gmail/callback  ◄── Google OAuth redirect   │
└────────────────────────┬────────────────────────────────┘
                         │  user_id injected into system prompt
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Claude Agent (claude_agent_sdk)             │
│                                                          │
│  Decides which tools to call based on user's message    │
└──────────┬──────────────────────────┬───────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐      ┌──────────────────────────────┐
│  my_tools MCP    │      │      gmail_tools MCP          │
│  (tools.py)      │      │      (gmail_tools.py)         │
│                  │      │                               │
│  read_mock_data  │      │  list_emails                  │
│  draft_email     │      │  get_email                    │
└──────────────────┘      │  search_emails                │
                          │  send_email                   │
                          │  create_draft                 │
                          │  reply_to_email               │
                          │  mark_as_read                 │
                          │  move_to_trash                │
                          │  archive_email                │
                          │  add_label / list_labels      │
                          │  get_gmail_profile            │
                          └──────────────┬────────────────┘
                                         │  Uses user_id to load tokens from DB
                                         ▼
                          ┌──────────────────────────────┐
                          │     SQLite  (educare.db)      │
                          │                               │
                          │  gmail_tokens table:          │
                          │  user_id → access_token       │
                          │           refresh_token       │
                          │           token_expiry        │
                          │           gmail_email         │
                          └──────────────┬────────────────┘
                                         │  Authenticated API call
                                         ▼
                          ┌──────────────────────────────┐
                          │       Gmail API (Google)      │
                          └──────────────────────────────┘
```

**Key insight**: The MCP server (`gmail_tools_server`) lives inside your Python
process — it is NOT a separate running server. It is instantiated in memory when
`api.py` starts and registered with Claude via `mcp_servers={"gmail_tools": ...}`.
No extra ports, no extra processes.

---

## 7. What Users Can Say to the Chatbot

Once connected, users can speak naturally and Claude will call the right tool:

| User says... | Claude calls... |
|---|---|
| "Check my inbox" | `list_emails` |
| "Show my unread emails" | `list_emails(unread_only=true)` |
| "Find emails from alice@company.com" | `search_emails(query="from:alice@company.com")` |
| "Read that last email" | `get_email(email_id=...)` |
| "Summarize my last 5 emails" | `list_emails` + Claude summarizes |
| "Send an email to john@work.com about the meeting" | `send_email` |
| "Reply to Alice's email saying I'll call her tomorrow" | `get_email` then `reply_to_email` |
| "Draft a reply but don't send it yet" | `create_draft` |
| "Mark all unread emails as read" | `list_emails` + `mark_as_read` |
| "Delete that promotional email" | `move_to_trash` |
| "Archive old newsletters" | `search_emails` + `archive_email` |
| "What Gmail account is connected?" | `get_gmail_profile` |
| "Show all my labels" | `list_labels` |

---

## 8. Going to Production

When you deploy to a real domain (e.g. `https://myapp.com`):

1. Go back to **Google Cloud Console → APIs & Services → Credentials**
2. Edit your OAuth 2.0 Client ID
3. Add a new **Authorized redirect URI**:
   ```
   https://myapp.com/auth/gmail/callback
   ```
4. Update `.env` on your server:
   ```env
   GOOGLE_REDIRECT_URI=https://myapp.com/auth/gmail/callback
   ```
5. Submit your app for **Google OAuth verification**:
   - Go to **OAuth consent screen → Publish App**
   - Google will review your scopes (~a few days)
   - Once approved: the scary "unverified app" warning disappears
   - Unlimited users can connect (not just the 100 test users)

---

## 9. Troubleshooting

**"GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env"**
→ You haven't added your credentials to `.env` yet. Follow Section 3.

**"redirect_uri_mismatch" error from Google**
→ The redirect URI in your `.env` doesn't exactly match what you added in Google Cloud
Console. Check for `http` vs `https`, trailing slashes, port numbers.

**"Gmail is not connected for this account"**
→ The user hasn't done the OAuth flow yet. Call `GET /auth/gmail/connect` first.

**"Access Not Configured" or "Gmail API has not been used"**
→ You forgot to enable the Gmail API. Go to Google Cloud Console →
APIs & Services → Library → search Gmail API → Enable.

**User sees "This app isn't verified" warning**
→ Normal during development. Click "Advanced → Go to EduCare Bots (unsafe)" to
proceed. Go through Google verification (Section 8) before launching to real users.

**Tokens stop working after a while**
→ If `refresh_token` is empty in the database, the token can't be refreshed.
Have the user disconnect and reconnect Gmail. This can happen if they previously
granted access and revoked it from their Google Account settings.
