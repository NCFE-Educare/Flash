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

async def generate_lesson_plan(
    grade: str, 
    topic: str, 
    criteria: str, 
    additional_context: str = None
) -> str:
    """
    Generate a high-quality lesson plan using OpenAI's gpt-4o-mini.
    """
    if not os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") == "your_openai_api_key_here":
        return "OpenAI API key not configured. Please add it to your .env file."

    # Construct the base prompt
    prompt = (
        f"You are a master teacher and friendly mentor. Create a high-quality, engaging lesson plan for school students.\n\n"
        f"GRADE LEVEL: {grade}\n"
        f"TOPIC: {topic}\n"
        f"SPECIFIC CRITERIA / GOALS: {criteria}\n"
    )

    if additional_context:
        prompt += f"\nADDITIONAL REFERENCE INFO:\n{additional_context}\n"

    prompt += (
        "\nSTRUCTURE THE LESSON PLAN AS FOLLOWS (using Markdown):\n"
        "1. **Lesson Overview**: A simple summary for students.\n"
        "2. **Learning Objectives**: What will the students learn? (Simple language)\n"
        "3. **Required Materials**: What do we need?\n"
        "4. **Step-by-Step Activities**: Broken down into time segments (e.g. 0-10 min: Intro).\n"
        "5. **Fun Quiz / Assessment**: A simple way to check if they learned it.\n"
        "6. **Teacher Tips**: Advice for making this lesson great.\n\n"
        "TONE: Friendly, encouraging, and easy to understand. Avoid overly academic jargon."
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a master educator who creates simplified, inspiring lesson plans for school teachers and students."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        lesson_plan = response.choices[0].message.content.strip()
        return lesson_plan
    except Exception as e:
        return f"Error generating lesson plan: {str(e)}"
