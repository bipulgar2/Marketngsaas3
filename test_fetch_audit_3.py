import os, json
from supabase import create_client

os.environ['SUPABASE_URL'] = 'https://kalbykwfjtirrotzphcx.supabase.co'
os.environ['SUPABASE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthbGJ5a3dmanRpcnJvdHpwaGN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTA3MDgzNiwiZXhwIjoyMDg0NjQ2ODM2fQ.Z-yyeus3PmaoaQVaoDkGB8tC85xpPAhooXcOkyshhD8'

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

audit_id = "d2c6eee6-c57a-49b1-b1d3-4ac90951fdc9"
response = supabase.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
audit = response.data

results = audit.get('results', {}) or {}
campaign_data = audit.get('campaigns', {}) or {}
domain = results.get('competitor_domain') or campaign_data.get('domain', '')

flat_audit = {
    **audit,
    'domain': domain,
    'keywords': results.get('keywords', []),
    'pages': results.get('pages', []),
    'pagespeed': results.get('pagespeed', {}),
    'backlinks': results.get('backlinks', {}),
    'backlinks_summary': results.get('backlinks_summary', results.get('backlinks', {})),
    'referring_domains': results.get('referring_domains', []),
    'total_keywords': results.get('total_keywords', 0),
    'total_traffic': results.get('total_traffic', 0),
    'keywords_at_limit': results.get('keywords_at_limit', 0)
}

try:
    json.dumps({'success': True, 'audit': flat_audit})
    print("JSON dumps succeeded")
except Exception as e:
    print("JSON dumps failed:", e)
