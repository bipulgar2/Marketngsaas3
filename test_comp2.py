import sys, json, requests
sys.path.append('.')
from api.dataforseo_client import get_auth_header
r = requests.post(
    'https://api.dataforseo.com/v3/dataforseo_labs/google/competitors_domain/live',
    headers={**get_auth_header(), 'Content-Type': 'application/json'},
    json=[{'target': 'weedposters.io', 'location_code': 2840, 'language_code': 'en', 'limit': 15}]
)
for item in r.json()['tasks'][0]['result'][0]['items']:
    print(item['domain'], "Intersections:", item['intersections'])
    # Does item contain categories?
    if 'categories' in item:
        print("Categories:", item['categories'])
