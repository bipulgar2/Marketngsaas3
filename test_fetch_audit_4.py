import os
from supabase import create_client

os.environ['SUPABASE_URL'] = 'https://kalbykwfjtirrotzphcx.supabase.co'
os.environ['SUPABASE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthbGJ5a3dmanRpcnJvdHpwaGN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTA3MDgzNiwiZXhwIjoyMDg0NjQ2ODM2fQ.Z-yyeus3PmaoaQVaoDkGB8tC85xpPAhooXcOkyshhD8'

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

response = supabase.table('audits').select('id, results').order('created_at', desc=True).limit(20).execute()
for row in response.data:
    results = row.get('results')
    if results is not None:
        if not isinstance(results, dict):
            print(f"Audit {row['id']} has results of type {type(results)}")
        else:
            print(f"Audit {row['id']} has results dict, keys: {list(results.keys())[:3]}")
    else:
        print(f"Audit {row['id']} has NO results")
