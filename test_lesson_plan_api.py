import requests

BASE_URL = "http://localhost:8000"

def test_lesson_plan_generation():
    # Since this is a multipart/form-data endpoint, we use 'data' and 'files'
    data = {
        "grade": "7",
        "topic": "Photosynthesis",
        "criteria": "Include a hands-on activity with leaves and clear diagrams."
    }
    
    # Optional file (mocked as a text file for simplicity)
    files = {
        "file": ("notes.txt", "Photosynthesis is the process by which plants make food.")
    }
    
    print(f"Testing POST /lesson-plan/generate with data: {data}")
    try:
        # We skip the auth token check here to see if the endpoint is reachable
        # In real usage, you'd add: headers={"Authorization": f"Bearer {token}"}
        response = requests.post(f"{BASE_URL}/lesson-plan/generate", data=data, files=files)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Response:", response.json().get("lesson_plan")[:200], "...")
        else:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    test_lesson_plan_generation()
