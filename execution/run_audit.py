#!/usr/bin/env python3
"""
Execution Script: Run Technical Audit

Usage:
    python execution/run_audit.py --domain example.com --pages 200 --campaign-id <uuid>

This script:
1. Starts a DataForSEO on-page audit
2. Polls for completion
3. Fetches results
4. Creates tasks from findings
5. Saves to Supabase
"""
import os
import sys
import time
import argparse
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from api.dataforseo_client import (
    start_onpage_audit,
    get_audit_status,
    get_audit_summary,
    get_page_issues
)
from supabase import create_client

# Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')  # Use service role for backend
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


def log(msg: str):
    """Print with timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def run_audit(domain: str, max_pages: int = 200, campaign_id: str = None) -> dict:
    """
    Run full audit workflow.
    
    Args:
        domain: Website domain
        max_pages: Pages to crawl
        campaign_id: Supabase campaign ID
    
    Returns:
        Dict with audit results and created tasks
    """
    log(f"Starting audit for {domain} ({max_pages} pages)")
    
    # Step 1: Start audit
    result = start_onpage_audit(domain, max_pages)
    if not result.get('success'):
        log(f"❌ Failed to start audit: {result.get('error')}")
        return {'success': False, 'error': result.get('error')}
    
    task_id = result['task_id']
    log(f"✓ Audit started. Task ID: {task_id}")
    log(f"  Cost: ${result.get('cost', 0):.4f}")
    
    # Step 2: Poll for completion
    log("Waiting for crawl to complete...")
    max_attempts = 60  # 30 minutes max
    for attempt in range(max_attempts):
        status = get_audit_status(task_id)
        if status.get('ready'):
            log("✓ Crawl complete!")
            break
        log(f"  Still crawling... (attempt {attempt + 1}/{max_attempts})")
        time.sleep(30)
    else:
        log("❌ Timeout waiting for audit")
        return {'success': False, 'error': 'Audit timeout'}
    
    # Step 3: Fetch results
    log("Fetching audit summary...")
    summary = get_audit_summary(task_id)
    if not summary.get('success'):
        log(f"❌ Failed to get summary: {summary.get('error')}")
        return {'success': False, 'error': summary.get('error')}
    
    log("Fetching page issues...")
    pages_result = get_page_issues(task_id, limit=100)
    if not pages_result.get('success'):
        log(f"❌ Failed to get pages: {pages_result.get('error')}")
        return {'success': False, 'error': pages_result.get('error')}
    
    pages = pages_result.get('pages', [])
    log(f"✓ Got {len(pages)} pages")
    
    # Step 4: Create tasks from findings
    tasks_created = []
    if campaign_id and supabase:
        tasks_created = create_tasks_from_audit(pages, campaign_id)
        log(f"✓ Created {len(tasks_created)} tasks")
    
    # Step 5: Save audit to Supabase
    audit_record = None
    if campaign_id and supabase:
        audit_record = supabase.table('audits').insert({
            'campaign_id': campaign_id,
            'type': 'technical',
            'status': 'completed',
            'results': {
                'summary': summary.get('summary', {}),
                'pages': pages
            },
            'summary': summary.get('summary', {}),
            'dataforseo_task_id': task_id
        }).execute()
        log(f"✓ Saved audit to database")
    
    return {
        'success': True,
        'task_id': task_id,
        'summary': summary.get('summary', {}),
        'pages_count': len(pages),
        'tasks_created': len(tasks_created)
    }


def create_tasks_from_audit(pages: list, campaign_id: str) -> list:
    """
    Create task records from audit findings.
    
    Groups issues by type and creates one task per type.
    """
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
        
        result = supabase.table('tasks').insert(task_data).execute()
        if result.data:
            created.append(result.data[0])
    
    return created


def main():
    parser = argparse.ArgumentParser(description='Run technical SEO audit')
    parser.add_argument('--domain', required=True, help='Domain to audit')
    parser.add_argument('--pages', type=int, default=200, help='Max pages to crawl')
    parser.add_argument('--campaign-id', help='Supabase campaign ID')
    parser.add_argument('--dry-run', action='store_true', help='Don\'t save to database')
    
    args = parser.parse_args()
    
    result = run_audit(
        domain=args.domain,
        max_pages=args.pages,
        campaign_id=args.campaign_id if not args.dry_run else None
    )
    
    print("\n" + "=" * 50)
    print("AUDIT RESULT")
    print("=" * 50)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
