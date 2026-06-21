import sys, json, requests
sys.path.append('.')
from api.dataforseo_client import get_auth_header
r = requests.post(
    'https://api.dataforseo.com/v3/backlinks/summary/live',
    headers={**get_auth_header(), 'Content-Type': 'application/json'},
    json=[{'target': 'leafly.com'}]
)
print(json.dumps(r.json(), indent=2)[:500])
