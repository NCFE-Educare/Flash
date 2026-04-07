"""Email notification system for Kanban board events using SendGrid."""

import os
from typing import Optional

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Content
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False
    print("[Email] SendGrid not installed. Run: pip install sendgrid")

from pathlib import Path

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


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """
    Send an email using SendGrid API.

    Configure these environment variables:
    - SENDGRID_API_KEY: Your SendGrid API key
    - EMAIL_FROM: Your verified sender email (e.g., noreply@yourdomain.com)
    - EMAIL_FROM_NAME: Display name (default: EduCare Bots)
    """
    if not SENDGRID_AVAILABLE:
        print("[Email] ❌ SendGrid library not installed")
        return False

    try:
        # Get email config from environment
        api_key = os.getenv("SENDGRID_API_KEY")
        from_email = os.getenv("EMAIL_FROM")
        from_name = os.getenv("EMAIL_FROM_NAME", "EduCare Bots")

        if not api_key:
            print("[Email] ❌ SENDGRID_API_KEY not configured in .env")
            return False

        if not from_email:
            print("[Email] ❌ EMAIL_FROM not configured in .env")
            return False

        # Create email message
        message = Mail(
            from_email=Email(from_email, from_name),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html_body)
        )

        # Send via SendGrid API
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        if response.status_code in [200, 202]:
            print(f"[Email] ✅ Sent to {to_email}: {subject}")
            return True
        else:
            print(f"[Email] ⚠️ Unexpected status {response.status_code} for {to_email}")
            return False

    except Exception as e:
        print(f"[Email] ❌ Failed to send to {to_email}: {e}")
        return False


def send_workspace_invitation_email(
    to_email: str,
    workspace_name: str,
    inviter_name: str,
    invitation_link: str,
    user_exists: bool = False,
) -> bool:
    """Send invitation email when user is invited to workspace."""

    if user_exists:
        # User already has account
        subject = f"{inviter_name} invited you to '{workspace_name}'"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0;">🎯 You've Been Invited!</h1>
            </div>

            <div style="background: #f7fafc; padding: 30px; border-radius: 0 0 10px 10px;">
                <p style="font-size: 16px; color: #2d3748;">
                    <strong>{inviter_name}</strong> has invited you to collaborate on:
                </p>

                <div style="background: white; padding: 20px; border-left: 4px solid #667eea; margin: 20px 0;">
                    <h2 style="margin: 0; color: #2d3748;">{workspace_name}</h2>
                </div>

                <p style="font-size: 14px; color: #4a5568;">
                    You've been added to this workspace and can start collaborating right away!
                </p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{invitation_link}"
                       style="background: #667eea; color: white; padding: 12px 30px; text-decoration: none;
                              border-radius: 6px; font-weight: bold; display: inline-block;">
                        View Workspace →
                    </a>
                </div>

                <p style="font-size: 12px; color: #718096; text-align: center; margin-top: 30px;">
                    Powered by EduCare Bots
                </p>
            </div>
        </body>
        </html>
        """
    else:
        # User needs to sign up first
        subject = f"{inviter_name} invited you to join '{workspace_name}'"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0;">🎯 Join Our Team!</h1>
            </div>

            <div style="background: #f7fafc; padding: 30px; border-radius: 0 0 10px 10px;">
                <p style="font-size: 16px; color: #2d3748;">
                    <strong>{inviter_name}</strong> has invited you to collaborate on:
                </p>

                <div style="background: white; padding: 20px; border-left: 4px solid #667eea; margin: 20px 0;">
                    <h2 style="margin: 0; color: #2d3748;">{workspace_name}</h2>
                </div>

                <p style="font-size: 14px; color: #4a5568;">
                    To accept this invitation, create your free account and you'll be automatically added to the workspace.
                </p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{invitation_link}"
                       style="background: #667eea; color: white; padding: 12px 30px; text-decoration: none;
                              border-radius: 6px; font-weight: bold; display: inline-block;">
                        Accept Invitation & Sign Up →
                    </a>
                </div>

                <p style="font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; padding-top: 20px; margin-top: 30px;">
                    Or copy this link:<br>
                    <a href="{invitation_link}" style="color: #667eea;">{invitation_link}</a>
                </p>

                <p style="font-size: 12px; color: #718096; text-align: center; margin-top: 30px;">
                    Powered by EduCare Bots
                </p>
            </div>
        </body>
        </html>
        """

    return send_email(to_email, subject, html_body)


