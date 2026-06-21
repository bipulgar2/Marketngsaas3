import sys
import requests
import json
sys.path.append('.')
from api.dataforseo_client import get_auth_header

endpoint = "https://api.dataforseo.com/v3/dataforseo_labs/google/domain_rank_overview/live"
payload = [{"target": "weedposters.io", "location_code": 2840, "language_code": "en"}]
response = requests.post(
    endpoint,
    headers={**get_auth_header(), "Content-Type": "application/json"},
    json=payload
)
print(json.dumps(response.json(), indent=2)[:500])
