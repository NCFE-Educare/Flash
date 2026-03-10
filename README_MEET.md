# Google Meet Integration — EduCare Bots

Complete guide to the Google Meet AI agent — what it can do, how to set it up in GCP, and example prompts.

---

## What You Need to Do in Google Cloud Console (GCP)

### Step 1: Enable the Google Meet API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Select your project (or create one)
3. Go to **APIs & Services** → **Library**
4. Search for **Google Meet API**
5. Click on it and click **Enable**

> The Google Meet API requires a Google Workspace account with Meet enabled.

### Step 2: Add the Meet Redirect URI

1. Go to **APIs & Services** → **Credentials**
2. Open your existing **OAuth 2.0 Client ID** (the same one used for Gmail, Sheets, Calendar, etc.)
3. Under **Authorized redirect URIs**, click **+ ADD URI**
4. Add:
   ```
   http://localhost:8000/auth/meet/callback
   ```
5. Click **Save**

> **For production:** Add your production callback URL (e.g. `https://yourdomain.com/auth/meet/callback`) to the redirect URIs as well.

### Step 3: Verify OAuth Consent Screen (if needed)

1. Go to **APIs & Services** → **OAuth consent screen**
2. Under **Scopes**, ensure these are added (they're requested by the app):
   - `https://www.googleapis.com/auth/meetings.space.created` — Create and manage meeting spaces
   - `https://www.googleapis.com/auth/userinfo.email` — User email

> **Note:** The `meetings.space.created` scope is a **Sensitive** scope and may require additional app verification for external users.

---

## Environment Variables

Your `.env` already has `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — those are reused.

The Meet redirect URI is set in `.env`:

```env
GOOGLE_MEET_REDIRECT_URI=http://localhost:8000/auth/meet/callback
```

---

## Connect Your Google Meet

1. Log in to the app and get a JWT token
2. Call:
   ```
   GET /auth/meet/connect
   Authorization: Bearer <your_jwt>
   ```
3. The response contains `auth_url` — open it in your browser
4. Sign in with Google and grant Meet access
5. Google redirects to `/auth/meet/callback` — you'll see a success page
6. Close the tab and return to the chatbot

---

## OAuth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/meet/connect` | GET | Get the Google OAuth URL to grant Meet access |
| `/auth/meet/callback` | GET | Google redirects here after approval (no JWT needed) |
| `/auth/meet/status` | GET | Check if Meet is connected + which account |
| `/auth/meet/disconnect` | DELETE | Remove Meet access |

---

## Example Prompts

Once connected, you can ask the chatbot:

- "Create a Google Meet link for me"
- "Start a video meeting"
- "Schedule a Meet and give me the link"
- "Get me a new meeting room URL"

---

## Agent Architecture

```
main_agent
└── meet_agent (delegated via Task tool)
    ├── create_meet_space
    ├── get_meet_space
    └── get_meet_profile
```

You only talk to the main agent. It routes Meet tasks to `meet_agent` automatically.

---

## Troubleshooting

### "Google Meet is not connected for this account"

- Call `GET /auth/meet/connect` and complete the OAuth flow in your browser
- Ensure you're using a Google Workspace account (not a regular Gmail account) — Meet API may have restrictions

### "Could not connect your Google Meet account"

- Confirm `http://localhost:8000/auth/meet/callback` is in **Authorized redirect URIs** in GCP
- Ensure `.env` has `GOOGLE_MEET_REDIRECT_URI=http://localhost:8000/auth/meet/callback`
- Ensure the Google Meet API is enabled for your project
