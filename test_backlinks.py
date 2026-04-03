import os
from api.dataforseo_client import fetch_backlinks_summary, get_referring_domains

domain = "apple.com"
print(f"Testing fetch_backlinks_summary for {domain}...")
summary = fetch_backlinks_summary(domain)
print("Summary result:", summary)

print(f"\nTesting get_referring_domains for {domain}...")
domains = get_referring_domains(domain, limit=3)
print(f"Found {len(domains)} referring domains.")
for d in domains:
    print(d)
