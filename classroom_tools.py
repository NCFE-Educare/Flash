"""Google Classroom MCP tools — per-user OAuth 2.0 with tokens stored in SQLite.

OAuth helpers (get_classroom_auth_url / exchange_classroom_code) are plain functions
called directly by api.py — they are NOT MCP tools.
"""

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from claude_agent_sdk import create_sdk_mcp_server, tool
from database import delete_classroom_tokens, get_classroom_tokens, save_classroom_tokens

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_CLASSROOM_REDIRECT_URI = os.environ.get(
    "GOOGLE_CLASSROOM_REDIRECT_URI", "http://localhost:8000/auth/classroom/callback"
)

CLASSROOM_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.rosters",
    "https://www.googleapis.com/auth/classroom.announcements",
    "https://www.googleapis.com/auth/classroom.topics",
    "https://www.googleapis.com/auth/classroom.profile.emails",
]

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_pending_flows: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# OAuth helpers (called by api.py, NOT MCP tools)
# ---------------------------------------------------------------------------

def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_CLASSROOM_REDIRECT_URI],
        }
    }


def get_classroom_auth_url(user_id: int) -> str:
    """Generate the Google OAuth consent-screen URL for Classroom access."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=CLASSROOM_SCOPES,
        redirect_uri=GOOGLE_CLASSROOM_REDIRECT_URI,
    )

    state = base64.urlsafe_b64encode(
        json.dumps({"user_id": user_id, "service": "classroom"}).encode()
    ).decode().rstrip("=")

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    _pending_flows[state] = flow
    return auth_url


def exchange_classroom_code(code: str, state: str) -> dict | None:
    """Exchange OAuth code for tokens, save to DB. Returns {email, user_id} or None."""
    try:
        padding = 4 - len(state) % 4
        padded = state + ("=" * padding if padding != 4 else "")
        state_data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        user_id = int(state_data["user_id"])
    except Exception:
        return None

    try:
        flow = _pending_flows.pop(state, None)
        if flow is None:
            flow = Flow.from_client_config(
                _client_config(),
                scopes=CLASSROOM_SCOPES,
                redirect_uri=GOOGLE_CLASSROOM_REDIRECT_URI,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        classroom_svc = build("classroom", "v1", credentials=creds)
        profile = classroom_svc.userProfiles().get(userId="me").execute()
        google_email = profile.get("emailAddress", "")

        expiry_str = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else datetime.utcnow().isoformat()
        )

        save_classroom_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or "",
            token_expiry=expiry_str,
            google_email=google_email,
        )
        return {"email": google_email, "user_id": user_id}
    except Exception as e:
        import traceback
        print(f"\n[Classroom OAuth ERROR] exchange_classroom_code failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credentials(user_id: int) -> Credentials:
    """Return valid (auto-refreshed) Google credentials for the user."""
    token_data = get_classroom_tokens(user_id)
    if not token_data:
        raise ValueError(
            "Google Classroom is not connected for this account. "
            "Please call GET /auth/classroom/connect to get the authorization URL, "
            "then open it in your browser to grant access."
        )

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=CLASSROOM_SCOPES,
    )

    try:
        expiry = datetime.fromisoformat(token_data["token_expiry"])
        if expiry.tzinfo is not None:
            expiry = expiry.replace(tzinfo=None)
        creds.expiry = expiry
    except Exception:
        pass

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed_expiry = (
            creds.expiry.replace(tzinfo=None).isoformat()
            if creds.expiry
            else token_data["token_expiry"]
        )
        save_classroom_tokens(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token or token_data["refresh_token"],
            token_expiry=refreshed_expiry,
            google_email=token_data["google_email"],
        )

    return creds


def _classroom(user_id: int):
    """Build an authenticated Classroom v1 service."""
    return build("classroom", "v1", credentials=_get_credentials(user_id))


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@tool(
    "list_courses",
    (
        "List courses the user teaches or is enrolled in. "
        "user_id: required. role: optional ('TEACHER' or 'STUDENT', default lists all). "
        "page_size: optional int (default 20). course_states: optional comma-separated "
        "('ACTIVE', 'ARCHIVED', 'PROVISIONED', 'DECLINED', 'SUSPENDED')."
    ),
    {"user_id": int, "role": str, "page_size": int, "course_states": str},
)
async def list_courses(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        page_size = int(args.get("page_size", 20))

        svc = _classroom(user_id)
        kwargs: dict[str, Any] = {"pageSize": page_size}

        role = args.get("role", "")
        if role:
            role_upper = role.strip().upper()
            if role_upper == "TEACHER":
                kwargs["teacherId"] = "me"
            elif role_upper == "STUDENT":
                kwargs["studentId"] = "me"

        states = args.get("course_states", "")
        if states:
            kwargs["courseStates"] = [s.strip().upper() for s in states.split(",") if s.strip()]

        result = svc.courses().list(**kwargs).execute()
        courses = result.get("courses", [])

        if not courses:
            return {"content": [{"type": "text", "text": "No courses found."}]}

        course_list = [
            {
                "course_id": c["id"],
                "name": c.get("name", ""),
                "section": c.get("section", ""),
                "description": c.get("descriptionHeading", ""),
                "state": c.get("courseState", ""),
                "enrollment_code": c.get("enrollmentCode", ""),
                "link": c.get("alternateLink", ""),
            }
            for c in courses
        ]
        return {"content": [{"type": "text", "text": json.dumps(course_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing courses: {e}"}]}


@tool(
    "get_course",
    "Get full details for a specific course. user_id: required. course_id: required.",
    {"user_id": int, "course_id": str},
)
async def get_course(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])

        svc = _classroom(user_id)
        course = svc.courses().get(id=course_id).execute()
        return {"content": [{"type": "text", "text": json.dumps(course, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting course: {e}"}]}


@tool(
    "create_course",
    (
        "Create a new course. user_id: required. name: required. "
        "section: optional. description: optional. room: optional."
    ),
    {"user_id": int, "name": str, "section": str, "description": str, "room": str},
)
async def create_course(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        body: dict[str, Any] = {
            "name": str(args["name"]),
            "ownerId": "me",
        }
        if args.get("section"):
            body["section"] = str(args["section"])
        if args.get("description"):
            body["descriptionHeading"] = str(args["description"])
        if args.get("room"):
            body["room"] = str(args["room"])

        svc = _classroom(user_id)
        course = svc.courses().create(body=body).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "course_id": course["id"],
                    "name": course.get("name", ""),
                    "enrollment_code": course.get("enrollmentCode", ""),
                    "link": course.get("alternateLink", ""),
                    "state": course.get("courseState", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating course: {e}"}]}


@tool(
    "update_course",
    (
        "Update an existing course. user_id: required. course_id: required. "
        "name: optional. section: optional. description: optional. room: optional. "
        "course_state: optional ('ACTIVE', 'ARCHIVED', 'PROVISIONED')."
    ),
    {"user_id": int, "course_id": str, "name": str, "section": str, "description": str, "room": str, "course_state": str},
)
async def update_course(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])

        svc = _classroom(user_id)
        course = svc.courses().get(id=course_id).execute()

        update_mask = []
        if args.get("name"):
            course["name"] = str(args["name"])
            update_mask.append("name")
        if args.get("section"):
            course["section"] = str(args["section"])
            update_mask.append("section")
        if args.get("description"):
            course["descriptionHeading"] = str(args["description"])
            update_mask.append("descriptionHeading")
        if args.get("room"):
            course["room"] = str(args["room"])
            update_mask.append("room")
        if args.get("course_state"):
            course["courseState"] = str(args["course_state"]).upper()
            update_mask.append("courseState")

        if not update_mask:
            return {"content": [{"type": "text", "text": "No fields to update."}]}

        updated = svc.courses().patch(
            id=course_id,
            body=course,
            updateMask=",".join(update_mask),
        ).execute()
        return {"content": [{"type": "text", "text": json.dumps({
            "course_id": updated["id"],
            "name": updated.get("name", ""),
            "state": updated.get("courseState", ""),
            "updated_fields": update_mask,
        }, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating course: {e}"}]}


@tool(
    "list_coursework",
    (
        "List assignments/coursework for a course. user_id: required. course_id: required. "
        "page_size: optional int (default 20). order_by: optional ('dueDate asc', 'updateTime desc')."
    ),
    {"user_id": int, "course_id": str, "page_size": int, "order_by": str},
)
async def list_coursework(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        page_size = int(args.get("page_size", 20))

        svc = _classroom(user_id)
        kwargs: dict[str, Any] = {"courseId": course_id, "pageSize": page_size}

        order_by = args.get("order_by", "")
        if order_by:
            kwargs["orderBy"] = order_by

        result = svc.courses().courseWork().list(**kwargs).execute()
        items = result.get("courseWork", [])

        if not items:
            return {"content": [{"type": "text", "text": "No coursework found for this course."}]}

        cw_list = [
            {
                "coursework_id": item["id"],
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "state": item.get("state", ""),
                "work_type": item.get("workType", ""),
                "max_points": item.get("maxPoints"),
                "due_date": item.get("dueDate"),
                "due_time": item.get("dueTime"),
                "link": item.get("alternateLink", ""),
            }
            for item in items
        ]
        return {"content": [{"type": "text", "text": json.dumps(cw_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing coursework: {e}"}]}


@tool(
    "create_coursework",
    (
        "Create an assignment in a course. user_id: required. course_id: required. "
        "title: required. description: optional. max_points: optional int (default 100). "
        "work_type: optional ('ASSIGNMENT' or 'SHORT_ANSWER_QUESTION' or 'MULTIPLE_CHOICE_QUESTION', default 'ASSIGNMENT'). "
        "due_date: optional (YYYY-MM-DD). due_time: optional (HH:MM, 24h). "
        "topic_id: optional (assign to a topic). state: optional ('PUBLISHED' or 'DRAFT', default 'PUBLISHED')."
    ),
    {"user_id": int, "course_id": str, "title": str, "description": str, "max_points": int, "work_type": str, "due_date": str, "due_time": str, "topic_id": str, "state": str},
)
async def create_coursework(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])

        body: dict[str, Any] = {
            "title": str(args["title"]),
            "workType": str(args.get("work_type", "ASSIGNMENT")).upper(),
            "state": str(args.get("state", "PUBLISHED")).upper(),
            "maxPoints": int(args.get("max_points", 100)),
        }

        if args.get("description"):
            body["description"] = str(args["description"])
        if args.get("topic_id"):
            body["topicId"] = str(args["topic_id"])

        due_date_str = args.get("due_date", "")
        if due_date_str:
            parts = due_date_str.split("-")
            body["dueDate"] = {"year": int(parts[0]), "month": int(parts[1]), "day": int(parts[2])}

            due_time_str = args.get("due_time", "23:59")
            time_parts = due_time_str.split(":")
            body["dueTime"] = {"hours": int(time_parts[0]), "minutes": int(time_parts[1])}

        svc = _classroom(user_id)
        cw = svc.courses().courseWork().create(courseId=course_id, body=body).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "coursework_id": cw["id"],
                    "title": cw.get("title", ""),
                    "state": cw.get("state", ""),
                    "max_points": cw.get("maxPoints"),
                    "link": cw.get("alternateLink", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating coursework: {e}"}]}


@tool(
    "list_students",
    (
        "List enrolled students in a course. user_id: required. course_id: required. "
        "page_size: optional int (default 30)."
    ),
    {"user_id": int, "course_id": str, "page_size": int},
)
async def list_students(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        page_size = int(args.get("page_size", 30))

        svc = _classroom(user_id)
        result = svc.courses().students().list(
            courseId=course_id, pageSize=page_size
        ).execute()
        students = result.get("students", [])

        if not students:
            return {"content": [{"type": "text", "text": "No students enrolled in this course."}]}

        student_list = [
            {
                "student_id": s.get("userId", ""),
                "name": s.get("profile", {}).get("name", {}).get("fullName", ""),
                "email": s.get("profile", {}).get("emailAddress", ""),
            }
            for s in students
        ]
        return {"content": [{"type": "text", "text": json.dumps(student_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing students: {e}"}]}


@tool(
    "invite_student",
    (
        "Invite a student to a course by email. user_id: required. course_id: required. "
        "email: required (the student's email address)."
    ),
    {"user_id": int, "course_id": str, "email": str},
)
async def invite_student(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        email = str(args["email"])

        svc = _classroom(user_id)
        invitation = svc.invitations().create(body={
            "courseId": course_id,
            "userId": email,
            "role": "STUDENT",
        }).execute()
        return {"content": [{"type": "text", "text": json.dumps({
            "invitation_id": invitation.get("id", ""),
            "course_id": invitation.get("courseId", ""),
            "email": email,
            "role": invitation.get("role", ""),
        }, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error inviting student: {e}"}]}


@tool(
    "list_teachers",
    (
        "List teachers in a course. user_id: required. course_id: required. "
        "page_size: optional int (default 30)."
    ),
    {"user_id": int, "course_id": str, "page_size": int},
)
async def list_teachers(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        page_size = int(args.get("page_size", 30))

        svc = _classroom(user_id)
        result = svc.courses().teachers().list(
            courseId=course_id, pageSize=page_size
        ).execute()
        teachers = result.get("teachers", [])

        if not teachers:
            return {"content": [{"type": "text", "text": "No teachers found for this course."}]}

        teacher_list = [
            {
                "teacher_id": t.get("userId", ""),
                "name": t.get("profile", {}).get("name", {}).get("fullName", ""),
                "email": t.get("profile", {}).get("emailAddress", ""),
            }
            for t in teachers
        ]
        return {"content": [{"type": "text", "text": json.dumps(teacher_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing teachers: {e}"}]}


@tool(
    "list_announcements",
    (
        "List announcements for a course. user_id: required. course_id: required. "
        "page_size: optional int (default 20)."
    ),
    {"user_id": int, "course_id": str, "page_size": int},
)
async def list_announcements(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        page_size = int(args.get("page_size", 20))

        svc = _classroom(user_id)
        result = svc.courses().announcements().list(
            courseId=course_id, pageSize=page_size
        ).execute()
        announcements = result.get("announcements", [])

        if not announcements:
            return {"content": [{"type": "text", "text": "No announcements found for this course."}]}

        ann_list = [
            {
                "announcement_id": a["id"],
                "text": a.get("text", ""),
                "state": a.get("state", ""),
                "creation_time": a.get("creationTime", ""),
                "update_time": a.get("updateTime", ""),
                "link": a.get("alternateLink", ""),
            }
            for a in announcements
        ]
        return {"content": [{"type": "text", "text": json.dumps(ann_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing announcements: {e}"}]}


@tool(
    "create_announcement",
    (
        "Post an announcement to a course. user_id: required. course_id: required. "
        "text: required (the announcement body). state: optional ('PUBLISHED' or 'DRAFT', default 'PUBLISHED')."
    ),
    {"user_id": int, "course_id": str, "text": str, "state": str},
)
async def create_announcement(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])

        body: dict[str, Any] = {
            "text": str(args["text"]),
            "state": str(args.get("state", "PUBLISHED")).upper(),
        }

        svc = _classroom(user_id)
        ann = svc.courses().announcements().create(
            courseId=course_id, body=body
        ).execute()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "announcement_id": ann["id"],
                    "text": ann.get("text", ""),
                    "state": ann.get("state", ""),
                    "link": ann.get("alternateLink", ""),
                }, indent=2),
            }]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating announcement: {e}"}]}


@tool(
    "list_submissions",
    (
        "List student submissions for a coursework item. user_id: required. "
        "course_id: required. coursework_id: required. page_size: optional int (default 30). "
        "states: optional comma-separated ('NEW', 'CREATED', 'TURNED_IN', 'RETURNED', 'RECLAIMED_BY_STUDENT')."
    ),
    {"user_id": int, "course_id": str, "coursework_id": str, "page_size": int, "states": str},
)
async def list_submissions(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        coursework_id = str(args["coursework_id"])
        page_size = int(args.get("page_size", 30))

        svc = _classroom(user_id)
        kwargs: dict[str, Any] = {
            "courseId": course_id,
            "courseWorkId": coursework_id,
            "pageSize": page_size,
        }
        states = args.get("states", "")
        if states:
            kwargs["states"] = [s.strip().upper() for s in states.split(",") if s.strip()]

        result = svc.courses().courseWork().studentSubmissions().list(**kwargs).execute()
        submissions = result.get("studentSubmissions", [])

        if not submissions:
            return {"content": [{"type": "text", "text": "No submissions found."}]}

        sub_list = [
            {
                "submission_id": s["id"],
                "student_id": s.get("userId", ""),
                "state": s.get("state", ""),
                "assigned_grade": s.get("assignedGrade"),
                "draft_grade": s.get("draftGrade"),
                "late": s.get("late", False),
                "update_time": s.get("updateTime", ""),
            }
            for s in submissions
        ]
        return {"content": [{"type": "text", "text": json.dumps(sub_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing submissions: {e}"}]}


@tool(
    "grade_submission",
    (
        "Assign a grade to a student submission. user_id: required. course_id: required. "
        "coursework_id: required. submission_id: required. grade: required (numeric). "
        "return_submission: optional bool (default true — returns it to the student after grading)."
    ),
    {"user_id": int, "course_id": str, "coursework_id": str, "submission_id": str, "grade": float, "return_submission": bool},
)
async def grade_submission(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        coursework_id = str(args["coursework_id"])
        submission_id = str(args["submission_id"])
        grade = float(args["grade"])
        return_sub = bool(args.get("return_submission", True))

        svc = _classroom(user_id)

        svc.courses().courseWork().studentSubmissions().patch(
            courseId=course_id,
            courseWorkId=coursework_id,
            id=submission_id,
            updateMask="assignedGrade,draftGrade",
            body={"assignedGrade": grade, "draftGrade": grade},
        ).execute()

        if return_sub:
            svc.courses().courseWork().studentSubmissions().return_(
                courseId=course_id,
                courseWorkId=coursework_id,
                id=submission_id,
                body={},
            ).execute()

        return {"content": [{"type": "text", "text": json.dumps({
            "submission_id": submission_id,
            "assigned_grade": grade,
            "returned": return_sub,
        }, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error grading submission: {e}"}]}


@tool(
    "list_topics",
    (
        "List topics in a course. user_id: required. course_id: required. "
        "page_size: optional int (default 20)."
    ),
    {"user_id": int, "course_id": str, "page_size": int},
)
async def list_topics(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])
        page_size = int(args.get("page_size", 20))

        svc = _classroom(user_id)
        result = svc.courses().topics().list(
            courseId=course_id, pageSize=page_size
        ).execute()
        topics = result.get("topic", [])

        if not topics:
            return {"content": [{"type": "text", "text": "No topics found for this course."}]}

        topic_list = [
            {
                "topic_id": t["topicId"],
                "name": t.get("name", ""),
                "update_time": t.get("updateTime", ""),
            }
            for t in topics
        ]
        return {"content": [{"type": "text", "text": json.dumps(topic_list, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing topics: {e}"}]}


@tool(
    "create_topic",
    (
        "Create a topic in a course for organizing coursework. "
        "user_id: required. course_id: required. name: required."
    ),
    {"user_id": int, "course_id": str, "name": str},
)
async def create_topic(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(args["user_id"])
        course_id = str(args["course_id"])

        svc = _classroom(user_id)
        topic = svc.courses().topics().create(
            courseId=course_id,
            body={"name": str(args["name"])},
        ).execute()
        return {"content": [{"type": "text", "text": json.dumps({
            "topic_id": topic["topicId"],
            "name": topic.get("name", ""),
        }, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating topic: {e}"}]}


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

classroom_server = create_sdk_mcp_server(
    name="classroom",
    version="1.0.0",
    tools=[
        list_courses,
        get_course,
        create_course,
        update_course,
        list_coursework,
        create_coursework,
        list_students,
        invite_student,
        list_teachers,
        list_announcements,
        create_announcement,
        list_submissions,
        grade_submission,
        list_topics,
        create_topic,
    ],
)
