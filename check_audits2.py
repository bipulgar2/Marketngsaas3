import os
import sys

os.environ['SUPABASE_URL'] = 'https://kalbykwfjtirrotzphcx.supabase.co'
os.environ['SUPABASE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthbGJ5a3dmanRpcnJvdHpwaGN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTA3MDgzNiwiZXhwIjoyMDg0NjQ2ODM2fQ.Z-yyeus3PmaoaQVaoDkGB8tC85xpPAhooXcOkyshhD8'

from supabase import create_client

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
res = supabase.table('audits').select('*').order('created_at', desc=True).limit(5).execute()
for audit in res.data:
    print(f"ID: {audit['id']}, Type: {audit['type']}, Status: {audit['status']}, DFS ID: {audit.get('dataforseo_task_id')}")
