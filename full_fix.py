import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    sys.exit(1)

supabase = create_client(url, key)

print("=" * 60)
print("STEP 1: DIAGNOSING DATABASE STATE")
print("=" * 60)

# Get all profiles
profiles = supabase.table('profiles').select('*').execute()
print(f"\nFound {len(profiles.data)} user profiles:")
for p in profiles.data:
    print(f"  - ID: {p['id'][:8]}... | Email: {p.get('email', 'N/A')} | OrgID: {p.get('organization_id')}")

# Get all organizations
orgs = supabase.table('organizations').select('*').execute()
print(f"\nFound {len(orgs.data)} organizations:")
for o in orgs.data:
    print(f"  - ID: {o['id'][:8]}... | Name: {o.get('name')}")

# Get all campaigns
campaigns = supabase.table('campaigns').select('*').execute()
print(f"\nFound {len(campaigns.data)} campaigns:")
for c in campaigns.data:
    print(f"  - ID: {c['id'][:8]}... | Domain: {c.get('domain')} | OrgID: {c.get('organization_id')}")

# Get all audits
audits = supabase.table('audits').select('*').execute()
print(f"\nFound {len(audits.data)} audits:")
for a in audits.data:
    print(f"  - ID: {a['id'][:8]}... | CampaignID: {a.get('campaign_id', 'N/A')[:8]}... | Status: {a.get('status')}")

print("\n" + "=" * 60)
print("STEP 2: FIXING DATA")
print("=" * 60)

# Find primary user (bipulgarrera - without the '1')
primary_user = None
secondary_user = None
for p in profiles.data:
    email = (p.get('email') or '').lower()
    if 'bipulgarrera1' in email or 'bipul garera1' in email.replace(' ', ''):
        secondary_user = p
    elif 'bipulgarrera' in email or 'bipul' in email:
        primary_user = p

print(f"\nPrimary User: {primary_user.get('email') if primary_user else 'NOT FOUND'}")
print(f"Secondary User: {secondary_user.get('email') if secondary_user else 'NOT FOUND'}")

# Ensure primary user has an organization
if primary_user:
    if not primary_user.get('organization_id'):
        print("\nPrimary user has NO organization. Creating one...")
        org_name = f"{primary_user.get('full_name', 'Primary')}'s Organization"
        slug = f"primary-org-{int(datetime.now().timestamp())}"
        new_org = supabase.table('organizations').insert({
            'name': org_name,
            'slug': slug,
            'owner_id': primary_user['id']
        }).execute()
        primary_org_id = new_org.data[0]['id']
        supabase.table('profiles').update({'organization_id': primary_org_id}).eq('id', primary_user['id']).execute()
        print(f"Created organization {primary_org_id} for primary user.")
    else:
        primary_org_id = primary_user['organization_id']
        print(f"\nPrimary user already has org: {primary_org_id}")

# Ensure secondary user has their OWN organization (different from primary!)
if secondary_user:
    if not secondary_user.get('organization_id') or secondary_user.get('organization_id') == primary_org_id:
        print("\nSecondary user needs their OWN organization. Creating one...")
        org_name = f"{secondary_user.get('full_name', 'Secondary')}'s Organization"
        slug = f"secondary-org-{int(datetime.now().timestamp())}"
        new_org = supabase.table('organizations').insert({
            'name': org_name,
            'slug': slug,
            'owner_id': secondary_user['id']
        }).execute()
        secondary_org_id = new_org.data[0]['id']
        supabase.table('profiles').update({'organization_id': secondary_org_id}).eq('id', secondary_user['id']).execute()
        print(f"Created organization {secondary_org_id} for secondary user.")
    else:
        secondary_org_id = secondary_user['organization_id']
        print(f"\nSecondary user already has org: {secondary_org_id}")

# Assign ALL campaigns to PRIMARY user's org (the one with the audit)
if primary_user and primary_org_id:
    print(f"\nAssigning ALL campaigns to PRIMARY org: {primary_org_id}")
    result = supabase.table('campaigns').update({'organization_id': primary_org_id}).neq('organization_id', primary_org_id).execute()
    # Also handle NULL cases explicitly
    null_result = supabase.table('campaigns').update({'organization_id': primary_org_id}).is_('organization_id', 'null').execute()
    print(f"Updated {len(result.data) + len(null_result.data)} campaigns.")

print("\n" + "=" * 60)
print("STEP 3: VERIFICATION")
print("=" * 60)

# Re-fetch and verify
profiles_after = supabase.table('profiles').select('id, email, organization_id').execute()
print("\nProfiles after fix:")
for p in profiles_after.data:
    print(f"  - {p.get('email')} -> OrgID: {p.get('organization_id')}")

campaigns_after = supabase.table('campaigns').select('id, domain, organization_id').execute()
print("\nCampaigns after fix:")
for c in campaigns_after.data:
    print(f"  - {c.get('domain')} -> OrgID: {c.get('organization_id')}")

print("\n" + "=" * 60)
print("DONE. Primary user should now see their data.")
print("Secondary user should see an EMPTY dashboard.")
print("=" * 60)
