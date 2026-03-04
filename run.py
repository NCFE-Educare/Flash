"""Entry point for running the FastAPI server on Windows with ProactorEventLoop."""

import asyncio
import sys

# Must be set before uvicorn imports anything async-related
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        loop="asyncio",   # tells uvicorn to use the current asyncio policy (Proactor on Windows)
    )
