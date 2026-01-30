import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

print("Starting inspection...")
load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("Error: Missing credentials")
    sys.exit(1)

print(f"Connecting to {url}...")
try:
    supabase: Client = create_client(url, key)
    # limit(1) might return empty if RLS blocks it (but used service role key)
    response = supabase.table('campaigns').select('*').limit(1).execute()
    print(f"Data count: {len(response.data)}")
    if response.data:
        print("First record keys:", list(response.data[0].keys()))
    else:
        print("No campaigns found.")
except Exception as e:
    print(f"Exception: {e}")
