-- Add organization_id to agency_integrations
ALTER TABLE agency_integrations 
ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE;

-- For existing integrations, we could attempt to backfill organization_id from profiles if needed,
-- but since this is presumably a fresh setup or we can just let users reconnect:
UPDATE agency_integrations ai
SET organization_id = p.organization_id
FROM profiles p
WHERE ai.user_id = p.id AND ai.organization_id IS NULL;

-- Remove the unique constraint on user_id + provider if it exists
ALTER TABLE agency_integrations DROP CONSTRAINT IF EXISTS agency_integrations_user_id_provider_key;

-- Add a unique constraint on organization_id + provider so an organization has one google connection
ALTER TABLE agency_integrations ADD CONSTRAINT agency_integrations_org_provider_key UNIQUE (organization_id, provider);
