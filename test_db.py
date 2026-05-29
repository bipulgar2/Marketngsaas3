import os
import json
from dotenv import load_dotenv
load_dotenv(".env")
load_dotenv(".env.local")
from supabase import create_client

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
db = create_client(supabase_url, supabase_key)
campaign_id = "e2f1372a-3e9b-43ab-b832-bcdb7adf4e33"

import time
start = time.time()
audit_res = db.table('audits').select('id, pages:results->pages, domain:results->>domain, comp_domain:results->>competitor_domain').eq('campaign_id', campaign_id).eq('type', 'technical').order('created_at', desc=True).limit(5).execute()
print(f"Query took: {time.time() - start:.2f}s")
print(f"Data length: {len(json.dumps(audit_res.data)) / 1024:.2f} KB")

if audit_res.data:
    for audit_record in audit_res.data:
        pages = audit_record.get('pages', [])
        print(f"Audit {audit_record['id']} has {len(pages) if pages else 0} pages")

