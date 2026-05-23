from api.index import app, supabase
with app.app_context():
    res = supabase.table('audits').select('id, url, results').order('created_at', desc=True).limit(1).execute()
    audit = res.data[0]
    results = audit.get('results', {})
    print(f"URL: {audit.get('url')}")
    pagespeed = results.get('pagespeed', {})
    import json
    print(json.dumps(pagespeed, indent=2))
