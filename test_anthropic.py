import os
import anthropic
from pathlib import Path

# Load .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

try:
    message = client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": "Hello, are you using the Anthropic API directly?",
            }
        ],
    )
    print(f"Response: {message.content[0].text}")
except Exception as e:
    print(f"Error: {e}")
