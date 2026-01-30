import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(url, key)

try:
    response = supabase.table('campaigns').select('*').limit(1).execute()
    if response.data:
        print("Keys:", response.data[0].keys())
    else:
        print("No data in campaigns table to inspect keys.")
except Exception as e:
    print(f"Error: {e}")
