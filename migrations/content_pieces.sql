-- Content Pieces — full content lifecycle management
-- Tracks articles from brief → draft → review → published

CREATE TABLE IF NOT EXISTS content_pieces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    
    -- Content identity
    title TEXT NOT NULL,
    slug TEXT,
    target_keyword TEXT,
    secondary_keywords JSONB DEFAULT '[]'::jsonb,
    funnel_stage TEXT DEFAULT 'tofu',  -- tofu, mofu, bofu
    content_type TEXT DEFAULT 'blog_post',  -- blog_post, landing_page, product_page, guest_post, pillar
    
    -- Content body
    brief JSONB DEFAULT '{}'::jsonb,  -- AI-generated content brief
    outline JSONB DEFAULT '[]'::jsonb,  -- section structure
    draft_html TEXT,  -- the actual article content
    word_count INTEGER DEFAULT 0,
    
    -- SEO metadata
    meta_title TEXT,
    meta_description TEXT,
    schema_markup JSONB,
    internal_links JSONB DEFAULT '[]'::jsonb,
    
    -- Workflow status
    status TEXT DEFAULT 'brief',  -- brief, outline, draft, review, revision, approved, published
    assigned_to UUID,  -- team member working on it
    assigned_by UUID,
    reviewer_notes TEXT,
    revision_count INTEGER DEFAULT 0,
    
    -- Publishing
    published_url TEXT,
    published_at TIMESTAMPTZ,
    publish_platform TEXT,  -- wordpress, webflow, manual
    
    -- Relations
    cluster_id UUID,
    calendar_entry_id UUID,
    source_brief_id UUID,  -- links back to the AI brief that spawned this
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_content_pieces_campaign ON content_pieces(campaign_id);
CREATE INDEX IF NOT EXISTS idx_content_pieces_status ON content_pieces(status);
CREATE INDEX IF NOT EXISTS idx_content_pieces_assigned ON content_pieces(assigned_to);
