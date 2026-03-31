import asyncio
import json
import os
from api import google_chat_webhook, app
from database import get_messages, get_session_by_title, init_db
from fastapi import BackgroundTasks

# Mock environment
os.environ["GCHAT_BOT_NAME"] = "agent"
os.environ["GCHAT_BOT_USER_ID"] = "12345"

async def test_webhook():
    print("--- Testing Google Chat Webhook (Mock) ---")
    init_db() # Trigger migration
    
    # Mock request data (Group mention)
    mock_request = {
        "type": "MESSAGE",
        "space": {"name": "spaces/mock_space_123"},
        "message": {
            "sender": {"email": "vansh@gmail.com"},
            "text": "@agent hello world this is a test"
        }
    }
    
    # We need to mock BackgroundTasks
    bg_tasks = BackgroundTasks()
    
    # Call the webhook
    response = await google_chat_webhook(mock_request, bg_tasks)
    print(f"Webhook response: {response}")
    
    # Verify the background tasks are added
    print(f"Number of background tasks: {len(bg_tasks.tasks)}")
    assert len(bg_tasks.tasks) > 0
    
    # Manually execute the background task to verify it works without error
    # Note: This will try to call _run_agent_in_thread and send_gchat_message.
    # We expect send_gchat_message to print a warning since service-account.json is missing.
    print("\n--- Running background task manually ---")
    task = bg_tasks.tasks[0]
    func = task.func
    kwargs = task.kwargs
    
    try:
        # We'll run the function directly. It's a synchronous function that spawns threads.
        func(**kwargs)
        print("Background task executed successfully (check logs above).")
        
        # Verify DB session exists
        session = get_session_by_title("GChat: spaces/mock_space_123", user_id=None)
        if session:
            print(f"Session found: {session['id']} - {session['title']}")
            messages = get_messages(session['id'])
            print(f"Messages in session: {len(messages)}")
            for msg in messages:
                print(f"  [{msg['role']}]: {msg['content'][:50]}...")
        else:
            print("ERROR: Session not found in DB!")
            
    except Exception as e:
        print(f"ERROR during background task execution: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook())
