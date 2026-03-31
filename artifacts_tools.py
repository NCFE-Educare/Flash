"""
Artifacts MCP tools — create, update, and execute artifacts.
Artifacts are structured content (code, HTML, docs) rendered in a dedicated UI pane.
"""

import json
import os
import subprocess
import sys
from typing import Any
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import create_artifact, update_artifact, get_artifact_by_identifier, get_artifacts_for_session

@tool(
    "create_artifact",
    (
        "Create a new artifact for the user. "
        "session_id: required. user_id: required. "
        "identifier: required (unique string ID, e.g. 'weather-dashboard'). "
        "title: required (human readable title). "
        "type: required (e.g. 'text/html', 'application/vnd.ant.code', 'text/markdown', 'image/svg+xml'). "
        "content: required (the actual code or text). "
        "language: optional (e.g. 'python', 'javascript' for code types)."
    ),
    {
        "session_id": int,
        "user_id": int,
        "identifier": str,
        "title": str,
        "type": str,
        "content": str,
        "language": str,
    },
)
async def create_artifact_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new artifact."""
    session_id = int(args["session_id"])
    user_id = int(args["user_id"])
    identifier = str(args["identifier"]).strip()
    title = str(args["title"]).strip()
    artifact_type = str(args["type"]).strip()
    content = str(args["content"])
    language = args.get("language")

    try:
        # Check if already exists
        existing = get_artifact_by_identifier(session_id, identifier)
        if existing:
            return {"content": [{"type": "text", "text": f"Error: Artifact with identifier '{identifier}' already exists in this session. Use update_artifact instead."}]}

        artifact = create_artifact(session_id, user_id, identifier, title, artifact_type, content, language)
        return {"content": [{"type": "text", "text": json.dumps(artifact, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating artifact: {e}"}]}


@tool(
    "update_artifact",
    (
        "Update an existing artifact. "
        "session_id: required. identifier: required. "
        "content: required (new content). title: optional."
    ),
    {
        "session_id": int,
        "identifier": str,
        "content": str,
        "title": str,
    },
)
async def update_artifact_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Update an artifact."""
    session_id = int(args["session_id"])
    identifier = str(args["identifier"]).strip()
    content = str(args["content"])
    title = args.get("title")

    try:
        artifact = update_artifact(session_id, identifier, content, title)
        if not artifact:
            return {"content": [{"type": "text", "text": f"Error: Artifact '{identifier}' not found in session {session_id}."}]}
        return {"content": [{"type": "text", "text": json.dumps(artifact, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating artifact: {e}"}]}


@tool(
    "execute_python_artifact",
    (
        "Execute a Python artifact and return its output. "
        "session_id: required. identifier: required. "
        "The artifact must be a Python script (language='python' or type='application/vnd.ant.code')."
    ),
    {
        "session_id": int,
        "identifier": str,
    },
)
async def execute_python_artifact_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a Python artifact."""
    session_id = int(args["session_id"])
    identifier = str(args["identifier"]).strip()

    try:
        artifact = get_artifact_by_identifier(session_id, identifier)
        if not artifact:
            return {"content": [{"type": "text", "text": f"Error: Artifact '{identifier}' not found."}]}
        
        # Check if it's Python
        is_python = artifact.get("language") == "python" or "code" in artifact.get("type", "").lower()
        if not is_python:
            return {"content": [{"type": "text", "text": f"Error: Artifact '{identifier}' is not a Python script (type={artifact.get('type')}, language={artifact.get('language')})."}]}

        # Save to temp file
        temp_dir = Path("artifacts_cache")
        temp_dir.mkdir(exist_ok=True)
        temp_file = temp_dir / f"{identifier}_{session_id}.py"
        temp_file.write_text(artifact["content"], encoding="utf-8")

        # Execute
        result = subprocess.run(
            [sys.executable, str(temp_file)],
            capture_output=True,
            text=True,
            timeout=30
        )

        output = f"--- STDOUT ---\n{result.stdout}\n\n--- STDERR ---\n{result.stderr}"
        if result.returncode != 0:
            output = f"Execution failed (exit code {result.returncode})\n\n" + output

        return {"content": [{"type": "text", "text": output}]}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "Error: Execution timed out (30s max)."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error executing artifact: {e}"}]}


artifacts_tools_server = create_sdk_mcp_server(
    name="artifacts",
    version="1.0.0",
    tools=[create_artifact_tool, update_artifact_tool, execute_python_artifact_tool],
)
