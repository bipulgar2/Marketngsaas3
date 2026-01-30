import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

sys.stdout.reconfigure(line_buffering=True)

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("Error: Credentials missing")
    sys.exit(1)

supabase: Client = create_client(url, key)

# 1. Find the main user (Bipul)
print("Searching for main user...")
# Use 'ilike' for case-insensitive search if supported, otherwise fetch all and filter
profiles = supabase.table('profiles').select('*').execute()

target_user = None
for p in profiles.data:
    email = p.get('email', '')
    if 'bipul' in email.lower():
        target_user = p
        break

if not target_user:
    # Fallback: exact match for what showed in screenshot if possible, or just the first admin?
    # Let's try to find ANY user with an Organization ID first
    for p in profiles.data:
        if p.get('organization_id'):
            target_user = p
            break

if not target_user:
    print("CRITICAL: No user with an organization found. Cannot rescue data.")
    sys.exit(1)

org_id = target_user.get('organization_id')
print(f"Target User: {target_user.get('email')} | Target Org: {org_id}")

if not org_id:
    print("CRITICAL: Main user has no Organization ID. Login backfill failed?")
    sys.exit(1)

# 2. Count Orphans
orphans = supabase.table('campaigns').select('id, name').is_('organization_id', 'null').execute()
count = len(orphans.data)
print(f"Found {count} orphaned campaigns.")

if count == 0:
    print("No orphans to rescue.")
    sys.exit(0)

# 3. Rescue
print(f"Rescuing {count} campaigns to Organization {org_id}...")
update_res = supabase.table('campaigns').update({'organization_id': org_id}).is_('organization_id', 'null').execute()

print("SUCCESS: Data rescued.")
