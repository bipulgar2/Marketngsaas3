-- Migration: SOP Library (Collaborative, Org-Scoped)
-- Creates the sops and sop_notes tables for the living SOP knowledge base

-- 1. Core SOP entries
CREATE TABLE IF NOT EXISTS public.sops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,

    -- Matching key (maps to task titles like "Fix Missing Page Titles")
    issue_key TEXT NOT NULL,

    -- Display
    title TEXT NOT NULL,
    issue_description TEXT NOT NULL,
    category TEXT DEFAULT 'onpage',          -- onpage, technical, content, links, performance, reporting, strategy
    difficulty TEXT DEFAULT 'easy',           -- easy, medium, hard
    estimated_minutes INT DEFAULT 5,
    tools_recommended TEXT[] DEFAULT '{}',    -- e.g. {'Screaming Frog', 'Ahrefs'}
    video_url TEXT,                           -- optional tutorial video

    -- Rich steps (ordered array of step objects)
    -- Each step: { order, text, why, tool_link, pro_tip, common_mistake }
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Quality checks (array of strings)
    quality_checks JSONB DEFAULT '[]'::jsonb,

    -- Role association
    assigned_role TEXT,                       -- e.g. 'Optimization Specialist'

    -- Authoring
    created_by UUID,
    updated_by UUID,
    is_default BOOLEAN DEFAULT false,         -- true = seeded from system defaults
    is_archived BOOLEAN DEFAULT false,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE(organization_id, issue_key)
);

-- 2. Community notes / experiences
CREATE TABLE IF NOT EXISTS public.sop_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sop_id UUID NOT NULL REFERENCES public.sops(id) ON DELETE CASCADE,

    author_id UUID,
    author_name TEXT,

    content TEXT NOT NULL,
    note_type TEXT DEFAULT 'tip',  -- tip, warning, workaround, experience
    upvotes INT DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Indexes
CREATE INDEX IF NOT EXISTS idx_sops_org_key ON public.sops(organization_id, issue_key);
CREATE INDEX IF NOT EXISTS idx_sops_org ON public.sops(organization_id);
CREATE INDEX IF NOT EXISTS idx_sop_notes_sop ON public.sop_notes(sop_id);

-- 4. Enable RLS
ALTER TABLE public.sops ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sop_notes ENABLE ROW LEVEL SECURITY;

-- 5. Policies (using service key from Flask, so allow all — API handles auth)
CREATE POLICY "Allow all sops operations" ON public.sops FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all sop_notes operations" ON public.sop_notes FOR ALL USING (true) WITH CHECK (true);
