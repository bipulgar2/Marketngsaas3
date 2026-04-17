-- Migration: Create site_audits table for Site Audit (Global) feature
-- Run this on your Supabase project (kalbykwfjtirrotzphcx)

CREATE TABLE IF NOT EXISTS site_audits (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    domain TEXT NOT NULL,
    max_pages INTEGER DEFAULT 50,
    task_id TEXT,
    status TEXT DEFAULT 'crawling',
    audit_data JSONB DEFAULT '{}',
    slides_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups by domain and status
CREATE INDEX IF NOT EXISTS idx_site_audits_domain ON site_audits(domain);
CREATE INDEX IF NOT EXISTS idx_site_audits_status ON site_audits(status);
CREATE INDEX IF NOT EXISTS idx_site_audits_created_at ON site_audits(created_at DESC);

-- Enable RLS (but allow all for now since the app uses service key)
ALTER TABLE site_audits ENABLE ROW LEVEL SECURITY;

-- Policy: allow all operations (the Flask app uses the service key)
CREATE POLICY "Allow all operations" ON site_audits FOR ALL USING (true) WITH CHECK (true);
