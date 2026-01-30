import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("Error: Credentials missing")
    exit(1)

supabase: Client = create_client(url, key)

print("--- USERS & ORGS ---")
users = supabase.table('profiles').select('id, email, full_name, role, organization_id').execute()
for u in users.data:
    print(f"User: {u['email']} | OrgID: {u['organization_id']} | Role: {u['role']}")

print("\n--- ORGANIZATIONS ---")
orgs = supabase.table('organizations').select('*').execute()
for o in orgs.data:
    print(f"Org: {o['id']} | Name: {o['name']}")

print("\n--- CAMPAIGNS ---")
campaigns = supabase.table('campaigns').select('id, name, domain, organization_id').execute()
for c in campaigns.data:
    print(f"Campaign: {c['domain']} ({c['name']}) | OrgID: {c['organization_id']}")

