from supabase import create_client
import os

def create_tasks_from_audit(categorized_data: dict, campaign_id: str, supabase_client) -> list:
    """
    Create task records from categorized audit findings.
    Iterates through the structured Accessibility, Usability, and Architecture data.
    """
    if not categorized_data:
        return []

    # Task Templates Mapping
    # Maps internal issue keys to Task definitions
    TASK_TEMPLATES = {
        # --- Usability (Content & Meta) ---
        'title_missing': {
            'title': 'Fix Missing Page Titles',
            'description': 'Page titles are critical for ranking and CTR. Create unique, keyword-rich titles for these pages.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'high'
        },
        'title_duplicate': {
            'title': 'Rewrite Duplicate Page Titles',
            'description': 'Duplicate titles confuse search engines. Ensure every page has a unique title.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'medium'
        },
        'title_over_65': {
            'title': 'Shorten Page Titles (>65 chars)',
            'description': 'Titles over 65 characters get truncated in SERPs. Rewrite them to be concise.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'low'
        },
        'desc_missing': {
            'title': 'Add Meta Descriptions',
            'description': 'Meta descriptions improve click-through rates. Write compelling descriptions for these pages.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'high'
        },
        'desc_duplicate': {
            'title': 'Rewrite Duplicate Meta Descriptions',
            'description': 'Each page should have a unique description summary.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'medium'
        },
        'desc_over_155': {
            'title': 'Shorten Meta Descriptions (>155 chars)',
            'description': 'Descriptions over 155 characters are truncated. Keep them concise.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'low'
        },
        'h1_missing': {
            'title': 'Add H1 Tags',
            'description': 'The H1 tag is the main headline of the page. Ensure every page has exactly one H1.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'high'
        },
        'h1_multiple': {
            'title': 'Fix Multiple H1 Tags',
            'description': 'Pages should have only one H1 tag. Differentiate subheadings with H2-H6.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'medium'
        },
        'h2_missing': {
            'title': 'Add Subheadings (H2)',
            'description': 'Content structure is important. Break up long content with H2 subheadings.',
            'type': 'content', 'role': 'Content Creator', 'priority': 'medium'
        },
        'low_word_count': {
            'title': 'Expand Thin Content (<300 words)',
            'description': 'These pages have very little content. Expand them to add value or consolidate/redirect them.',
            'type': 'content', 'role': 'Content Creator', 'priority': 'medium'
        },
        'misspelling': {
            'title': 'Fix Grammar & Spelling Errors',
            'description': 'Spelling errors hurt credibility. Review and fix typos on these pages.',
            'type': 'content', 'role': 'Content Creator', 'priority': 'low'
        },
        'links_broken': {
            'title': 'Fix Broken Links (404s)',
            'description': 'These pages contain links to URLs that return errors (4xx/5xx). Remove or update the links.',
            'type': 'technical', 'role': 'Link Builder', 'priority': 'high'
        },
        'links_redirect_3xx': {
            'title': 'Audit Internal Redirects (3xx)',
            'description': 'Update internal links to point directly to the final destination, avoiding redirect chains.',
            'type': 'technical', 'role': 'Link Builder', 'priority': 'low'
        },
        'orphan_urls': {
            'title': 'Link Orphan Pages',
            'description': 'These pages have no internal links pointing to them. Add links from other relevant pages.',
            'type': 'usability', 'role': 'Link Builder', 'priority': 'medium'
        },

        # --- Accessibility (Speed & Experience) ---
        'mobile_friendly': {
            'title': 'Fix Mobile Friendliness Issues',
            'description': 'These pages failed the mobile-friendly check. Verify viewport settings and responsive design.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'high'
        },
        'image_alt_missing': {
            'title': 'Add Alt Text to Images',
            'description': 'Alt text helps accessibility and Image SEO. Describe the images.',
            'type': 'onpage', 'role': 'Content Creator', 'priority': 'medium'
        },
        'images_large': {
            'title': 'Compress Large Images (>100KB)',
            'description': 'These images are too large and slow down the page. Compress or resize them.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'medium'
        },
        'core_web_vitals': {
            'title': 'Optimize Core Web Vitals (LCP/CLS)',
            'description': 'These pages failed Core Web Vitals checks. Improve loading speed and visual stability.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'high'
        },

        # --- Architecture (Site Structure) ---
        'site_architecture': {
            'title': 'Build Site Architecture',
            'description': 'Define and visualize the site structure.',
            'type': 'architecture', 'role': 'Optimization Specialist', 'priority': 'high'
        },

        # --- Usability (Technical & Content) ---
        'permalink_issues': {
            'title': 'Optimize URL Structure',
            'description': 'URLs should be clean, readable, and keyword-rich (avoid special chars/IDs).',
            'type': 'usability', 'role': 'Optimization Specialist', 'priority': 'medium'
        },
        'sitemap_issues': {
            'title': 'Fix Sitemap Issues',
            'description': 'Ensure sitemap.xml exists and contains valid URLs.',
            'type': 'usability', 'role': 'Optimization Specialist', 'priority': 'high'
        },
        'robots_issues': {
            'title': 'Review Robots.txt',
            'description': 'Ensure robots.txt is not blocking important pages.',
            'type': 'usability', 'role': 'Optimization Specialist', 'priority': 'high'
        },
        'no_canonical': {
            'title': 'Add Canonical Tags',
            'description': 'Canonical tags prevent duplicate content issues. Add self-referencing canonicals if unique.',
            'type': 'usability', 'role': 'Optimization Specialist', 'priority': 'medium'
        },
        'duplicate_content': {
            'title': 'Resolve Duplicate Content',
            'description': 'These pages are very similar to others. Use canonicals or distinct content.',
            'type': 'content', 'role': 'Content Creator', 'priority': 'high'
        },
        'no_index': {
            'title': 'Review No-Index Tags',
            'description': 'These pages are marked as no-index. Confirm this is intentional.',
            'type': 'usability', 'role': 'Optimization Specialist', 'priority': 'low'
        },
        'schema_missing': {
            'title': 'Implement Structured Data (Schema)',
            'description': 'Add relevant schema markup (Organization, Article, Product) to improve SERP features.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'medium'
        },
        'server_errors_5xx': {
            'title': 'Fix Server Errors (5xx)',
            'description': 'These pages returned a 500-level error. Investigate server logs immediately.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'critical'
        },
        'client_errors_4xx': {
            'title': 'Fix Broken Pages (404/4xx)',
            'description': 'These URLs do not exist. Redirect them or restore the content.',
            'type': 'technical', 'role': 'Optimization Specialist', 'priority': 'high'
        }
    }
    
    created_tasks = []

    # 1. Fetch existing pending/in-progress tasks for this campaign
    existing_tasks = []
    try:
        res = supabase_client.table('tasks').select('*').eq('campaign_id', campaign_id).in_('status', ['pending', 'in_progress']).execute()
        existing_tasks = res.data or []
    except Exception as e:
        print(f"Error fetching existing tasks: {e}")

    # 2. Iterate and Merge/Create
    for category, issues_map in categorized_data.items():
        if not isinstance(issues_map, dict):
            continue
            
        for issue_key, issue_data in issues_map.items():
            if not isinstance(issue_data, dict) or issue_data.get('issues', 0) == 0:
                continue

            items = issue_data.get('items', [])
            if not items and issue_key != 'sitemap_issues':
                 continue

            template = TASK_TEMPLATES.get(issue_key)
            if not template:
                continue

            base_title = template['title']
            new_urls = items[:50] # Hard cap per run to avoid huge db rows

            # Find if a matching task already exists by prefix title
            matching_task = next((t for t in existing_tasks if t.get('title', '').startswith(base_title)), None)

            if matching_task:
                # Merge checklist
                current_checklist = matching_task.get('checklist') or []
                existing_urls = {item['item'] for item in current_checklist if isinstance(item, dict) and 'item' in item}
                
                # Append new unique URLs
                added = False
                for url in new_urls:
                    if url not in existing_urls:
                        current_checklist.append({'item': url, 'completed': False})
                        added = True
                
                if added:
                    # Update task title with new total count
                    total_count = len(current_checklist)
                    new_title = f"{base_title} ({total_count} pages)"
                    try:
                        supabase_client.table('tasks').update({
                            'checklist': current_checklist,
                            'title': new_title
                        }).eq('id', matching_task['id']).execute()
                        created_tasks.append(matching_task)
                    except Exception as e:
                        print(f"Error updating task {issue_key}: {e}")
            else:
                # Create brand new task
                priority_map = {'critical': 3, 'high': 2, 'medium': 1, 'low': 0}
                task_data = {
                    'campaign_id': campaign_id,
                    'type': template['type'],
                    'title': f"{base_title} ({len(new_urls)} pages)",
                    'description': template['description'],
                    'checklist': [{'item': url, 'completed': False} for url in new_urls],
                    'assigned_role': template['role'],
                    'priority': priority_map.get(template.get('priority', 'medium'), 1),
                    'status': 'pending',
                    'created_at': 'now()'
                }
                
                try:
                    result = supabase_client.table('tasks').insert(task_data).execute()
                    if result.data:
                        created_tasks.append(result.data[0])
                except Exception as e:
                    print(f"Error creating task for {issue_key}: {e}")

    print(f"Processed {len(created_tasks)} tasks from audit.")
    return created_tasks

