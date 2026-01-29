from supabase import create_client
import os

def create_tasks_from_audit(pages: list, campaign_id: str, supabase_client) -> list:
    """
    Create task records from audit findings.
    Groups issues by type and creates one task per type.
    """
    if not pages:
        return []

    # Group pages by issue
    issues = {
        'missing_title': [],
        'missing_description': [],
        'missing_h1': [],
        'slow_pages': [],
        'low_content': [],
        'broken_pages': [],
        'no_canonical': []
    }
    
    for page in pages:
        page_issues = page.get('issues', {})
        url = page.get('url', '')
        
        if page_issues.get('no_title'):
            issues['missing_title'].append(url)
        if page_issues.get('no_description'):
            issues['missing_description'].append(url)
        if page_issues.get('no_h1'):
            issues['missing_h1'].append(url)
        if page_issues.get('slow_load'):
            issues['slow_pages'].append(url)
        if page_issues.get('low_content'):
            issues['low_content'].append(url)
        if page_issues.get('is_broken') or page_issues.get('is_4xx') or page_issues.get('is_5xx'):
            issues['broken_pages'].append(url)
        if page_issues.get('no_canonical'):
            issues['no_canonical'].append(url)
    
    # Task templates
    templates = {
        'missing_title': {
            'title': 'Fix pages with missing title tags',
            'description': 'These pages have no title tag, which hurts SEO and CTR.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 2
        },
        'missing_description': {
            'title': 'Add meta descriptions',
            'description': 'These pages have no meta description.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 1
        },
        'missing_h1': {
            'title': 'Add H1 headings',
            'description': 'These pages have no H1 tag.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 1
        },
        'slow_pages': {
            'title': 'Improve slow loading pages',
            'description': 'These pages take > 3 seconds to load.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 2
        },
        'low_content': {
            'title': 'Expand thin content pages',
            'description': 'These pages have < 300 words.',
            'type': 'content',
            'role': 'content_creator',
            'priority': 1
        },
        'broken_pages': {
            'title': 'Fix broken pages (4xx/5xx errors)',
            'description': 'These pages return error status codes.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 3
        },
        'no_canonical': {
            'title': 'Add canonical tags',
            'description': 'These pages have no canonical URL specified.',
            'type': 'technical',
            'role': 'optimization_specialist',
            'priority': 1
        }
    }
    
    created = []
    for issue_type, urls in issues.items():
        if not urls:
            continue
        
        template = templates.get(issue_type, {})
        task_data = {
            'campaign_id': campaign_id,
            'type': template.get('type', 'technical'),
            'title': f"{template.get('title')} ({len(urls)} pages)",
            'description': template.get('description', ''),
            'checklist': [{'item': url, 'completed': False} for url in urls[:50]],  # Limit checklist
            'assigned_role': template.get('role', 'optimization_specialist'),
            'priority': template.get('priority', 0),
            'status': 'pending'
        }
        
        try:
            result = supabase_client.table('tasks').insert(task_data).execute()
            if result.data:
                created.append(result.data[0])
        except Exception as e:
            print(f"Error creating task for {issue_type}: {e}")
    
    return created
