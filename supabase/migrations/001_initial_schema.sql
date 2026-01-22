-- ============================================================================
-- SEO Agency Platform - Initial Schema
-- Run this in Supabase SQL Editor
-- ============================================================================

-- Enable necessary extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. ORGANIZATIONS (Multi-tenant root)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    owner_id UUID,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 2. PROFILES (Extends Supabase auth.users)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'viewer',
    organization_id UUID REFERENCES public.organizations(id) ON DELETE SET NULL,
    avatar_url TEXT,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Valid roles: admin, campaign_manager, content_strategist, content_creator, 
-- optimization_specialist, link_builder, reporting_manager, viewer

-- ============================================================================
-- 3. CAMPAIGNS (Clients being managed)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    settings JSONB DEFAULT '{}',  -- GSC keys, GA4 keys, etc.
    status TEXT DEFAULT 'active', -- active, paused, archived
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 4. TASKS (Work items from audits, manual creation)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES public.campaigns(id) ON DELETE CASCADE,
    type TEXT NOT NULL,              -- technical, content, link_building, optimization
    title TEXT NOT NULL,
    description TEXT,
    checklist JSONB DEFAULT '[]',    -- Array of {item, completed, notes}
    assigned_to UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    assigned_role TEXT,              -- Which role sees this task
    status TEXT DEFAULT 'pending',   -- pending, in_progress, review, done
    priority INTEGER DEFAULT 0,      -- 0=low, 1=medium, 2=high, 3=urgent
    due_date DATE,
    sop_content TEXT,                -- AI-generated SOP for this task
    created_by UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 5. AUDITS (Technical audit results)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.audits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES public.campaigns(id) ON DELETE CASCADE,
    type TEXT NOT NULL,              -- technical, content, backlink, competitor
    status TEXT DEFAULT 'pending',   -- pending, running, completed, failed
    results JSONB DEFAULT '{}',      -- Full audit data
    summary JSONB DEFAULT '{}',      -- Quick stats
    slides_url TEXT,                 -- Google Slides URL
    dataforseo_task_id TEXT,         -- For tracking async audits
    created_by UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- ============================================================================
-- 6. KEYWORDS (Keyword tracking)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.keywords (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES public.campaigns(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    search_volume INTEGER,
    difficulty INTEGER,
    current_rank INTEGER,
    previous_rank INTEGER,
    intent TEXT,                     -- informational, commercial, transactional
    cluster_id UUID,                 -- For topic clustering
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 7. CONTENT (Content pieces - briefs, drafts, published)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.content (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES public.campaigns(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    slug TEXT,
    content_type TEXT DEFAULT 'blog', -- blog, landing, guest_post
    status TEXT DEFAULT 'brief',      -- brief, draft, review, published
    brief JSONB DEFAULT '{}',         -- AI-generated brief
    draft TEXT,                       -- Article content
    target_keyword TEXT,
    target_keywords JSONB DEFAULT '[]',
    word_count INTEGER,
    published_url TEXT,
    assigned_to UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

-- ============================================================================
-- 8. ACTIVITY LOG (For tracking what happened)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.activity_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES public.organizations(id),
    campaign_id UUID REFERENCES public.campaigns(id),
    user_id UUID REFERENCES public.profiles(id),
    action TEXT NOT NULL,             -- created, updated, completed, assigned, etc.
    entity_type TEXT NOT NULL,        -- task, audit, content, campaign
    entity_id UUID,
    details JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.content ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_log ENABLE ROW LEVEL SECURITY;

-- Profiles: Users can read all profiles in their org, update own
CREATE POLICY "Users can view profiles in their org" ON public.profiles
    FOR SELECT USING (
        organization_id IN (
            SELECT organization_id FROM public.profiles WHERE id = auth.uid()
        )
        OR id = auth.uid()
    );

CREATE POLICY "Users can update own profile" ON public.profiles
    FOR UPDATE USING (id = auth.uid());

-- Organizations: Members can view their org
CREATE POLICY "Members can view their organization" ON public.organizations
    FOR SELECT USING (
        id IN (SELECT organization_id FROM public.profiles WHERE id = auth.uid())
    );

-- Campaigns: Users see their org's campaigns
CREATE POLICY "Users see their org campaigns" ON public.campaigns
    FOR SELECT USING (
        organization_id IN (
            SELECT organization_id FROM public.profiles WHERE id = auth.uid()
        )
    );

CREATE POLICY "Managers can create campaigns" ON public.campaigns
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager')
        )
    );

CREATE POLICY "Managers can update campaigns" ON public.campaigns
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager')
        )
    );

-- Tasks: See assigned or if manager
CREATE POLICY "Users see assigned tasks or all if manager" ON public.tasks
    FOR SELECT USING (
        assigned_to = auth.uid()
        OR EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager')
        )
    );

CREATE POLICY "Managers can create tasks" ON public.tasks
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager')
        )
    );

CREATE POLICY "Users can update assigned tasks" ON public.tasks
    FOR UPDATE USING (
        assigned_to = auth.uid()
        OR EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager')
        )
    );

-- Audits: Same as campaigns
CREATE POLICY "Users see their org audits" ON public.audits
    FOR SELECT USING (
        campaign_id IN (
            SELECT id FROM public.campaigns 
            WHERE organization_id IN (
                SELECT organization_id FROM public.profiles WHERE id = auth.uid()
            )
        )
    );

-- Keywords: Same as campaigns
CREATE POLICY "Users see their org keywords" ON public.keywords
    FOR SELECT USING (
        campaign_id IN (
            SELECT id FROM public.campaigns 
            WHERE organization_id IN (
                SELECT organization_id FROM public.profiles WHERE id = auth.uid()
            )
        )
    );

-- Content: Same as tasks
CREATE POLICY "Users see assigned content or all if manager" ON public.content
    FOR SELECT USING (
        assigned_to = auth.uid()
        OR EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'campaign_manager', 'content_strategist')
        )
    );

-- Activity log: Org members can view
CREATE POLICY "Users see their org activity" ON public.activity_log
    FOR SELECT USING (
        organization_id IN (
            SELECT organization_id FROM public.profiles WHERE id = auth.uid()
        )
    );

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_profiles_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_campaigns_updated_at
    BEFORE UPDATE ON public.campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_tasks_updated_at
    BEFORE UPDATE ON public.tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_keywords_updated_at
    BEFORE UPDATE ON public.keywords
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_content_updated_at
    BEFORE UPDATE ON public.content
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_profiles_organization ON public.profiles(organization_id);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON public.profiles(role);
CREATE INDEX IF NOT EXISTS idx_campaigns_organization ON public.campaigns(organization_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON public.campaigns(status);
CREATE INDEX IF NOT EXISTS idx_tasks_campaign ON public.tasks(campaign_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON public.tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON public.tasks(status);
CREATE INDEX IF NOT EXISTS idx_audits_campaign ON public.audits(campaign_id);
CREATE INDEX IF NOT EXISTS idx_keywords_campaign ON public.keywords(campaign_id);
CREATE INDEX IF NOT EXISTS idx_content_campaign ON public.content(campaign_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_campaign ON public.activity_log(campaign_id);

-- ============================================================================
-- INITIAL SETUP HELPER (Run after creating first user)
-- ============================================================================
-- To create first admin:
-- 1. Sign up user via Supabase Auth
-- 2. Run: INSERT INTO organizations (name, slug) VALUES ('Your Agency', 'your-agency');
-- 3. Run: UPDATE profiles SET role = 'admin', organization_id = 'org-uuid' WHERE email = 'your@email.com';
