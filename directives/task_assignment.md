# Directive: Task Assignment

## Goal
Convert audit findings into actionable tasks and assign them to the appropriate roles.

## Inputs
- **Audit Results**: JSON from DataForSEO audit
- **Campaign ID**: Which campaign the tasks belong to
- **Assignment Mode**: Auto (by role) or Manual (Campaign Manager assigns)

## Task Types → Roles Mapping

| Issue Category | Task Type | Assigned Role |
|----------------|-----------|---------------|
| Missing titles, descriptions | technical | optimization_specialist |
| Slow load times | technical | optimization_specialist |
| Missing H1, heading structure | technical | optimization_specialist |
| Broken links | technical | optimization_specialist |
| Redirect chains | technical | optimization_specialist |
| Low content pages | content | content_creator |
| Missing schema markup | technical | optimization_specialist |
| Keyword gaps | content | content_strategist |
| Link opportunities | link_building | link_builder |

## Process

### Step 1: Group Issues
```python
issues_by_type = {
    'missing_title': [],
    'missing_description': [],
    'slow_pages': [],
    'low_content': [],
    # etc.
}

for page in audit_pages:
    if page['issues']['no_title']:
        issues_by_type['missing_title'].append(page['url'])
    # etc.
```

### Step 2: Create Tasks
For each group with > 0 issues:
```python
supabase.table('tasks').insert({
    'campaign_id': campaign_id,
    'type': TYPE_MAP[issue_type],
    'title': f"Fix {len(urls)} pages with {issue_label}",
    'description': DESCRIPTION_TEMPLATES[issue_type],
    'checklist': [{'item': url, 'completed': False} for url in urls],
    'assigned_role': ROLE_MAP[issue_type],
    'status': 'pending',
    'priority': PRIORITY_MAP[issue_type]
}).execute()
```

### Step 3: Notify Campaign Manager
Log to activity_log that tasks were created.

## Priority Levels
- **3 (Urgent)**: 5xx errors, broken critical pages
- **2 (High)**: Missing titles, slow homepage
- **1 (Medium)**: Missing descriptions, heading issues
- **0 (Low)**: Meta keyword issues, minor improvements

## Edge Cases
- **No issues found**: Create a single "All Clear" task for review
- **Duplicate tasks**: Check if task with same title exists before creating
- **Large batches**: Split into multiple tasks if > 50 items per task

## SOPs for Each Task Type
Each task should include inline SOPs. Example for missing title:
```
## How to Fix Missing Title Tags

1. Open the URL in browser
2. Check if it's a real page or redirect
3. Write a title under 60 characters
4. Include primary keyword
5. Make it compelling for clicks
6. Update in CMS or code
7. Mark as complete in checklist
```