def categorize_audit_issues(pages: list, summary: dict = None) -> dict:
    """
    Structure audit data into Accessibility, Usability, and Architecture categories.
    Returns counts and sample URLs for each metric.
    """
    if not pages:
        return {}

    # Initialize structure - FLATTENED for easier UI rendering
    data = {
        "accessibility": {
            "core_web_vitals": {"score": 0, "issues": 0, "label": "Pass"},
            "page_speed": {"score": 0, "issues": 0, "label": "Good"}, # Desktop/Mobile avg
            "mobile_friendly": {"issues": 0, "items": []},
            "image_alt_missing": {"issues": 0, "items": []},
            "images_large": {"issues": 0, "items": []}, # >100KB
            "web_dev_score": {"score": 0, "label": "N/A"}
        },
        "usability": {
            "title_missing": {"issues": 0, "items": []},
            "title_duplicate": {"issues": 0, "items": []},
            "title_over_65": {"issues": 0, "items": []},
            "desc_missing": {"issues": 0, "items": []},
            "desc_duplicate": {"issues": 0, "items": []},
            "desc_over_155": {"issues": 0, "items": []},
            "h1_missing": {"issues": 0, "items": []},
            "h1_multiple": {"issues": 0, "items": []},
            "h2_missing": {"issues": 0, "items": []},
            "low_word_count": {"issues": 0, "items": []},
            "misspelling": {"issues": 0, "items": []},
            "links_broken": {"issues": 0, "items": []},
            "links_redirect_3xx": {"issues": 0, "items": []},
            "orphan_urls": {"issues": 0, "items": []},
            "permalink_issues": {"issues": 0, "items": []},
            "sitemap_issues": {"issues": 0, "items": []},
            "robots_issues": {"issues": 0, "items": []},
            "no_canonical": {"issues": 0, "items": []},
            "duplicate_content": {"issues": 0, "items": []},
            "no_index": {"issues": 0, "items": []},
            "schema_missing": {"issues": 0, "items": []},
            "server_errors_5xx": {"issues": 0, "items": []},
            "client_errors_4xx": {"issues": 0, "items": []},
        },
        "architecture": {
            "site_architecture": {"issues": 0, "items": [], "label": "Coming Soon"},
        }
    }

    # Aggregate Data
    total_speed_score = 0
    pages_with_speed = 0
    
    # CWV Counters
    cwv_issues = 0
    
    for page in pages:
        url = page.get('url', '')
        meta = page.get('meta', {})
        
        # Robust Check Extraction
        checks = page.get('checks', {}) 
        if not checks:
             checks = page.get('dfs_checks', {})
        if not checks:
             checks = page.get('issues', {})

        content = page.get('content', {})
        
        # --- Accessibility ---
        
        # Core Web Vitals (Simple Logic)
        # Fail if LCP > 2.5s OR CLS > 0.1
        lcp = page.get('largest_contentful_paint', 0) or 0
        cls = page.get('cumulative_layout_shift', 0) or 0
        if lcp > 2500 or cls > 0.1:
            cwv_issues += 1
            # Add to items so it's clickable
            data['accessibility']['core_web_vitals'].setdefault('items', []).append(f"{url} (LCP: {lcp}ms, CLS: {cls})")

        # Mobile Friendly
        if checks.get('is_mobile_friendly') is False:
             data['accessibility']['mobile_friendly']['issues'] += 1
             data['accessibility']['mobile_friendly']['items'].append(url)

        # Image Alt
        if checks.get('no_image_alt'):
             data['accessibility']['image_alt_missing']['issues'] += 1
             data['accessibility']['image_alt_missing']['items'].append(url)

        # Broken Images / Resources (New Check to capture missing assets)
        if checks.get('broken_resources') or checks.get('has_broken_resources'):
             data['accessibility']['images_large']['issues'] += 1 # Grouping with large images or create new? Using existing Large Images for "Asset Issues"
             data['accessibility']['images_large']['items'].append(f"{url} (Broken Resource)")

        # Large Images (>100KB avg)
        if (page.get('images_size', 0) / (page.get('images_count', 1) or 1)) > 102400: 
             data['accessibility']['images_large']['issues'] += 1
             data['accessibility']['images_large']['items'].append(url)

        # Speed (OnPage Score as proxy)
        score = page.get('onpage_score', 0)
        if score:
            total_speed_score += score
            pages_with_speed += 1
            
        # Flag poor speed pages
        if score < 50:
             data['accessibility']['page_speed']['issues'] += 1
             data['accessibility']['page_speed'].setdefault('items', []).append(f"{url} (Score: {score})")

        # --- Usability ---

        # Titles
        title = meta.get('title', '')
        if not title:
            data['usability']['title_missing']['issues'] += 1
            data['usability']['title_missing']['items'].append(url)
        elif len(title) > 65:
            data['usability']['title_over_65']['issues'] += 1
            data['usability']['title_over_65']['items'].append(url)
        
        if checks.get('duplicate_title') or checks.get('duplicate_title_tag'):
             data['usability']['title_duplicate']['issues'] += 1
             data['usability']['title_duplicate']['items'].append(url)

        # Descriptions
        desc = meta.get('description', '')
        if not desc:
            data['usability']['desc_missing']['issues'] += 1
            data['usability']['desc_missing']['items'].append(url)
        elif len(desc) > 155:
            data['usability']['desc_over_155']['issues'] += 1
            data['usability']['desc_over_155']['items'].append(url)
            
        if checks.get('duplicate_description'):
             data['usability']['desc_duplicate']['issues'] += 1
             data['usability']['desc_duplicate']['items'].append(url)

        # Headings
        if checks.get('no_h1') or checks.get('no_h1_tag'):
             data['usability']['h1_missing']['issues'] += 1
             data['usability']['h1_missing']['items'].append(url)
        if checks.get('duplicate_h1') or checks.get('duplicate_h1_tag') or len(meta.get('h1', []) or []) > 1:
             data['usability']['h1_multiple']['issues'] += 1
             data['usability']['h1_multiple']['items'].append(url)
             
        if len(meta.get('h2', []) or []) == 0:
             data['usability']['h2_missing']['issues'] += 1
             data['usability']['h2_missing']['items'].append(url)

        # Content
        if checks.get('low_content') or checks.get('low_content_rate'):
             data['usability']['low_word_count']['issues'] += 1
             data['usability']['low_word_count']['items'].append(url)

        # Misspelling
        if checks.get('has_misspelling'):
             data['usability']['misspelling']['issues'] += 1
             data['usability']['misspelling']['items'].append(url)
             
        # Links
        # Catch explicit 4xx/5xx pages AND pages with broken links on them
        if checks.get('is_broken') or checks.get('broken_links') or checks.get('has_broken_links'):
             data['usability']['links_broken']['issues'] += 1
             data['usability']['links_broken']['items'].append(url)
        if checks.get('is_redirect'):
             data['usability']['links_redirect_3xx']['issues'] += 1
             data['usability']['links_redirect_3xx']['items'].append(url)
        if checks.get('is_orphan_page'):
             data['usability']['orphan_urls']['issues'] += 1
             data['usability']['orphan_urls']['items'].append(url)


        # --- Architecture (Moved to Usability per 3-pillar model) ---
        
        # Permalink Structure (SEO Friendly URL)
        if checks.get('seo_friendly_url') is False:
             data['usability']['permalink_issues']['issues'] += 1
             data['usability']['permalink_issues']['items'].append(url)

        # Indexing
        if checks.get('no_canonical'):
             data['usability']['no_canonical']['issues'] += 1
             data['usability']['no_canonical']['items'].append(url)
        if checks.get('duplicate_content'):
             data['usability']['duplicate_content']['issues'] += 1
             data['usability']['duplicate_content']['items'].append(url)
        if checks.get('is_marked_as_noindex') or checks.get('no_index'): 
             data['usability']['no_index']['issues'] += 1
             data['usability']['no_index']['items'].append(url)
             
        # Server
        if page.get('status_code', 200) >= 500:
             data['usability']['server_errors_5xx']['issues'] += 1
             data['usability']['server_errors_5xx']['items'].append(url)
        elif page.get('status_code', 200) >= 400:
             data['usability']['client_errors_4xx']['issues'] += 1
             data['usability']['client_errors_4xx']['items'].append(url)

        # Schema (Basic check)
        if not page.get('meta', {}).get('schema') and not checks.get('has_schema'):
             data['usability']['schema_missing']['issues'] += 1
             data['usability']['schema_missing']['items'].append(url)


    # Summary Level Data overrides/calculations
    if summary:
        data['accessibility']['page_speed']['score'] = int(summary.get('onpage_score', 0))
        data['accessibility']['web_dev_score']['score'] = int(summary.get('onpage_score', 0)) # Proxy
        
        # Sitemap Check from Summary
        if not summary.get('has_sitemap'):
            data['usability']['sitemap_issues']['issues'] = 1
            data['usability']['sitemap_issues']['items'].append("Sitemap missing")

        # Robots.txt Check (Proxy: if we crawled, it's likely accessible, but check summary logic if available)
        # For now, we leave 0 unless specific error
            
    elif pages_with_speed > 0:
        avg_score = int(total_speed_score / pages_with_speed)
        data['accessibility']['page_speed']['score'] = avg_score
        data['accessibility']['web_dev_score']['score'] = avg_score

    # CWV Score Calculation
    data['accessibility']['core_web_vitals']['issues'] = cwv_issues
    if cwv_issues == 0 and len(pages) > 0:
        data['accessibility']['core_web_vitals']['label'] = "Pass"
        data['accessibility']['core_web_vitals']['score'] = 100
    else:
        data['accessibility']['core_web_vitals']['label'] = "Fail"
        data['accessibility']['core_web_vitals']['score'] = max(0, 100 - (cwv_issues * 5))

    # Logic for Labels
    score = data['accessibility']['page_speed']['score']
    if score >= 90:
        data['accessibility']['page_speed']['label'] = "Excellent"
    elif score >= 50:
        data['accessibility']['page_speed']['label'] = "Fair"
    else:
        data['accessibility']['page_speed']['label'] = "Poor"

    # Set status labels for all items
    for cat in data:
        for key in data[cat]:
            item = data[cat][key]
            # Use get() safely
            if item.get('issues', 0) > 0:
                item['status'] = 'fail'
            elif item.get('score') is not None and item.get('score') < 50:
                item['status'] = 'fail'
            else:
                item['status'] = 'pass'

    return data
