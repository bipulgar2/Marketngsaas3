# Source Code Reference

This new Agency Platform builds on existing codebases. Use these as reference for patterns, modules, and functionality.

## Primary Source Repos

### 1. seo-system-main (reliable - git)
**Path:** `/Users/bipul/Downloads/ALL WORKSPACES/seo-system-main (reliable - git)/`

**Contains:**
- `api/index.py` — Main Flask API (295KB, comprehensive)
- `api/dataforseo_client.py` — DataForSEO wrapper
- `api/deep_audit_slides.py` — Slides generation
- `public/agency.html` — Full AgencyOS UI (5600+ lines)
- Content generation (MoFu/ToFu)
- Keyword research
- Competitor analysis
- Webflow publishing

**Use for:**
- UI design patterns (dark theme, Tailwind)
- Content generation logic
- Competitor analysis endpoints
- Keyword/topic research

---

### 2. audit-app
**Path:** `/Users/bipul/Downloads/ALL WORKSPACES/audit-app/`

**Contains:**
- `api/index.py` — Standalone audit API
- `api/deep_audit_slides.py` — Slides (cleaner version)
- `execution/screenshot_capture.py` — Playwright screenshots
- `execution/pagespeed_insights.py` — Core Web Vitals
- `public/audit-dashboard.html` — Audit UI

**Use for:**
- Audit flow (create → poll → save → slides)
- Screenshot capture
- PageSpeed analysis
- Readability analysis

---

## Already Copied to Agency Platform

| File | Source | Purpose |
|------|--------|---------|
| `api/dataforseo_client.py` | seo-system-main | DataForSEO API |
| `api/deep_audit_slides.py` | audit-app | Slides generation |
| `api/google_auth.py` | seo-system-main | OAuth helpers |
| `api/drive_utils.py` | seo-system-main | Drive file sharing |
| `execution/screenshot_capture.py` | audit-app | Playwright |
| `execution/pagespeed_insights.py` | audit-app | Core Web Vitals |

---

## Key Patterns to Follow

### From agency.html (seo-system-main)
- Tailwind dark theme config
- Glass morphism cards (`.glass` class)
- Top navigation with tabs
- Left sidebar for sub-navigation
- Table styling with hover states
- Modal patterns

### From audit-app
- Supabase integration pattern
- Async audit polling
- Slides generation with Google API
- Error handling patterns
