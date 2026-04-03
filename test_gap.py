import sys
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv('.env.local')
load_dotenv('.env')
sys.path.append('.')

from api.dataforseo_client import get_keyword_gap, get_organic_keywords, DATAFORSEO_API_URL, get_auth_header

endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/ranked_keywords/live"
payload = [{
    "target": "supergoop.com",
    "location_code": 2840,
    "language_code": "en",
    "limit": 10000,
    "include_serp_info": True
}]

print("Testing direct 10k request...")
try:
    response = requests.post(
        endpoint,
        headers={**get_auth_header(), "Content-Type": "application/json"},
        json=payload,
        timeout=60
    )
    print("Status:", response.status_code)
    data = response.json()
    if data.get('tasks') and data['tasks'][0].get('result'):
        items = data['tasks'][0]['result'][0].get('items', [])
        print("Success! Got items:", len(items))
    else:
        print("No result.", data.get('tasks', [{}])[0].get('status_message', 'Unknown'))
except Exception as e:
    print("Error:", e)
