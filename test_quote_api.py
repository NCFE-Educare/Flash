import requests
import json

BASE_URL = "http://localhost:8000"

def test_quote_generation():
    # 1. Login to get token (assuming test user exists or we use a known one)
    # For simplicity, if you have a token, use it here.
    # Otherwise, this test might fail due to auth.
    # I'll try to reach the endpoint. If it returns 401, the endpoint is at least there.
    
    payload = {
        "topic": "Success",
        "mood": "Energetic"
    }
    
    print(f"Testing POST /quote/generate with payload: {payload}")
    try:
        response = requests.post(f"{BASE_URL}/quote/generate", json=payload)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    test_quote_generation()
