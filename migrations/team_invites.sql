-- Migration: RBAC and Team Invitations
-- Run this on your Supabase project (kalbykwfjtirrotzphcx)
-- Go to: https://supabase.com/dashboard/project/kalbykwfjtirrotzphcx/sql/new

-- 1. Add assigned_campaigns column to profiles
ALTER TABLE profiles 
ADD COLUMN IF NOT EXISTS assigned_campaigns JSONB DEFAULT '[]'::jsonb;

-- 2. Create invitations table
CREATE TABLE IF NOT EXISTS invitations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    token UUID DEFAULT gen_random_uuid() UNIQUE,
    email TEXT NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    assigned_campaigns JSONB DEFAULT '[]'::jsonb,
    used BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- 3. Index for fast token lookups
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);

-- 4. Enable RLS
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;

-- 5. Policy: allow all operations (the Flask app uses the service key)
CREATE POLICY "Allow all operations" ON invitations FOR ALL USING (true) WITH CHECK (true);
