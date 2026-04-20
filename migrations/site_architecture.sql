-- Add site_architecture JSONB column to campaigns table
-- Stores the planned site hierarchy tree

ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS site_architecture JSONB DEFAULT NULL;

-- Architecture is a tree of nodes:
-- {
--   "nodes": [
--     {
--       "id": "uuid",
--       "name": "Homepage",
--       "type": "page",        -- "folder" or "page"
--       "slug": "/",
--       "keyword": "brand name",
--       "pr": 100,              -- PageRank priority weight
--       "parent_id": null,      -- null = root
--       "order": 0,
--       "pushed_to_content": false
--     },
--     ...
--   ],
--   "business_type": "saas",
--   "generated_at": "2026-04-21T00:00:00Z",
--   "meta": {
--     "total_nodes": 30,
--     "max_depth": 3
--   }
-- }
