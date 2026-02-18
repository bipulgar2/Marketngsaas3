# Directive: Smart Task Assignment System

## Goal
Automatically convert raw audit findings (from DataForSEO) into actionable, role-assigned tasks in the database. This is the "Hook" that turns data into work.

## 1. Input Data Structure
Source: `api.dataforseo_client.get_page_issues()`
The `pages` list contains items with an `issues` dictionary:

```python
"issues": {
    "no_title": bool,
    "no_description": bool,
    "no_h1": bool,
    "title_too_long": bool,
    "description_too_long": bool,
    "slow_load": bool,          # > 3000ms
    "low_content": bool,        # < 300 words
    "broken_links": bool,
    "duplicate_title": bool,
    "duplicate_content": bool,
    "no_canonical": bool,
    "is_4xx": bool,
    "is_5xx": bool
}
```

## 2. Issue → Role Mapping Matrix

| Issue Flag | Task Category | Assigned Role | Priority | Title Template |
|------------|---------------|---------------|----------|----------------|
| `is_5xx`, `is_4xx` | Technical | Optimization Specialist | **Urgent (3)** | Fix Server Errors on {n} Pages |
| `slow_load` | Technical | Optimization Specialist | High (2) | Improve Page Speed on {n} Pages |
| `no_title`, `title_too_long` | On-Page | Optimization Specialist | High (2) | Optimize Title Tags on {n} Pages |
| `no_description`, `description_too_long` | On-Page | Optimization Specialist | Medium (1) | Optimize Meta Descriptions on {n} Pages |
| `no_h1`, `duplicate_h1` | On-Page | Content Creator | Medium (1) | Fix Heading Structure on {n} Pages |
| `low_content` | Content | Content Creator | Medium (1) | Expand Thin Content on {n} Pages |
| `duplicate_content` | Content | Content Strategist | High (2) | Resolve Duplicate Content on {n} Pages |
| `broken_links` | Technical | Link Builder | Low (0) | Fix Broken Internal Links on {n} Pages |
| `no_canonical` | Technical | Optimization Specialist | Medium (1) | Add Canonical Tags to {n} Pages |

## 3. Implementation Logic

### A. Grouping Strategy
*Do not* create one task per page. Group by **Issue Type**.
1. Iterate through all `pages`.
2. For each page, check all `issue` flags.
3. Add the page URL to the corresponding `issue_bucket`.

```python
buckets = {
    'no_title': [],
    'slow_load': [],
    # ...
}

for page in pages:
    issues = page['issues']
    if issues['no_title']:
        buckets['no_title'].append(page['url'])
    # ...
```

### B. Task Creation (Batching)
For each bucket with items:
1. **Check Existing**: Query DB to see if an *open* task (status != 'completed') already exists for this `campaign_id` and `issue_type`.
    - *If yes*: Update the existing task (append new URLs to checklist).
    - *If no*: Create a new task.
2. **Batch Limit**: If a bucket has > 50 URLs, split into multiple tasks (e.g., "Fix Title Tags (Batch 1)", "Fix Title Tags (Batch 2)").

### C. Database Record
Review the `tasks` table schema usage:
- `checklist`: JSON array of objects `{'item': url, 'completed': False, 'details': 'Current length: 0'}`
- `type`: Maps to 'technical', 'content', 'link_building'
- `description`: Auto-injected SOP (see below)

## 4. Standard Operating Procedures (SOPs)
These text blocks will be injected into the `description` field.

**SOP: Server Errors (4xx/5xx)**
> 1. Check if the page is truly gone or just moved.
> 2. If moved, set up a 301 redirect.
> 3. If gone, ensure 404 is returned correctly and remove internal links to it.
> 4. For 5xx, check server logs for timeout or crash reasons.

**SOP: Title Tags**
> 1. Write unique title under 60 characters.
> 2. Include primary keyword near the front.
> 3. Match search intent of the page.
> 4. Ensure no duplicates across site.

**SOP: Thin Content**
> 1. Review page purpose. Is it necessary?
> 2. If yes, expand to at least 500-800 words of comprehensive value.
> 3. Use "People Also Ask" for sub-topic ideas.
> 4. If no, consider 301 redirecting to a relevant parent page or adding 'noindex'.

## 5. Execution Plan
1. **Modify `api/utils.py`**:
    - Update `create_tasks_from_audit(pages, campaign_id, client)` function.
    - Implement the Bucket → Check → Insert logic.
2. **Frontend Update**:
    - Ensure Task Detail view renders the `checklist` as actionable items.
    - (Already ready in `dashboard.html`).

## 6. Edge Cases
- **Re-Audits**: When an audit runs again, it might find the same issues.
    - *Strategy*: If a task is 'pending' or 'in_progress', do not create a duplicate. Update the existing one. If 'completed', create a new one (regression).
- **False Positives**: User can manually delete tasks.

