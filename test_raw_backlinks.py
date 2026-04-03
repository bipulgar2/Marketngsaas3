import os
import requests
import json
import base64

DATAFORSEO_API_URL = "https://api.dataforseo.com/v3"
login = os.getenv('DATAFORSEO_LOGIN')
password = os.getenv('DATAFORSEO_PASSWORD')

credentials = f"{login}:{password}"
encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
headers = {
    'Authorization': f'Basic {encoded_credentials}',
    'Content-Type': 'application/json'
}

print("Testing raw DataForSEO backlinks/summary/live endpoint...")
endpoint = f"{DATAFORSEO_API_URL}/backlinks/summary/live"
payload = [{"target": "apple.com", "include_subdomains": True}]
response = requests.post(endpoint, headers=headers, json=payload)
print(f"Status Code: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
