import asyncio
import json
from artifacts_tools import create_artifact_tool, update_artifact_tool, execute_python_artifact_tool
from database import init_db, create_user, create_session, get_artifacts_for_session

async def test():
    print("--- Initializing DB ---")
    init_db()
    
    # Setup mock user and session
    user = create_user("test@example.com", "testuser", "password")
    user_id = user["id"] if user else 1
    session = create_session(user_id, "Test Session")
    session_id = session["id"]
    
    print(f"User ID: {user_id}, Session ID: {session_id}")
    
    # 1. Create Artifact
    print("\n--- Testing create_artifact ---")
    create_args = {
        "session_id": session_id,
        "user_id": user_id,
        "identifier": "hello-world",
        "title": "Hello World Script",
        "type": "application/vnd.ant.code",
        "language": "python",
        "content": "print('Hello from the artifact!')\nimport sys\nprint(f'Python version: {sys.version}')"
    }
    res = await create_artifact_tool(create_args)
    print(res["content"][0]["text"])
    
    # 2. Update Artifact
    print("\n--- Testing update_artifact ---")
    update_args = {
        "session_id": session_id,
        "identifier": "hello-world",
        "title": "Updated Hello World",
        "content": "print('Hello from the UPDATED artifact!')\nimport math\nprint(f'Pi is approx {math.pi}')"
    }
    res = await update_artifact_tool(update_args)
    print(res["content"][0]["text"])
    
    # 3. List Artifacts
    print("\n--- Testing listing artifacts ---")
    artifacts = get_artifacts_for_session(session_id)
    print(f"Found {len(artifacts)} artifacts.")
    for a in artifacts:
        print(f"- {a['identifier']}: {a['title']} (v{a['version']})")
        
    # 4. Execute Artifact
    print("\n--- Testing execute_python_artifact ---")
    exec_args = {
        "session_id": session_id,
        "identifier": "hello-world"
    }
    res = await execute_python_artifact_tool(exec_args)
    print(res["content"][0]["text"])

if __name__ == "__main__":
    asyncio.run(test())
