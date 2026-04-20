-- Add tracked_keywords to campaigns
ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS tracked_keywords JSONB DEFAULT '[]'::jsonb;
