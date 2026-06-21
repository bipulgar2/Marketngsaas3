import sys
import json
sys.path.append('.')
from api.dataforseo_client import fetch_ranked_keywords
import requests

def fetch_cost():
    endpoint = "https://api.dataforseo.com/v3/dataforseo_labs/google/ranked_keywords/live"
    from api.dataforseo_client import get_auth_header
    payload = [{
        "target": "weedposters.io",
        "location_code": 2840,
        "language_code": "en",
        "limit": 100,
        "include_serp_info": True
    }]
    response = requests.post(
        endpoint,
        headers={**get_auth_header(), "Content-Type": "application/json"},
        json=payload
    )
    data = response.json()
    print("Cost:", data.get('cost', 'unknown'))

fetch_cost()