def send_task_assigned_email(
    to_email: str,
    task_title: str,
    task_description: Optional[str],
    assigner_name: str,
    workspace_name: str,
    task_link: str,
    priority: str = "medium",
    due_date: Optional[str] = None,
) -> bool:
    """Send email when a task is assigned to someone."""

    priority_colors = {
        "low": "#10B981",
        "medium": "#F59E0B",
        "high": "#EF4444",
        "urgent": "#DC2626",
    }
    priority_color = priority_colors.get(priority, "#6B7280")

    due_date_html = ""
    if due_date:
        due_date_html = f"""
        <p style="font-size: 14px; color: #EF4444; margin: 10px 0;">
            📅 <strong>Due:</strong> {due_date}
        </p>
        """

    subject = f"New Task Assigned: {task_title}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
            <h1 style="color: white; margin: 0;">📋 New Task Assigned!</h1>
        </div>

        <div style="background: #f7fafc; padding: 30px; border-radius: 0 0 10px 10px;">
            <p style="font-size: 16px; color: #2d3748;">
                <strong>{assigner_name}</strong> assigned you a task in <strong>{workspace_name}</strong>:
            </p>

            <div style="background: white; padding: 20px; border-left: 4px solid {priority_color}; margin: 20px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <h2 style="margin: 0; color: #2d3748;">{task_title}</h2>
                    <span style="background: {priority_color}; color: white; padding: 4px 12px; border-radius: 12px;
                                 font-size: 12px; font-weight: bold; text-transform: uppercase;">
                        {priority}
                    </span>
                </div>

                {f'<p style="font-size: 14px; color: #4a5568; margin: 10px 0;">{task_description}</p>' if task_description else ''}

                {due_date_html}
            </div>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{task_link}"
                   style="background: #3B82F6; color: white; padding: 12px 30px; text-decoration: none;
                          border-radius: 6px; font-weight: bold; display: inline-block;">
                    View Task →
                </a>
            </div>

            <p style="font-size: 12px; color: #718096; text-align: center; margin-top: 30px;">
                Powered by EduCare Bots
            </p>
        </div>
    </body>
    </html>
    """

    return send_email(to_email, subject, html_body)


def send_task_comment_notification(
    to_email: str,
    task_title: str,
    commenter_name: str,
    comment_text: str,
    workspace_name: str,
    task_link: str,
) -> bool:
    """Send email when someone comments on a task you're involved with."""

    subject = f"New comment on: {task_title}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #10B981 0%, #059669 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
            <h1 style="color: white; margin: 0;">💬 New Comment</h1>
        </div>

        <div style="background: #f7fafc; padding: 30px; border-radius: 0 0 10px 10px;">
            <p style="font-size: 16px; color: #2d3748;">
                <strong>{commenter_name}</strong> commented on <strong>{task_title}</strong> in {workspace_name}:
            </p>

            <div style="background: white; padding: 20px; border-left: 4px solid #10B981; margin: 20px 0;">
                <p style="font-size: 14px; color: #4a5568; margin: 0; font-style: italic;">
                    "{comment_text}"
                </p>
            </div>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{task_link}"
                   style="background: #10B981; color: white; padding: 12px 30px; text-decoration: none;
                          border-radius: 6px; font-weight: bold; display: inline-block;">
                    View Task & Reply →
                </a>
            </div>

            <p style="font-size: 12px; color: #718096; text-align: center; margin-top: 30px;">
                Powered by EduCare Bots
            </p>
        </div>
    </body>
    </html>
    """

    return send_email(to_email, subject, html_body)


def send_task_due_reminder(
    to_email: str,
    task_title: str,
    workspace_name: str,
    task_link: str,
    due_date: str,
    hours_until_due: int,
) -> bool:
    """Send reminder email when task is due soon."""

    urgency_text = "is overdue!" if hours_until_due < 0 else f"is due in {hours_until_due} hours!"
    urgency_color = "#DC2626" if hours_until_due < 0 else "#F59E0B"

    subject = f"⏰ Task Due Soon: {task_title}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: {urgency_color}; padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
            <h1 style="color: white; margin: 0;">⏰ Task Reminder</h1>
        </div>

        <div style="background: #f7fafc; padding: 30px; border-radius: 0 0 10px 10px;">
            <p style="font-size: 18px; color: #2d3748; font-weight: bold;">
                Your task <strong>{urgency_text}</strong>
            </p>

            <div style="background: white; padding: 20px; border-left: 4px solid {urgency_color}; margin: 20px 0;">
                <h2 style="margin: 0 0 10px 0; color: #2d3748;">{task_title}</h2>
                <p style="font-size: 14px; color: #4a5568; margin: 5px 0;">
                    📁 Workspace: {workspace_name}
                </p>
                <p style="font-size: 14px; color: {urgency_color}; margin: 5px 0; font-weight: bold;">
                    📅 Due: {due_date}
                </p>
            </div>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{task_link}"
                   style="background: {urgency_color}; color: white; padding: 12px 30px; text-decoration: none;
                          border-radius: 6px; font-weight: bold; display: inline-block;">
                    View Task →
                </a>
            </div>

            <p style="font-size: 12px; color: #718096; text-align: center; margin-top: 30px;">
                Powered by EduCare Bots
            </p>
        </div>
    </body>
    </html>
    """

    return send_email(to_email, subject, html_body)
