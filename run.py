"""Entry point for running the FastAPI server on Windows with ProactorEventLoop."""

import asyncio
import os
import sys

# Must be set before uvicorn imports anything async-related
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

# OAuth (Gmail, Sheets, Docs, Drive) stores PKCE code_verifier in memory. With reload=True,
# the server restarts on file changes and loses it, causing "Invalid code verifier" on callback.
# Default: reload=False so OAuth works. Set RELOAD=1 to enable auto-reload for development.
_use_reload = os.environ.get("RELOAD", "").strip() == "1"

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=_use_reload,
        loop="asyncio",   # tells uvicorn to use the current asyncio policy (Proactor on Windows)
    )
