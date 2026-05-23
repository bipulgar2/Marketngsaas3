import sys, os
sys.path.append(os.getcwd())
from api.index import supabase

res = supabase.table('audits').select('id, domain, url, results').order('created_at', desc=True).limit(1).execute()
audit = res.data[0]
results = audit.get('results', {})
print(f"URL: {audit.get('url')} | Domain: {audit.get('domain')}")
print(f"Domain Traffic: {results.get('domain_totals')}")
pagespeed = results.get('pagespeed', {})
print(f"Pagespeed: {pagespeed}")
