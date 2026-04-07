import os
from pathlib import Path
from email_notifications import send_workspace_invitation_email, send_task_assigned_email

# ---------------------------------------------------------------------------
# Load .env (same pattern as the rest of the project)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

TEST_EMAIL = os.environ.get("EMAIL_FROM", "your-test-email@gmail.com")

print("=" * 60)
print("  SendGrid Email Test")
print("=" * 60)
print()

# Test 1: Workspace Invitation Email
print("Test 1: Sending Workspace Invitation Email...")
result1 = send_workspace_invitation_email(
    to_email=TEST_EMAIL,
    workspace_name="Math Department - Q2 2024",
    inviter_name="Principal John",
    invitation_link="http://localhost:3000/workspaces/1",
    user_exists=True
)
print(f"Result: {'✅ Success' if result1 else '❌ Failed'}\n")

# Test 2: Task Assignment Email
print("Test 2: Sending Task Assignment Email...")
result2 = send_task_assigned_email(
    to_email=TEST_EMAIL,
    task_title="Review Q1 Algebra Curriculum",
    task_description="Analyze student performance and identify knowledge gaps",
    assigner_name="Principal John",
    workspace_name="Math Department - Q2 2024",
    task_link="http://localhost:3000/tasks/5",
    priority="high",
    due_date="2024-05-15T17:00:00Z"
)
print(f"Result: {'✅ Success' if result2 else '❌ Failed'}\n")

print("=" * 60)
print("  Test Complete!")
print("=" * 60)
print()
print("Check your email inbox for the test emails.")
print("If failed, check:")
print("  1. SENDGRID_API_KEY is set in .env")
print("  2. EMAIL_FROM is set in .env")
print("  3. EMAIL_FROM is a verified sender in SendGrid")
print("  4. SendGrid is installed: pip install sendgrid")
