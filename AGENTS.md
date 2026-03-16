# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

EduCare Bots is a FastAPI backend that wraps the Claude Agent SDK to provide an AI chatbot with deep Google Workspace integration. Users authenticate via JWT, connect their Google accounts through per-service OAuth flows, and interact with a multi-agent system that can manage Gmail, Sheets, Docs, Drive, Calendar, Meet, Slides, and Forms on their behalf.

## Running the Server

```
python run.py
```

Starts uvicorn on `0.0.0.0:8000`. On Windows, the entry point sets `WindowsProactorEventLoopPolicy` before uvicorn imports.

- **Auto-reload** (dev): `$env:RELOAD="1"; python run.py` — note this breaks OAuth because PKCE `code_verifier` is stored in memory and lost on restart.
- **Agent trace** (debug): `$env:AGENT_TRACE="1"; python run.py` then in a second terminal `python logs.py` to tail `agent_trace.md`.
- **CLI mode** (no server): `python main_agent.py` — interactive chatbot loop against the same agent config, no user auth required.

## Environment

- Python virtual environment at `.venv/`
- All secrets in `.env` (never committed): `ANTHROPIC_API_KEY`, `JWT_SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, plus per-service redirect URIs.
- Optional `USE_BEDROCK=1` to route through AWS Bedrock instead of the Anthropic API.
- Database: SQLite at `educare.db` (auto-created on startup via `init_db()`).

## Architecture

### Request Flow

1. **`api.py`** — FastAPI app. Handles auth, sessions, file uploads, and chat endpoints (`/chat`, `/chat/stream`).
2. Chat endpoints run the agent in a **separate thread** with a fresh `ProactorEventLoop` (Windows workaround). Streaming uses a `Queue` to push SSE events back.
3. **`agent_config.py`** (`make_agent_options`) — single source of truth for the Claude Agent SDK configuration. Both `api.py` and `main_agent.py` import from here. All MCP servers, sub-agents, tools, system prompt, and permissions are assembled in this one function.
4. The SDK client (`ClaudeSDKClient`) is given the options and executes the agent turn.

### Session Memory

- **Primary**: Claude SDK native session resumption via `claude_session_id` stored in the `sessions` table.
- **Fallback**: If the SDK transcript file is missing (e.g. server restart), the full conversation is rebuilt from DB messages (`_build_context_from_db`).

### Multi-Agent Delegation

The main agent (Sonnet-class) delegates to specialized sub-agents via the `Task` tool. Sub-agents are defined in **`sub_agent.py`** using `AgentDefinition`:

- **gmail_agent** (Haiku) — all Gmail CRUD via `gmail_tools.py` MCP server
- **sheets_agent** (Sonnet, orchestrator) — delegates to three leaf agents:
  - `sheets_data_agent` (Haiku) — CRUD, worksheets, sort, find-replace
  - `sheets_format_agent` (Haiku) — colors, fonts, borders, merge, freeze, resize
  - `sheets_visual_agent` (Haiku) — charts, conditional formatting, sparklines, dropdowns
- **docs_agent** (Haiku) — Google Docs via `docs_tools.py`
- **drive_agent** (Haiku) — Google Drive via `drive_tools.py`
- **calendar_agent** (Haiku) — Google Calendar via `calendar_tools.py`
- **meet_agent** (Haiku) — Google Meet via `meet_tools.py`
- **slides_agent** (Haiku, orchestrator) → `slides_data_agent` + `slides_format_agent`
- **forms_agent** (Haiku) — Google Forms via `forms_tools.py`

### Reminders

Users can say "remind me tomorrow at 9am about X" and the agent uses the `create_reminder` tool directly (no delegation). Reminders are stored in the `reminders` table. A background worker (APScheduler, runs every minute) checks for due reminders and pushes them via the existing `/chat/notifications` SSE stream. Frontend connects to that stream and listens for `event: reminder` to show in-app notifications.

- **`reminders_tools.py`** — `create_reminder`, `list_reminders` MCP tools
- **Endpoints**: `GET /reminders`, `GET /reminders/pending`, `DELETE /reminders/{id}`

### MCP Tool Servers

Each `*_tools.py` file exposes tools via `create_sdk_mcp_server`. Tool names follow the pattern `mcp__<server_name>__<tool_name>`. Every tool takes `user_id` as its first parameter to look up per-user OAuth tokens from the database.

### Google OAuth Pattern

Every Google service follows the same pattern (Gmail shown as example):
1. `get_<service>_auth_url(user_id)` — creates a `Flow`, encodes `user_id` in `state`, stores the Flow in `_pending_flows` dict.
2. `exchange_<service>_code(code, state)` — pops the Flow, exchanges the code, saves tokens to `<service>_tokens` table via `database.py`.
3. `_get_service(user_id)` — builds an authenticated API client, auto-refreshes expired tokens.

The in-memory `_pending_flows` dict means OAuth breaks if the server restarts between `/connect` and `/callback`.

### Database Schema (database.py)

SQLite with tables: `users`, `sessions` (has `claude_session_id`), `messages` (has `image_url`, `document_url`), `reminders` (user_id, remind_at, message, delivered), and per-service token tables (`gmail_tokens`, etc.). Schema migrations are handled inline in `init_db()` via `ALTER TABLE` wrapped in try/except.

## Key Conventions

- **All tool modules** load `.env` manually with the same inline parser (no `python-dotenv` dependency).
- **`user_id` must be passed** to every sub-agent delegation prompt and every MCP tool call.
- **Sheets range notation**: data tools use `Sheet1!A1:D10` (with sheet name prefix); formatting tools use `A1:D10` (no prefix). Chart `data_range` uses the prefix.
- **Agent working directory** is `cwd/` (created at runtime). Uploaded files go to `cwd/uploads/`.
- **Document parsing** (`document_parser.py`): extracts text from PDF, DOCX, PPTX, TXT. For image-based PDFs, falls back to Claude Vision OCR.
- Auth uses `bcrypt` + `python-jose` JWTs. Token expiry default is 60 min (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`).
