import sys
import json
sys.path.append('.')
from api.dataforseo_client import fetch_ranked_keywords
res = fetch_ranked_keywords("weedposters.io", limit=5)
if res.get('success'):
    kws = res.get('keywords', [])
    if kws:
        print(json.dumps(kws[0]['ranked_serp_element'], indent=2)[:500])
