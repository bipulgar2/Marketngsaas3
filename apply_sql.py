import os
from supabase import create_client

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_KEY")

if not url or not key:
    print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    exit(1)

supabase = create_client(url, key)

with open("migrations/serp_history.sql", "r") as f:
    sql = f.read()

# Supabase REST API doesn't allow executing arbitrary SQL directly through the python client
# Wait, this is why the user usually runs it in the SQL editor.
print("Manual SQL execution required.")
