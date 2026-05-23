import os
import sys
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

if not os.getenv('SUPABASE_URL'):
    print("No SUPABASE_URL")
    sys.exit(1)

supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY'))
res = supabase.table('audits').select('*').order('created_at', desc=True).limit(5).execute()
for audit in res.data:
    print(f"ID: {audit['id']}, Type: {audit['type']}, Status: {audit['status']}, DFS ID: {audit.get('dataforseo_task_id')}")
