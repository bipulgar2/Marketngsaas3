import sys
sys.path.append('.')
from api.supabase_client import supabase
res = supabase.table('campaigns').select('*').limit(1).execute()
print(res.data)
