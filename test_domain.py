from execution.pagespeed_insights import fetch_pagespeed_scores
from api.dataforseo_client import fetch_domain_metrics

print("Testing PageSpeed...")
ps = fetch_pagespeed_scores("https://weedposters.io", strategy="mobile")
print("PageSpeed success:", ps.get('success'))

print("Testing DataForSEO...")
dm = fetch_domain_metrics("weedposters.io")
print("DataForSEO success:", dm.get('success'))
print("Traffic:", dm.get('total_traffic'))
