import os, sys, requests

# Get session cookie from env or we can just bypass login if we are using the deployed URL?
# Actually, it's easier to just look at the server logs on Railway if possible, or run the same logic locally against the prod database.
from supabase import create_client
import json

os.environ['SUPABASE_URL'] = 'https://kalbykwfjtirrotzphcx.supabase.co'
os.environ['SUPABASE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthbGJ5a3dmanRpcnJvdHpwaGN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTA3MDgzNiwiZXhwIjoyMDg0NjQ2ODM2fQ.Z-yyeus3PmaoaQVaoDkGB8tC85xpPAhooXcOkyshhD8'

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

audit_id = "d2c6eee6-c57a-49b1-b1d3-4ac90951fdc9"
response = supabase.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
audit = response.data

if not audit:
    print("Audit not found")
    sys.exit(1)

# Now what does the endpoint do with this data?
# Let's check api/index.py line 2849
print("Status:", audit.get('status'))
print("Has results?", bool(audit.get('results')))

# Let's check the legacy data support code which might be failing
results = audit.get('results') or {}
print("Pages count in results:", len(results.get('pages', [])))
print("Categorized exists?", bool(results.get('categorized')))

