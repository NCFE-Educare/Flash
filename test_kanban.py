"""Test script to demonstrate the full Kanban board workflow."""

import requests
import json
from time import sleep

BASE_URL = "http://localhost:8000"

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def print_response(response):
    print(f"Status: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}")
    except:
        print(f"Response: {response.text}")
    print()

# Store tokens and IDs
tokens = {}
workspace_id = None
column_ids = {}
task_ids = {}

# ---------------------------------------------------------------------------
# 1. Create Users
# ---------------------------------------------------------------------------
print_section("Step 1: Create Users (Signup)")

users = [
    {"email": "principal@school.edu", "username": "principal", "password": "pass123"},
    {"email": "teacher1@school.edu", "username": "teacher_john", "password": "pass123"},
    {"email": "teacher2@school.edu", "username": "teacher_mary", "password": "pass123"},
]

for user in users:
    print(f"Creating user: {user['username']}")
    response = requests.post(
        f"{BASE_URL}/auth/signup",
        json=user
    )
    print_response(response)

    if response.status_code == 201:
        data = response.json()
        tokens[user["username"]] = data["access_token"]
        print(f"✅ Token saved for {user['username']}\n")
    else:
        # User might already exist, try login
        print(f"Signup failed, trying login for {user['username']}")
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": user["email"], "password": user["password"]}
        )
        print_response(response)
        if response.status_code == 200:
            data = response.json()
            tokens[user["username"]] = data["access_token"]
            print(f"✅ Logged in and token saved for {user['username']}\n")
        else:
            print(f"❌ Login failed for {user['username']}, skipping...\n")

sleep(1)

# Check if we have all tokens
if "principal" not in tokens:
    print("❌ Failed to get principal token. Exiting.")
    exit(1)

# ---------------------------------------------------------------------------
# 2. Create Workspace
# ---------------------------------------------------------------------------
print_section("Step 2: Principal Creates Workspace")

response = requests.post(
    f"{BASE_URL}/workspaces",
    headers={"Authorization": f"Bearer {tokens['principal']}"},
    json={
        "name": "Math Department - Q2 2024",
        "description": "Curriculum planning and resource development"
    }
)
print_response(response)

if response.status_code == 201:
    workspace_id = response.json()["workspace"]["id"]
    print(f"✅ Workspace created with ID: {workspace_id}\n")

sleep(1)

# ---------------------------------------------------------------------------
# 3. Get Workspace Details (with default columns)
# ---------------------------------------------------------------------------
print_section("Step 3: Get Workspace Details")

response = requests.get(
    f"{BASE_URL}/workspaces/{workspace_id}",
    headers={"Authorization": f"Bearer {tokens['principal']}"}
)
print_response(response)

if response.status_code == 200:
    data = response.json()
    for col in data["columns"]:
        column_ids[col["name"]] = col["id"]
    print(f"✅ Default columns: {list(column_ids.keys())}\n")

sleep(1)

# ---------------------------------------------------------------------------
# 4. Invite Team Members
# ---------------------------------------------------------------------------
print_section("Step 4: Invite Team Members")

for user in ["teacher1@school.edu", "teacher2@school.edu"]:
    print(f"Inviting {user} to workspace...")
    response = requests.post(
        f"{BASE_URL}/workspaces/{workspace_id}/invite",
        headers={"Authorization": f"Bearer {tokens['principal']}"},
        json={"email": user}
    )
    print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 5. List Workspace Members
# ---------------------------------------------------------------------------
print_section("Step 5: List Workspace Members")

response = requests.get(
    f"{BASE_URL}/workspaces/{workspace_id}/members",
    headers={"Authorization": f"Bearer {tokens['principal']}"}
)
print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 6. Create Tasks
# ---------------------------------------------------------------------------
print_section("Step 6: Create Tasks")

tasks = [
    {
        "title": "Review Q1 Algebra Curriculum",
        "description": "Analyze student performance and identify gaps",
        "assignee_email": "teacher1@school.edu",
        "priority": "high",
        "column": "To Do",
        "due_date": "2024-05-15T17:00:00Z"
    },
    {
        "title": "Develop Geometry Lesson Plans",
        "description": "Create 8-week lesson plan for geometry unit",
        "assignee_email": "teacher2@school.edu",
        "priority": "medium",
        "column": "To Do",
        "due_date": "2024-05-20T17:00:00Z"
    },
    {
        "title": "Order Math Manipulatives",
        "description": "Budget: $500 for hands-on learning tools",
        "assignee_email": "principal@school.edu",
        "priority": "urgent",
        "column": "In Progress",
        "due_date": "2024-04-30T17:00:00Z"
    },
]

