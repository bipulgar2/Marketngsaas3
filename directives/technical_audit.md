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

## Cost
- On-page audit: ~$0.02-0.10 per audit
- Lighthouse: ~$0.01 per URL
- Screenshots: Free (Playwright) or ~$0.002 (DataForSEO)
