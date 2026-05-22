# Directive: Technical Audit

## Goal
Run a comprehensive technical SEO audit on a client's website and generate actionable task checklists + Google Slides presentation.

## Existing Code Reference
The audit flows are already built in **audit-app**. We've copied the key files:
- `api/deep_audit_slides.py` — Full slides generation (96KB)
- `api/dataforseo_client.py` — DataForSEO API wrapper (66KB)
- `execution/screenshot_capture.py` — Playwright screenshots
- `execution/pagespeed_insights.py` — Core Web Vitals

## Process Flow (From audit-app)

### 1. Create Audit
`POST /api/audit/create` with `{domain, max_pages}`
- Calls `start_onpage_audit()` from dataforseo_client
- Saves task_id to Supabase audits table
- Returns task_id for polling

### 2. Poll Status
`GET /api/audit/status/<task_id>`
- Calls `get_audit_status()` from dataforseo_client
- Returns `{ready: true/false}`

### 3. Save Results (when ready)
`POST /api/audit/save-results` with `{audit_id, task_id}`
- Calls `get_audit_summary()` and `get_page_issues()`
- Saves full results to Supabase
- Returns page count and summary

### 4. Generate Slides
`POST /api/slides/generate` with `{audit_id}`
- Uses `deep_audit_slides.py`
- Creates Google Slides presentation
- Returns slides URL

### 5. Create Tasks (NEW - to build)
From audit results, create tasks grouped by issue type.

## Key Functions in dataforseo_client.py

| Function | Purpose |
|----------|---------|
| `start_onpage_audit(domain, max_pages)` | Start crawl |
| `get_audit_status(task_id)` | Check if done |
| `get_audit_summary(task_id)` | Get overall stats |
| `get_page_issues(task_id, limit)` | Get page-level issues |
| `get_lighthouse_audit(url)` | Core Web Vitals |
| `get_links_data(task_id)` | Broken links analysis |
| `get_backlinks_summary(domain)` | Backlink profile |

## Integration with Task System

After audit completes, parse `pages[].issues` to create tasks:

```python
# Issue types to task mapping
ISSUE_TO_TASK = {
    'no_title': ('technical', 'optimization_specialist', 2),
    'no_description': ('technical', 'optimization_specialist', 1),
    'no_h1': ('technical', 'optimization_specialist', 1),
    'slow_load': ('technical', 'optimization_specialist', 2),
    'low_content': ('content', 'content_creator', 1),
    'is_broken': ('technical', 'optimization_specialist', 3),
}
```

## Edge Cases
- **Large sites**: Limit to 200 pages
- **Slow crawls**: May take 15+ minutes
- **Screenshots fail**: Fall back to DataForSEO screenshots
- **Slides OAuth**: Requires valid Google credentials
- **DataForSEO transient failures**: `save-audit-results` now retries core API calls (summary, pages) 3x with 5s/10s backoff
- **Slides show 0 traffic/keywords**: Root cause was missing `domain_rank` in `projects.full_audit_data`. Fixed: now fetched via `get_domain_rank_overview()` during audit creation.
- **Slides fallback indentation bug**: The `audits` table fallback in `generate_deep_audit_slides_endpoint` had misaligned indentation causing it to run outside the `else:` scope. Fixed.
- **Data flow for slides**: Three paths to slides data (priority order): (1) `projects.full_audit_data`, (2) `site_audits.audit_data`, (3) `audits.results`. All three must have traffic/keywords/backlinks.

## Cost
- On-page audit: ~$0.02-0.10 per audit
- Lighthouse: ~$0.01 per URL
- Screenshots: Free (Playwright) or ~$0.002 (DataForSEO)
- Domain rank overview: ~$0.005 per call (added during audit creation)

## Phase 2 Learnings (Competitor Analysis)

### Location Code Flow
- `fetch_domain_metrics(domain, location_code=2840)` now accepts a `location_code` parameter (default=US). Previously hardcoded to 2356 (India), which caused all traffic/keyword totals to report India-specific data.
- `create_audit()` resolves location from `campaign.settings.location` via `location_code_for()`. For competitor audits, it prefers `competitor_country` from the POST body.
- `save-audit-results` resolves `campaign_location` from `campaigns.settings` and passes it to `fetch_domain_metrics`.
- `/api/client/stats` live fallback uses `resolved_location` from campaign settings.

### Competitor Country Threading
- Frontend (`dashboard.html`): `window.lastCompetitorCountry` captured from `#competitorCountryInput`, sent as `competitor_country` in `POST /api/audits` body.
- Backend (`create_audit`): When `audit_type == 'competitor'` and `competitor_country` is provided, overrides the campaign's default location.
- Gap analysis already correctly separates `client_location` and `competitor_location` via query params.

### Campaign Query Pattern
- Always select `'domain, settings'` from campaigns (not just `'domain'`) when you need location resolution. Previous bug: `create_audit` only selected `domain`, so `settings.location` was always `None`.
