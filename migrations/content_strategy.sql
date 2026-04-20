-- Add brand_config JSONB column to campaigns table
-- Stores: brand voice, USP, target audience, funnel stages, sales journey, content guidelines
ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS brand_config JSONB DEFAULT '{}'::jsonb;

-- Keyword research results storage (for Phase 2)
CREATE TABLE IF NOT EXISTS keyword_research (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    search_volume INT DEFAULT 0,
    keyword_difficulty DECIMAL DEFAULT 0,
    cpc DECIMAL DEFAULT 0,
    competition DECIMAL DEFAULT 0,
    search_intent TEXT,
    serp_features JSONB DEFAULT '[]'::jsonb,
    cluster_id UUID,
    opportunity_score DECIMAL DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Topic clusters (for Phase 4)
CREATE TABLE IF NOT EXISTS topic_clusters (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    cluster_name TEXT NOT NULL,
    pillar_keyword TEXT,
    pillar_page_id UUID,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Content calendar entries (for Phase 6)
CREATE TABLE IF NOT EXISTS content_calendar (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    page_id UUID,
    cluster_id UUID,
    title TEXT,
    target_keyword TEXT,
    funnel_stage TEXT,
    scheduled_date DATE,
    assigned_to UUID,
    status TEXT DEFAULT 'planned',
    brief JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
