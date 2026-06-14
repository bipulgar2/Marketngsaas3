-- Create serp_history table for tracking exact keyword rankings over time
CREATE TABLE IF NOT EXISTS serp_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    rank_absolute INTEGER,
    url TEXT,
    search_volume INTEGER,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add indexes for fast fetching of latest ranks per campaign and keyword
CREATE INDEX IF NOT EXISTS idx_serp_history_campaign_keyword_date 
ON serp_history(campaign_id, keyword, fetched_at DESC);

-- Allow RLS but let authenticated users access it
ALTER TABLE serp_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view serp_history for their campaigns" ON serp_history
    FOR SELECT USING (
        campaign_id IN (
            SELECT id FROM campaigns WHERE organization_id = (
                SELECT organization_id FROM profiles WHERE id = auth.uid()
            )
        )
    );

CREATE POLICY "Service role has full access to serp_history" ON serp_history
    USING (true);
