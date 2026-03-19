"""Mem0 Platform API wrapper for long-term user memory across sessions."""

from pathlib import Path

_env: dict[str, str] = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _env[k.strip()] = v.strip()

_API_KEY = _env.get("MEM0_API_KEY", "")
_ENABLED = bool(_API_KEY)

_client = None


def _get_client():
    global _client
    if _client is None:
        from mem0 import MemoryClient
        _client = MemoryClient(api_key=_API_KEY)
    return _client


def add_memory(user_id: int, messages: list[dict], metadata: dict | None = None):
    """Feed a conversation exchange to Mem0 for automatic fact extraction."""
    if not _ENABLED:
        return None
    try:
        kwargs: dict = {"user_id": str(user_id), "version": "v2"}
        if metadata:
            kwargs["metadata"] = metadata
        return _get_client().add(messages, **kwargs)
    except Exception as e:
        print(f"[Mem0] add_memory error: {e}")
        return None


def search_memory(user_id: int, query: str, limit: int = 10) -> list[dict]:
    """Semantic search over a user's stored memories."""
    if not _ENABLED:
        return []
    try:
        result = _get_client().search(
            query, version="v2",
            filters={"user_id": str(user_id)},
            top_k=limit,
        )
        if isinstance(result, dict):
            return result.get("results", result.get("memories", []))
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[Mem0] search_memory error: {e}")
        return []


def get_all_memories(user_id: int) -> list[dict]:
    """Retrieve every stored memory for a user."""
    if not _ENABLED:
        return []
    try:
        result = _get_client().get_all(
            version="v2",
            filters={"user_id": str(user_id)},
        )
        if isinstance(result, dict):
            return result.get("results", result.get("memories", []))
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[Mem0] get_all_memories error: {e}")
        return []


def delete_memory(memory_id: str) -> bool:
    """Delete a specific memory by its ID."""
    if not _ENABLED:
        return False
    try:
        _get_client().delete(memory_id=memory_id)
        return True
    except Exception as e:
        print(f"[Mem0] delete_memory error: {e}")
        return False


def update_memory(memory_id: str, text: str) -> bool:
    """Update the text content of a specific memory."""
    if not _ENABLED:
        return False
    try:
        _get_client().update(memory_id=memory_id, text=text)
        return True
    except Exception as e:
        print(f"[Mem0] update_memory error: {e}")
        return False