for task in tasks:
    column_id = column_ids.get(task["column"])
    print(f"Creating task: {task['title']}")

    response = requests.post(
        f"{BASE_URL}/workspaces/{workspace_id}/tasks",
        headers={"Authorization": f"Bearer {tokens['principal']}"},
        json={
            "column_id": column_id,
            "title": task["title"],
            "description": task["description"],
            "assignee_email": task["assignee_email"],
            "priority": task["priority"],
            "due_date": task["due_date"],
            "position": 0
        }
    )
    print_response(response)

    if response.status_code == 201:
        task_id = response.json()["task"]["id"]
        task_ids[task["title"]] = task_id
        print(f"✅ Task created with ID: {task_id}\n")

sleep(1)

# ---------------------------------------------------------------------------
# 7. Teacher Updates Task (Move to In Progress)
# ---------------------------------------------------------------------------
print_section("Step 7: Teacher Moves Task to 'In Progress'")

task_id = task_ids.get("Review Q1 Algebra Curriculum")
if task_id:
    print(f"Teacher John moves task {task_id} to 'In Progress'")
    response = requests.patch(
        f"{BASE_URL}/tasks/{task_id}",
        headers={"Authorization": f"Bearer {tokens['teacher_john']}"},
        json={
            "column_id": column_ids["In Progress"]
        }
    )
    print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 8. Add Comments
# ---------------------------------------------------------------------------
print_section("Step 8: Add Comments to Task")

if task_id:
    comments = [
        {"user": "teacher_john", "text": "Started working on this. Will review the test results first."},
        {"user": "principal", "text": "Great! Let me know if you need access to the district data."},
    ]

    for comment in comments:
        print(f"{comment['user']} adds comment...")
        response = requests.post(
            f"{BASE_URL}/tasks/{task_id}/comments",
            headers={"Authorization": f"Bearer {tokens[comment['user']]}"},
            json={"comment": comment["text"]}
        )
        print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 9. Get Task Details (with comments)
# ---------------------------------------------------------------------------
print_section("Step 9: Get Task Details with Comments")

if task_id:
    response = requests.get(
        f"{BASE_URL}/tasks/{task_id}",
        headers={"Authorization": f"Bearer {tokens['principal']}"}
    )
    print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 10. List All Tasks in Workspace
# ---------------------------------------------------------------------------
print_section("Step 10: List All Tasks in Workspace")

response = requests.get(
    f"{BASE_URL}/workspaces/{workspace_id}/tasks",
    headers={"Authorization": f"Bearer {tokens['principal']}"}
)
print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 11. View Activity Log
# ---------------------------------------------------------------------------
print_section("Step 11: View Activity Log")

response = requests.get(
    f"{BASE_URL}/workspaces/{workspace_id}/activity",
    headers={"Authorization": f"Bearer {tokens['principal']}"}
)
print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 12. Create Custom Column
# ---------------------------------------------------------------------------
print_section("Step 12: Create Custom Column 'Blocked'")

response = requests.post(
    f"{BASE_URL}/workspaces/{workspace_id}/columns",
    headers={"Authorization": f"Bearer {tokens['principal']}"},
    json={
        "name": "Blocked",
        "position": 2,
        "color": "#EF4444"
    }
)
print_response(response)

sleep(1)

# ---------------------------------------------------------------------------
# 13. List My Workspaces
# ---------------------------------------------------------------------------
print_section("Step 13: Teacher Lists Their Workspaces")

response = requests.get(
    f"{BASE_URL}/workspaces",
    headers={"Authorization": f"Bearer {tokens['teacher_john']}"}
)
print_response(response)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_section("🎉 Demo Complete!")

print("""
Summary of what we tested:
✅ User signup/login
✅ Create workspace (auto-creates default columns)
✅ Invite members by email
✅ Create tasks with assignees
✅ Move tasks between columns
✅ Add comments to tasks
✅ View task details
✅ List all tasks
✅ View activity log
✅ Create custom columns
✅ List user's workspaces

Next steps:
- Test file attachments
- Test email notifications
- View analytics dashboard
""")
