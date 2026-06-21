import sys, json, requests
sys.path.append('.')
from api.dataforseo_client import get_auth_header
r = requests.post(
    'https://api.dataforseo.com/v3/dataforseo_labs/google/competitors_domain/live',
    headers={**get_auth_header(), 'Content-Type': 'application/json'},
    json=[{'target': 'weedposters.io', 'location_code': 2840, 'language_code': 'en', 'limit': 3}]
)
print(json.dumps(r.json(), indent=2)[:1500])
