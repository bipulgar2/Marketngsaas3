import os
from supabase import create_client

os.environ['SUPABASE_URL'] = 'https://kalbykwfjtirrotzphcx.supabase.co'
os.environ['SUPABASE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImthbGJ5a3dmanRpcnJvdHpwaGN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTA3MDgzNiwiZXhwIjoyMDg0NjQ2ODM2fQ.Z-yyeus3PmaoaQVaoDkGB8tC85xpPAhooXcOkyshhD8'

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

audit_id = "d2c6eee6-c57a-49b1-b1d3-4ac90951fdc9"
response = supabase.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
audit = response.data

print("campaigns type:", type(audit.get('campaigns')))
print("campaigns value:", audit.get('campaigns'))
