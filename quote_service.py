import os
from pathlib import Path
from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Load .env (same pattern as the rest of the project)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

# Initialize the async client
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

async def generate_quote(topic: str = None, mood: str = None) -> str:
    """
    Generate a two-liner quote using OpenAI's gpt-4o-mini.
    """
    if not os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") == "your_openai_api_key_here":
        return "OpenAI API key not configured. Please add it to your .env file."

    # Construct the prompt
    if topic and mood:
        prompt = f"Write a simple, encouraging two-liner quote for school students about {topic} that feels {mood}."
    elif topic:
        prompt = f"Write a simple, encouraging two-liner quote for school students about {topic}."
    elif mood:
        prompt = f"Write a simple, encouraging two-liner quote for school students that feels {mood}."
    else:
        prompt = "Write a random simple, encouraging two-liner quote for school students."

    prompt += "\n\nRules:\n1. Use simple words that a student can easily understand.\n2. Exactly two lines.\n3. No emojis.\n4. No attribution (don't say who said it)."

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a friendly mentor who writes simple, encouraging, and easy-to-understand two-liner quotes for school students."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.8
        )
        quote = response.choices[0].message.content.strip()
        return quote
    except Exception as e:
        return f"Error generating quote: {str(e)}"
