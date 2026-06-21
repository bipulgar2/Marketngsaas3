import sys
sys.path.append('.')
from api.index import app
from api.dataforseo_client import fetch_domain_metrics

dm = fetch_domain_metrics("weedposters.io")
data = {
    "domain_metrics": dm,
    "organic_keywords": []
}

rank_overview = data.get('domain_rank') or data.get('domain_metrics', {})
keywords = data.get('organic_keywords', [])
total_keywords = data.get('total_keywords', 0)

if total_keywords == 0:
    total_keywords = rank_overview.get('total_keywords', 0)

page_1_count = 0
for kw in keywords:
    page_1_count += 1

if page_1_count == 0 and not keywords:
    metrics = rank_overview.get('metrics', {}) if rank_overview else {}
    organic_metrics = metrics.get('organic') if metrics else {}
    if organic_metrics:
        page_1_count = organic_metrics.get('pos_1', 0) + organic_metrics.get('pos_2_3', 0) + organic_metrics.get('pos_4_10', 0)

print(f"Rank Overview: {rank_overview.keys()}")
if 'metrics' in rank_overview:
    print(f"Metrics: {rank_overview['metrics'].keys() if rank_overview['metrics'] else 'empty'}")
print(f"Page 1 Count: {page_1_count}")
