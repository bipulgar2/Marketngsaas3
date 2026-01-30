import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

# Unbuffered stdout
sys.stdout.reconfigure(line_buffering=True)

print("Starting debug script...", file=sys.stderr)

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

try:
    supabase = create_client(url, key)
    
    print("\n--- USERS ---", file=sys.stderr)
    users = supabase.table('profiles').select('*').execute()
    for u in users.data:
        print(f"User: {u.get('email', 'N/A')} | FullName: {u.get('full_name')} | Org: {u.get('organization_id')}", file=sys.stderr)

    print("\n--- CAMPAIGNS ---", file=sys.stderr)
    # Check for NULL orgs specifically too
    campaigns = supabase.table('campaigns').select('*').execute()
    for c in campaigns.data:
        print(f"Campaign: {c.get('domain')} | Org: {c.get('organization_id')}", file=sys.stderr)

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
