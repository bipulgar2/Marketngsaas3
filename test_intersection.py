import os
import requests
import json
import base64

DATAFORSEO_API_URL = "https://api.dataforseo.com/v3"

def get_auth_header():
    login = os.getenv('DATAFORSEO_LOGIN')
    password = os.getenv('DATAFORSEO_PASSWORD')
    credentials = f"{login}:{password}"
    encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    return {
        'Authorization': f'Basic {encoded}',
        'Content-Type': 'application/json'
    }

endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/domain_intersection/live"
payload = [{
    "target1": "growthx.club",
    "target2": "apple.com",
    "location_code": 2840,
    "language_code": "en",
    "intersection_mode": "target2_only",
    "limit": 5
}]

print("Testing intersection...")
response = requests.post(
    endpoint,
    headers=get_auth_header(),
    json=payload
)
print(response.status_code)
print(json.dumps(response.json(), indent=2))
