# Google Calendar Integration — EduCare Bots

Complete guide to the Google Calendar AI agent — what it can do, how to set it up in GCP, and example prompts.

---

## What You Need to Do in Google Cloud Console (GCP)

### Step 1: Enable the Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Select your project (or create one)
3. Go to **APIs & Services** → **Library**
4. Search for **Google Calendar API**
5. Click on it and click **Enable**

> The Google Calendar API is free to use. No billing required for basic usage.

### Step 2: Add the Calendar Redirect URI

1. Go to **APIs & Services** → **Credentials**
2. Open your existing **OAuth 2.0 Client ID** (the same one used for Gmail, Sheets, Docs, Drive)
3. Under **Authorized redirect URIs**, click **+ ADD URI**
4. Add:
   ```
   http://localhost:8000/auth/calendar/callback
   ```
5. Click **Save**

> **For production:** Add your production callback URL (e.g. `https://yourdomain.com/auth/calendar/callback`) to the redirect URIs as well.

### Step 3: Verify OAuth Consent Screen (if needed)

If this is your first Google API setup:

1. Go to **APIs & Services** → **OAuth consent screen**
2. Choose **External** (or **Internal** for Workspace)
3. Fill in App name, User support email, Developer contact
4. Under **Scopes**, ensure these are added (they’re requested by the app):
   - `https://www.googleapis.com/auth/calendar` — Full calendar access
   - `https://www.googleapis.com/auth/userinfo.email` — User email

---

## Environment Variables

Your `.env` already has `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — those are reused.

The Calendar redirect URI is set in `.env`:

```env
GOOGLE_CALENDAR_REDIRECT_URI=http://localhost:8000/auth/calendar/callback
```

---

## Connect Your Google Calendar

1. Log in to the app and get a JWT token
2. Call:
   ```
   GET /auth/calendar/connect
   Authorization: Bearer <your_jwt>
   ```
3. The response contains `auth_url` — open it in your browser
4. Sign in with Google and grant Calendar access
5. Google redirects to `/auth/calendar/callback` — you’ll see a success page
6. Close the tab and return to the chatbot

---

## OAuth Endpoints

| Endpoint | Method | Description |
|----------|--------|--------------|
| `/auth/calendar/connect` | GET | Get the Google OAuth URL to grant Calendar access |
| `/auth/calendar/callback` | GET | Google redirects here after approval (no JWT needed) |
| `/auth/calendar/status` | GET | Check if Calendar is connected + which account |
| `/auth/calendar/disconnect` | DELETE | Remove Calendar access |

---

## What You Can Ask the AI

### List Events

- "What's on my calendar today?"
- "Show my upcoming events for this week"
- "List my calendar events for March 2025"

### Create Events

- "Schedule a meeting tomorrow at 2pm for 1 hour titled 'Team standup'"
- "Add an all-day event on March 15 called 'Holiday'"
- "Create an event 'Lunch with John' on Friday at 12:30pm, 1 hour, at Downtown Cafe"

### Update Events

- "Reschedule my 2pm meeting to 3pm"
- "Change the title of my next event to 'Updated meeting'"

### Delete Events

- "Cancel my 2pm meeting tomorrow"
- "Delete the event 'Lunch with John'"

### List Calendars

- "What calendars do I have?"
- "List all my calendars"

---

## Agent Architecture

```
Main Agent
└── calendar_agent (delegated via Task tool)
    ├── list_calendars
    ├── list_events
    ├── get_event
    ├── create_event
    ├── update_event
    └── delete_event
```

You only talk to the main agent. It routes Calendar tasks to `calendar_agent` automatically.

---

## Troubleshooting

### "Google Calendar is not connected"

- Call `GET /auth/calendar/connect` and complete the OAuth flow in your browser
- Ensure you’re logged in with a valid JWT

### "redirect_uri_mismatch"

- Confirm `http://localhost:8000/auth/calendar/callback` is in **Authorized redirect URIs** in GCP
- Ensure `.env` has `GOOGLE_CALENDAR_REDIRECT_URI=http://localhost:8000/auth/calendar/callback`

### "Access blocked: This app's request is invalid"

- Check the OAuth consent screen configuration
- Ensure the Google Calendar API is enabled for your project
