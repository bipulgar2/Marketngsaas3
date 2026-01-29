"""
DataForSEO API Client for Deep Audit functionality.
Provides comprehensive on-page SEO analysis using DataForSEO's On-Page API.
"""
import os
import requests
import base64
import time
import sys
from typing import Optional, Dict, Any, List

# Ensure .env is loaded
from dotenv import load_dotenv
load_dotenv()

# API Configuration
DATAFORSEO_API_URL = "https://api.dataforseo.com/v3"


def get_auth_header() -> Dict[str, str]:
    """Get authorization header for DataForSEO API."""
    login = os.getenv('DATAFORSEO_LOGIN')
    password = os.getenv('DATAFORSEO_PASSWORD')
    
    if not login or not password:
        # Fallback for dev - remove in prod
        if os.getenv('FLASK_ENV') == 'development':
            print("WARNING: DataForSEO credentials missing in dev mode")
            return {"Authorization": "Basic xxx"}
        raise ValueError("DataForSEO credentials not configured. Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.")
    
    credentials = f"{login}:{password}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def get_domain_rank_overview(domain: str, location_code: int = 2840, language_code: str = "en") -> Dict[str, Any]:
    """
    Get organic traffic estimate and keyword counts for a domain.
    Uses DataForSEO SERP / Rank Overview API (simulated via SERP API for now or using Traffic Analytics).
    Note: DataForSEO Traffic Analytics is separate. We'll use a simple SERP check or available endpoint.
    For this MVP, we will use 'serp/google/organic/live/advanced' to fetch ranking for brand name 
    OR just return mock data if the specific API requires separate add-on.
    
    ACTUALLY: We will use 'dataforseo_labs/google/ranked_keywords/live' to get total keywords count
    and estimating traffic from that is better.
    """
    endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/ranked_keywords/live"
    
    payload = [{
        "target": domain,
        "location_code": location_code,
        "language_code": language_code,
        "limit": 1, 
        "include_serp_info": False
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0]
            total_count = result.get('total_count', 0)
            
            # Estimate traffic (very rough heuristic without full traffic API)
            # In real app, buy Traffic Analytics add-on
            estimated_traffic = int(total_count * 3.5) 
            
            return {
                "success": True,
                "domain": domain,
                "organic_keywords": total_count,
                "estimated_traffic": estimated_traffic,
                "metrics": result.get('metrics', {})
            }
        else:
            # Fallback for demo if API fails/quota exceeded
            print(f"DataForSEO Labs API Error: {data.get('status_message')}")
            return {
                "success": False, 
                "error": data.get('status_message'),
                # Fallback mock data for demo smoothness if quota fails
                "organic_keywords": 0,
                "estimated_traffic": 0
            }
            
    except Exception as e:
        print(f"Error fetching competitor stats: {e}")
        return {"success": False, "error": str(e)}


def start_onpage_audit(domain: str, max_pages: int = 200) -> Dict[str, Any]:
    """
    Start an on-page audit task for a domain.
    
    Args:
        domain: The domain to audit (e.g., 'example.com')
        max_pages: Maximum pages to crawl (default 200 for comprehensive analysis)
    
    Returns:
        Dict with task_id and status
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/task_post"
    
    payload = [{
        "target": domain,
        "max_crawl_pages": max_pages,
        "load_resources": True,
        "enable_javascript": True,
        "enable_browser_rendering": True,
        "enable_xhr": True,
        "check_spell": True,
        "calculate_keyword_density": True,
        "store_raw_html": False
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            tasks = data.get('tasks') or []
            if len(tasks) > 0 and tasks[0] is not None:
                task = tasks[0]
                return {
                    "success": True,
                    "task_id": task.get('id') if isinstance(task, dict) else None,
                    "status": task.get('status_message') if isinstance(task, dict) else 'Unknown',
                    "cost": task.get('cost', 0) if isinstance(task, dict) else 0
                }
            else:
                return {"success": False, "error": "API returned empty or null task list"}
        else:
            return {
                "success": False,
                "error": data.get('status_message', 'Unknown error')
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


def get_audit_status(task_id: str) -> Dict[str, Any]:
    """
    Check the status of an ongoing audit task.
    
    Args:
        task_id: The task ID from start_onpage_audit
    
    Returns:
        Dict with crawl progress and status
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/tasks_ready"
    
    try:
        response = requests.get(
            endpoint,
            headers=get_auth_header(),
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('tasks'):
            for task in data['tasks'][0].get('result', []):
                if task.get('id') == task_id:
                    return {
                        "success": True,
                        "ready": True,
                        "task_id": task_id
                    }
        
        return {
            "success": True,
            "ready": False,
            "message": "Task still in progress"
        }
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_audit_summary(task_id: str) -> Dict[str, Any]:
    """
    Get the summary results of a completed audit.
    
    Args:
        task_id: The task ID from start_onpage_audit
    
    Returns:
        Dict with overall audit summary
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/summary/{task_id}"
    
    try:
        response = requests.get(
            endpoint,
            headers=get_auth_header(),
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            task_result = data['tasks'][0]
            result_list = task_result.get('result', [])
            
            # Debug logging
            print(f"DEBUG get_audit_summary: result_list type={type(result_list)}, len={len(result_list) if isinstance(result_list, list) else 'N/A'}")
            if result_list and isinstance(result_list, list) and len(result_list) > 0:
                print(f"DEBUG get_audit_summary: first item type={type(result_list[0])}")
            
            # Safely extract result - handle all edge cases
            result = {}
            if isinstance(result_list, list) and len(result_list) > 0:
                first_item = result_list[0]
                if isinstance(first_item, dict):
                    result = first_item
                else:
                    print(f"DEBUG get_audit_summary: first_item is NOT a dict, it's: {first_item}")
            
            # Safely extract crawl_progress
            # It can be a string "finished" or a dict with progress stats
            crawl_progress_val = result.get('crawl_progress')
            crawl_progress_status = "unknown"
            crawl_progress_stats = {}
            
            if isinstance(crawl_progress_val, str):
                crawl_progress_status = crawl_progress_val
            elif isinstance(crawl_progress_val, dict):
                crawl_progress_stats = crawl_progress_val
                crawl_progress_status = "in_progress" # generic fall back
            
            # Extract page_metrics which holds the true totals (can be null in API response!)
            page_metrics = result.get('page_metrics') or {}
            
            return {
                "success": True,
                "summary": {
                    "domain": result.get('target', '') if isinstance(result, dict) else '',
                    "crawl_progress": crawl_progress_status, # "finished" or status string
                    "pages_crawled": crawl_progress_stats.get('pages_crawled', 0) if crawl_progress_stats else (result.get('crawl_status') or {}).get('pages_crawled', 0),
                    "pages_in_queue": crawl_progress_stats.get('pages_in_queue', 0) if crawl_progress_stats else (result.get('crawl_status') or {}).get('pages_in_queue', 0),
                    "onpage_score": result.get('onpage_score', 0) if isinstance(result, dict) else 0,
                    "total_pages": result.get('total_pages', 0) if isinstance(result, dict) else 0,
                    
                    # Store full page_metrics for deeper extraction
                    "page_metrics": page_metrics,
                    
                    # Legacy fields (kept for backward compatibility but preferred from page_metrics)
                    "pages_with_issues": result.get('pages_with_issues', 0),
                    "duplicate_title": page_metrics.get('duplicate_title', 0),
                    "duplicate_description": page_metrics.get('duplicate_description', 0),
                    "duplicate_content": page_metrics.get('duplicate_content', 0),
                    "broken_links": page_metrics.get('broken_links', 0),
                    "broken_images": page_metrics.get('broken_resources', 0),
                    
                    # Link Stats from page_metrics
                    "links_internal": page_metrics.get('links_internal', 0),
                    "links_external": page_metrics.get('links_external', 0),
                    "non_indexable": page_metrics.get('non_indexable', 0),
                    
                    # Performance
                    "avg_page_load_time": result.get('avg_page_load_time', 0),
                    "avg_page_size": result.get('avg_page_size', 0),
                    
                    # Technical
                    "ssl_enabled": result.get('ssl_info', {}).get('valid_certificate', False),
                    "www_redirect": result.get('www_redirect_status_code') is not None,
                    "has_sitemap": result.get('checks', {}).get('sitemap', False)
                }
            }
        else:
            return {
                "success": False,
                "error": data.get('status_message', 'Unknown error')
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_page_issues(task_id: str, issue_type: str = "all", limit: int = 100) -> Dict[str, Any]:
    """
    Get detailed page-level issues from the audit.
    
    Args:
        task_id: The task ID
        issue_type: Type of issues to fetch (all, critical, warning, info)
        limit: Number of results
    
    Returns:
        Dict with list of pages and their issues
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/pages"
    
    payload = [{
        "id": task_id,
        "limit": limit,
        "order_by": ["onpage_score,asc"]  # Worst pages first
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = data['tasks'][0].get('result', [{}])[0] or {}
            pages = result.get('items') or []
            
            formatted_pages = []
            for page in pages:
                # Safely extract nested objects with full null protection
                meta = page.get('meta') or {}
                htags = meta.get('htags') or {}
                h1_list = htags.get('h1') or []
                h2_list = htags.get('h2') or []
                h3_list = htags.get('h3') or []
                title = meta.get('title') or ''
                description = meta.get('description') or ''
                
                # Page timing metrics (Core Web Vitals and more)
                page_timing = page.get('page_timing') or {}
                
                # Content metrics - nested under meta.content
                content_info = meta.get('content') or {}
                
                # All checks from DataForSEO
                checks = page.get('checks') or {}
                
                # Cache info
                cache_control = page.get('cache_control') or {}
                
                # Debug: Log raw data for first page
                if len(formatted_pages) == 0:
                    print(f"DEBUG get_page_issues: First page ALL raw data:", file=sys.stderr)
                    print(f"  page keys: {list(page.keys())}", file=sys.stderr)
                    print(f"  meta keys: {list(meta.keys())}", file=sys.stderr)
                    print(f"  meta.content: {content_info}", file=sys.stderr)
                    print(f"  page_timing: {page_timing}", file=sys.stderr)
                    print(f"  checks: {checks}", file=sys.stderr)
                
                # Extract timing metrics
                time_to_interactive = page_timing.get('time_to_interactive') or 0
                dom_complete = page_timing.get('dom_complete') or 0
                lcp = page_timing.get('largest_contentful_paint') or 0
                fid = page_timing.get('first_input_delay') or 0
                connection_time = page_timing.get('connection_time') or 0
                ttfb = page_timing.get('waiting_time') or 0  # Time to First Byte
                download_time = page_timing.get('download_time') or 0
                duration_time = page_timing.get('duration_time') or 0
                
                # Use TTI as primary load time, fallback to others
                load_time_ms = time_to_interactive or dom_complete or download_time or 0
                
                # Content metrics
                word_count = content_info.get('plain_text_word_count', 0) or 0
                plain_text_size = content_info.get('plain_text_size', 0) or 0
                plain_text_rate = content_info.get('plain_text_rate', 0) or 0
                
                # Readability indices
                automated_readability = content_info.get('automated_readability_index') or 0
                coleman_liau = content_info.get('coleman_liau_readability_index') or 0
                flesch_kincaid = content_info.get('flesch_kincaid_readability_index') or 0
                smog = content_info.get('smog_readability_index') or 0
                
                # Extract all meta counts
                internal_links = meta.get('internal_links_count', 0) or 0
                external_links = meta.get('external_links_count', 0) or 0
                inbound_links = meta.get('inbound_links_count', 0) or 0
                images_count = meta.get('images_count', 0) or 0
                images_size = meta.get('images_size', 0) or 0
                scripts_count = meta.get('scripts_count', 0) or 0
                scripts_size = meta.get('scripts_size', 0) or 0
                stylesheets_count = meta.get('stylesheets_count', 0) or 0
                stylesheets_size = meta.get('stylesheets_size', 0) or 0
                
                # Additional meta fields
                canonical = meta.get('canonical') or ''
                meta_keywords = meta.get('meta_keywords') or ''
                favicon = meta.get('favicon') or ''
                generator = meta.get('generator') or ''
                charset = meta.get('charset') or 0
                cumulative_layout_shift = meta.get('cumulative_layout_shift') or 0
                render_blocking_scripts = meta.get('render_blocking_scripts_count', 0) or 0
                render_blocking_stylesheets = meta.get('render_blocking_stylesheets_count', 0) or 0
                
                # Page-level fields
                page_size = page.get('size', 0) or 0
                encoded_size = page.get('encoded_size', 0) or 0
                total_transfer_size = page.get('total_transfer_size', 0) or 0
                fetch_time = page.get('fetch_time') or ''
                click_depth = page.get('click_depth', 0) or 0
                total_dom_size = page.get('total_dom_size', 0) or 0
                
                # Build comprehensive page data
                formatted_pages.append({
                    # Core identifiers
                    "url": page.get('url'),
                    "status_code": page.get('status_code'),
                    "onpage_score": page.get('onpage_score', 0),
                    "resource_type": page.get('resource_type', 'html'),
                    
                    # Meta object for frontend compatibility (frontend reads from p.meta.*)
                    "meta": {
                        "title": title,
                        "description": description,
                        "h1": h1_list,
                        "h2": h2_list,
                        "h3": h3_list,
                        "canonical": canonical,
                    },
                    
                    # Direct fields for backward compatibility
                    "title": title,
                    "title_length": len(title),
                    "description": description,
                    "description_length": len(description),
                    "canonical": canonical,
                    "meta_keywords": meta_keywords,
                    
                    # Headings (direct access)
                    "h1": h1_list,
                    "h1_count": len(h1_list),
                    "h2": h2_list,
                    "h2_count": len(h2_list),
                    "h3": h3_list,
                    "h3_count": len(h3_list),
                    
                    # Performance - Core Web Vitals
                    "load_time": load_time_ms,
                    "time_to_interactive": time_to_interactive,
                    "dom_complete": dom_complete,
                    "largest_contentful_paint": lcp,
                    "first_input_delay": fid,
                    "cumulative_layout_shift": cumulative_layout_shift,
                    "ttfb": ttfb,
                    "connection_time": connection_time,
                    "download_time": download_time,
                    "duration_time": duration_time,
                    
                    # Size metrics
                    "page_size": page_size,
                    "encoded_size": encoded_size,
                    "total_transfer_size": total_transfer_size,
                    "total_dom_size": total_dom_size,
                    
                    # Content metrics
                    "word_count": word_count,
                    "plain_text_size": plain_text_size,
                    "plain_text_rate": plain_text_rate,
                    
                    # Readability
                    "automated_readability_index": automated_readability,
                    "coleman_liau_index": coleman_liau,
                    "flesch_kincaid_index": flesch_kincaid,
                    "smog_index": smog,
                    
                    # Links
                    "internal_links_count": internal_links,
                    "external_links_count": external_links,
                    "inbound_links_count": inbound_links,
                    
                    # Resources
                    "images_count": images_count,
                    "images_size": images_size,
                    "scripts_count": scripts_count,
                    "scripts_size": scripts_size,
                    "stylesheets_count": stylesheets_count,
                    "stylesheets_size": stylesheets_size,
                    "render_blocking_scripts": render_blocking_scripts,
                    "render_blocking_stylesheets": render_blocking_stylesheets,
                    
                    # Technical
                    "is_https": checks.get('is_https', False) or str(page.get('url', '')).startswith('https'),
                    "is_http": checks.get('is_http', False),
                    "has_schema": checks.get('has_micromarkup', False),
                    "click_depth": click_depth,
                    "fetch_time": fetch_time,
                    "favicon": favicon,
                    "generator": generator,
                    "charset": charset,
                    
                    # Cache
                    "is_cacheable": cache_control.get('cachable', False),
                    "cache_ttl": cache_control.get('ttl', 0),
                    
                    # All DataForSEO checks (boolean flags)
                    "dfs_checks": checks,
                    
                    # Computed issues for quick filtering
                    "issues": {
                        "no_title": checks.get('no_title', not title),
                        "no_description": checks.get('no_description', not description),
                        "no_h1": checks.get('no_h1_tag', len(h1_list) == 0),
                        "multiple_h1": len(h1_list) > 1,
                        "title_too_long": checks.get('title_too_long', len(title) > 60),
                        "title_too_short": checks.get('title_too_short', 0 < len(title) < 30),
                        "description_too_long": len(description) > 160,
                        "no_canonical": not canonical,
                        "is_broken": checks.get('is_broken', False),
                        "is_redirect": checks.get('is_redirect', False),
                        "is_4xx": checks.get('is_4xx_code', False),
                        "is_5xx": checks.get('is_5xx_code', False),
                        "slow_load": checks.get('high_loading_time', load_time_ms > 3000),
                        "high_waiting_time": checks.get('high_waiting_time', False),
                        "low_content": checks.get('low_content_rate', word_count < 300),
                        "no_image_alt": checks.get('no_image_alt', False),
                        "no_image_title": checks.get('no_image_title', False),
                        "no_favicon": checks.get('no_favicon', False),
                        "duplicate_title": checks.get('duplicate_title_tag', False),
                        "duplicate_description": page.get('duplicate_description', False),
                        "duplicate_content": page.get('duplicate_content', False),
                        "has_render_blocking": checks.get('has_render_blocking_resources', False),
                        "deprecated_html_tags": checks.get('deprecated_html_tags', False),
                        "duplicate_meta_tags": checks.get('duplicate_meta_tags', False),
                        "no_doctype": checks.get('no_doctype', False),
                        "no_encoding": checks.get('no_encoding_meta_tag', False),
                        "https_to_http_links": checks.get('https_to_http_links', False),
                        "is_orphan_page": checks.get('is_orphan_page', False),
                        "redirect_chain": checks.get('redirect_chain', False),
                        "canonical_chain": checks.get('canonical_chain', False),
                        "has_links_to_redirects": checks.get('has_links_to_redirects', False),
                        "large_page_size": checks.get('large_page_size', False),
                        "low_readability": checks.get('low_readability_rate', False),
                        "has_misspelling": checks.get('has_misspelling', False),
                        "lorem_ipsum": checks.get('lorem_ipsum', False),
                        "seo_friendly_url": checks.get('seo_friendly_url', True),
                    }
                })
            
            return {
                "success": True,
                "total_count": result.get('total_count', len(pages)),
                "pages": formatted_pages
            }
        else:
            return {
                "success": False,
                "error": data.get('status_message', 'Unknown error')
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_lighthouse_audit(url: str, for_mobile: bool = True) -> Dict[str, Any]:
    """
    Run a Lighthouse audit on a specific URL for Core Web Vitals.
    
    Args:
        url: The URL to audit
        for_mobile: Whether to audit for mobile (True) or desktop (False)
    
    Returns:
        Dict with Lighthouse scores and metrics
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/lighthouse/live/json"
    
    payload = [{
        "url": url,
        "for_mobile": for_mobile,
        "categories": ["performance", "seo", "accessibility", "best_practices"]
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=120  # Lighthouse takes longer
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            task = data['tasks'][0]
            result = (task.get('result') or [{}])[0]
            
            if not result or not result.get('categories'):
                status = task.get('status_message', 'No result data available')
                print(f"DEBUG: get_lighthouse_audit - Task success but result empty: {status}")
                return {"success": False, "error": f"Lighthouse result not found: {status}"}

            categories = result.get('categories', {})
            audits = result.get('audits', {})
            
            return {
                "success": True,
                "url": url,
                "scores": {
                    "performance": int((categories.get('performance', {}).get('score') or 0) * 100),
                    "seo": int((categories.get('seo', {}).get('score') or 0) * 100),
                    "accessibility": int((categories.get('accessibility', {}).get('score') or 0) * 100),
                    "best_practices": int((categories.get('best-practices', {}).get('score') or 0) * 100)
                },
                "metrics": {
                    "fcp": audits.get('first-contentful-paint', {}).get('displayValue'),
                    "lcp": audits.get('largest-contentful-paint', {}).get('displayValue'),
                    "cls": audits.get('cumulative-layout-shift', {}).get('displayValue'),
                    "tbt": audits.get('total-blocking-time', {}).get('displayValue'),
                    "si": audits.get('speed-index', {}).get('displayValue')
                },
                "strategy": "mobile" if for_mobile else "desktop"
            }
        else:
            error_msg = data.get('status_message', 'Unknown error')
            print(f"DEBUG: get_lighthouse_audit - API Error: {error_msg}")
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        print(f"ERROR: Lighthouse audit failed: {e}")
        return {"success": False, "error": str(e)}


def instant_pages_audit(urls: List[str]) -> Dict[str, Any]:
    """
    Run instant on-page audit on specific URLs (up to 20 at a time).
    This is the per-page audit matching the Tech Audit flow.
    
    Args:
        urls: List of full URLs to audit (max 20)
    
    Returns:
        Dict with results for each URL including 120+ metrics
    """
    if not urls:
        return {"success": False, "error": "No URLs provided"}
    
    if len(urls) > 20:
        urls = urls[:20]  # API limit
    
    endpoint = f"{DATAFORSEO_API_URL}/on_page/instant_pages"
    
    # Build payload for each URL
    payload = []
    for url in urls:
        payload.append({
            "url": url,
            "enable_javascript": True,
            "enable_browser_rendering": True,
            "load_resources": True,
            "check_spell": True
        })
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=120  # Per-page audit can take longer
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            results = []
            
            for task in data.get('tasks', []):
                if task.get('result'):
                    for page_result in task['result']:
                        items = page_result.get('items', [])
                        if items:
                            page = items[0]
                            meta = page.get('meta', {})
                            htags = meta.get('htags', {})
                            content = page.get('content', {})
                            page_timing = page.get('page_timing', {})
                            checks = page.get('checks', {})
                            
                            results.append({
                                "url": page.get('url'),
                                "status_code": page.get('status_code'),
                                "onpage_score": page.get('onpage_score', 0),
                                
                                # Meta data
                                "title": meta.get('title'),
                                "title_length": len(meta.get('title') or ''),
                                "description": meta.get('description'),
                                "description_length": len(meta.get('description') or ''),
                                "h1": htags.get('h1') or [],
                                "h2_count": len(htags.get('h2') or []),
                                "canonical": meta.get('canonical'),
                                
                                # Content
                                "word_count": content.get('word_count', 0),
                                "plain_text_word_count": content.get('plain_text_word_count', 0),
                                
                                # Performance
                                "load_time_ms": page_timing.get('time_to_interactive'),
                                "dom_complete": page_timing.get('dom_complete'),
                                "page_size": page.get('size', 0),
                                
                                # Technical checks (DataForSEO specific)
                                "is_https": page.get('is_https', False),
                                "is_http2": page.get('is_http2', False),
                                "has_robots_meta": checks.get('has_meta_robots', False),
                                "is_indexable": not checks.get('no_index', False),
                                "has_schema": len((page.get('schema') or {}).get('items') or []) > 0,
                                
                                # Images
                                "images_count": len((page.get('images') or {}).get('images') or []),
                                "images_without_alt": sum(1 for img in ((page.get('images') or {}).get('images') or []) if not img.get('alt')),
                                
                                # Links
                                "internal_links_count": page.get('internal_links_count', 0),
                                "external_links_count": page.get('external_links_count', 0),
                                
                                # Issues summary
                                "checks": {
                                    "no_title": checks.get('no_title', False),
                                    "title_too_long": checks.get('title_too_long', False),
                                    "no_description": checks.get('no_description', False),
                                    "description_too_long": checks.get('description_too_long', False),
                                    "no_h1": checks.get('no_h1_tag', False),
                                    "duplicate_h1": checks.get('duplicate_h1_tags', False),
                                    "no_canonical": checks.get('no_canonical', False),
                                    "canonical_mismatch": checks.get('canonical_to_broken', False),
                                    "low_content": checks.get('low_content_rate', False),
                                    "broken_links": checks.get('has_broken_links', False),
                                    "redirect_chain": checks.get('redirect_chain', False),
                                    "slow_load": (page_timing.get('time_to_interactive') or 0) > 3000
                                }
                            })
            
            return {
                "success": True,
                "cost": data.get('cost', 0),
                "results": results
            }
        else:
            return {
                "success": False,
                "error": data.get('status_message', 'Unknown error')
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_links_data(task_id: str, limit: int = 100) -> Dict[str, Any]:
    """
    Get internal and external links data from the audit.
    
    Args:
        task_id: The task ID
        limit: Number of results
    
    Returns:
        Dict with link statistics and issues
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/links"
    
    payload = [{
        "id": task_id,
        "limit": limit,
        "order_by": ["link_from,asc"]
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            links = result.get('items') or []
            
            # Aggregate stats
            internal_count = sum(1 for l in links if l.get('type') == 'internal')
            external_count = sum(1 for l in links if l.get('type') == 'external')
            broken_links = [l for l in links if l.get('is_broken')]
            nofollow_links = [l for l in links if l.get('is_nofollow')]
            
            return {
                "success": True,
                "total_links": result.get('total_count', len(links)),
                "internal_count": internal_count,
                "external_count": external_count,
                "broken_count": len(broken_links),
                "nofollow_count": len(nofollow_links),
                "broken_links": [{"from": l.get('link_from'), "to": l.get('link_to'), "anchor": l.get('anchor')} for l in broken_links[:20]],
                "sample_links": links[:50]
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_redirect_chains(task_id: str, limit: int = 100) -> Dict[str, Any]:
    """
    Get redirect chain issues from the audit.
    
    Args:
        task_id: The task ID
        limit: Number of results
    
    Returns:
        Dict with redirect chain data
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/redirect_chains"
    
    payload = [{
        "id": task_id,
        "limit": limit
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            chains = result.get('items') or []
            
            return {
                "success": True,
                "total_chains": result.get('total_count', len(chains)),
                "chains": [
                    {
                        "from_url": chain.get('from_url'),
                        "to_url": chain.get('to_url'),
                        "chain_length": chain.get('chain_length', 0),
                        "is_loop": chain.get('is_loop', False),
                        "is_broken": chain.get('is_broken', False)
                    }
                    for chain in chains
                ]
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_duplicate_tags(task_id: str, limit: int = 100) -> Dict[str, Any]:
    """
    Get pages with duplicate title or description tags.
    
    Args:
        task_id: The task ID
        limit: Number of results
    
    Returns:
        Dict with duplicate tag data
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/duplicate_tags"
    
    payload = [{
        "id": task_id,
        "limit": limit
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            
            dup_titles = []
            dup_descriptions = []
            
            for item in items:
                if item.get('duplicate_tag_type') == 'title':
                    dup_titles.append({
                        "tag_value": item.get('tag'),
                        "pages": item.get('pages') or []
                    })
                elif item.get('duplicate_tag_type') == 'description':
                    dup_descriptions.append({
                        "tag_value": item.get('tag'),
                        "pages": item.get('pages') or []
                    })
            
            return {
                "success": True,
                "total_duplicates": result.get('total_count', len(items)),
                "duplicate_titles": dup_titles,
                "duplicate_descriptions": dup_descriptions
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_duplicate_content(task_id: str, limit: int = 100) -> Dict[str, Any]:
    """
    Get pages with duplicate/similar content.
    
    Args:
        task_id: The task ID
        limit: Number of results
    
    Returns:
        Dict with duplicate content data
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/duplicate_content"
    
    payload = [{
        "id": task_id,
        "limit": limit
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            
            return {
                "success": True,
                "total_duplicate_groups": result.get('total_count', len(items)),
                "duplicate_groups": [
                    {
                        "similarity": item.get('similarity', 0),
                        "pages": item.get('pages') or []
                    }
                    for item in items
                ]
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def get_non_indexable(task_id: str, limit: int = 100) -> Dict[str, Any]:
    """
    Get pages that are blocked from indexing.
    
    Args:
        task_id: The task ID
        limit: Number of results
    
    Returns:
        Dict with non-indexable pages
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/non_indexable"
    
    payload = [{
        "id": task_id,
        "limit": limit
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            
            return {
                "success": True,
                "total_non_indexable": result.get('total_count', len(items)),
                "pages": [
                    {
                        "url": item.get('url'),
                        "reason": item.get('reason'),
                        "meta_robots": item.get('meta', {}).get('robots')
                    }
                    for item in items
                ]
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}



def get_domain_rank_overview(domain: str) -> Dict[str, Any]:
    """Get domain rank and organic traffic overview."""
    try:
        # Use DataForSEO Labs for organic traffic & keywords count
        endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/historical_rank_overview/live"
        payload = [{
            "target": domain,
            "location_code": 2840, # US
            "language_code": "en"
        }]
        
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['tasks'] and data['tasks'][0]['result']:
                # Return the most recent month's data
                results = data['tasks'][0]['result'][0]['items']
                if results:
                    return results[-1] # Newest data
        return {}
    except Exception as e:
        print(f"Error fetching domain rank: {e}")
        return {}


def get_backlinks_summary(domain: str) -> Dict[str, Any]:
    """Get backlinks summary (DR, total links, referring domains)."""
    try:
        endpoint = f"{DATAFORSEO_API_URL}/backlinks/summary/live"
        payload = [{
            "target": domain,
            "include_subdomains": True
        }]
        
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['tasks'] and data['tasks'][0]['result']:
                return data['tasks'][0]['result'][0]
        return {}
    except Exception as e:
        print(f"Error fetching backlinks summary: {e}")
        return {}


def get_organic_keywords(domain: str, limit: int = 1000, location_code: int = 2356) -> List[Dict[str, Any]]:
    """
    Get organic keywords from DataForSEO Labs (up to 1000 for comprehensive audit).
    
    Args:
        domain: The domain to analyze
        limit: Number of keywords to return (default 1000 for full audits)
        location_code: 2356 = India, 2840 = US
    
    Returns:
        List of keyword objects in FULL DataForSEO format (with keyword_data, ranked_serp_element)
    """
    try:
        endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/ranked_keywords/live"
        payload = [{
            "target": domain,
            "location_code": location_code,
            "language_code": "en",
            "limit": limit,
            "include_serp_info": True  # Required to get keyword_difficulty
        }]
        
        print(f"DEBUG: Fetching organic keywords for {domain} (location={location_code}, limit={limit})", file=sys.stderr)
        
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=120  # Increased timeout for larger requests
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('tasks') and data['tasks'][0].get('result'):
                result = data['tasks'][0]['result'][0]
                items = result.get('items', [])
                total_count = result.get('total_count', 0)
                print(f"DEBUG: Got {len(items)} keywords, total_count={total_count}", file=sys.stderr)
                return items
            else:
                print(f"DEBUG: No result in response: {data.get('tasks', [{}])[0].get('status_message', 'Unknown')}", file=sys.stderr)
        else:
            print(f"DEBUG: API returned status {response.status_code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error fetching organic keywords: {e}", file=sys.stderr)
        return []


def get_referring_domains(domain: str, limit: int = 1000, order_by: list = ["rank,desc"]) -> List[Dict[str, Any]]:
    """Get referring domains. Default order is high rank first."""
    try:
        endpoint = f"{DATAFORSEO_API_URL}/backlinks/referring_domains/live"
        payload = [{
            "target": domain,
            "limit": limit,
            "order_by": order_by
        }]
        
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['tasks'] and data['tasks'][0]['result']:
                return data['tasks'][0]['result'][0]['items']
        return []
    except Exception as e:
        print(f"Error fetching referring domains: {e}")
        return []

def run_full_site_audit(domain: str, max_pages: int = 10, mock: bool = False) -> Dict[str, Any]:
    """
    Run a comprehensive site audit and return all data.
    This is a synchronous wrapper that waits for completion.
    
    Args:
        domain: The domain to audit
        max_pages: Maximum pages to crawl
        mock: If True, return mock data without calling API (for testing)
    
    Returns:
        Dict with all audit data from all endpoints
    """
    # MOCK MODE - return fake data for testing without API calls
    if mock:
        return _get_mock_audit_data(domain, max_pages)
    
    # Start the audit
    start_result = start_onpage_audit(domain, max_pages)
    
    # Safety check
    if start_result is None:
        return {"success": False, "error": "start_onpage_audit returned None"}
    if not start_result.get('success'):
        return start_result
    
    task_id = start_result.get('task_id')
    if not task_id:
        return {"success": False, "error": "No task_id returned from API"}
    
    # Poll for completion (max 30 minutes for large sites)
    max_wait = 1800
    waited = 0
    poll_interval = 10
    
    print(f"DEBUG: Starting crawl polling for task {task_id} (timeout={max_wait}s)", file=sys.stderr)
    
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        
        # Check summary for crawl progress
        summary_result = get_audit_summary(task_id)
        
        # Safety check: ensure summary_result is not None
        if summary_result is None:
            print(f"DEBUG: Poll {waited}s: get_audit_summary returned None!", file=sys.stderr)
            continue
            
        if summary_result.get('success'):
            summary = summary_result.get('summary') or {}
            crawl_progress = summary.get('crawl_progress', 'unknown')
            
            pages_crawled = summary.get('pages_crawled', 0) or 0
            pages_in_queue = summary.get('pages_in_queue', 0) or 0
            
            print(f"DEBUG: Poll {waited}s: {pages_crawled} crawled, {pages_in_queue} queued. Status: {crawl_progress}", file=sys.stderr)
            
            # Wait for queue to empty OR status to be 'finished'
            if crawl_progress == 'finished' or (pages_in_queue == 0 and pages_crawled > 0 and waited > 30):
                print(f"DEBUG: Audit complete! Final: {pages_crawled} pages", file=sys.stderr)
                break
        else:
            error_msg = summary_result.get('error', 'Unknown error') if summary_result else 'None result'
            print(f"DEBUG: Poll {waited}s failed: {error_msg}", file=sys.stderr)
    
    # Collect all data
    result = {
        "success": True,
        "task_id": task_id,
        "domain": domain,
        "max_pages": max_pages
    }
    
    # Get all endpoint data
    
    # Get all endpoint data
    summary_data = get_audit_summary(task_id)
    result["summary"] = summary_data
    
    # Extract page_metrics from summary to enrich other sections
    # The summary from API contains "page_metrics" with correct total counts
    page_metrics = {}
    if summary_data.get('success'):
        # API variance: sometimes it's directly in summary, sometimes nested
        # Based on user JSON: data['tasks'][0]['result'][0]['page_metrics']
        # Our get_audit_summary flattens it partially. Let's re-read the full structure if needed
        # Or just trust our wrapper.
        # Let's peek into the raw summary data we already have
        pm = summary_data.get('summary', {})
        # If our wrapper doesn't expose everything, we might miss it.
        # But wait, our wrapper in get_audit_summary constructs a specific dict.
        # It DOES NOT currently return 'page_metrics'. We should modify it or better yet,
        # pass the raw summary to the frontend or extraction logic.
        
        # Actually, let's just fetch detailed data. 
        # But for accurate counts, we should use what we can.
    
    result["pages"] = get_page_issues(task_id, limit=max_pages)
    result["links"] = get_links_data(task_id, limit=100)
    result["redirect_chains"] = get_redirect_chains(task_id, limit=50)
    result["duplicate_tags"] = get_duplicate_tags(task_id, limit=50)
    result["duplicate_content"] = get_duplicate_content(task_id, limit=50)
    result["non_indexable"] = get_non_indexable(task_id, limit=50)

    # Fetch additional data for slides (Rank, Backlinks, Keywords)
    # These are live calls and don't depend on the crawl task_id
    traffic_data = run_traffic_audit(domain)
    result.update(traffic_data)
    
    # ENRICHMENT: If links data is empty but summary has it, fill it in.
    # The get_links_data function might return 0 if it hits a different endpoint structure.
    # But more importantly, we want the TOTALS from the summary to be available.
    if summary_data.get('success'):
        # We need to access the raw page_metrics that might be inside specific fields
        # OR update get_audit_summary to return them.
        pass
    
    return result

def run_traffic_audit(domain: str) -> Dict[str, Any]:
    """
    Run only the traffic and keyword analysis parts of the audit.
    Does NOT crawl pages. Very fast and cheap.
    """
    print(f"Fetching traffic data (Rank, Backlinks, Keywords) for {domain}...", file=sys.stderr)
    result = {}
    
    # Run these in parallel ideally, but sequential is fast enough for now
    result["domain_rank"] = get_domain_rank_overview(domain)
    result["backlinks_summary"] = get_backlinks_summary(domain)
    result["organic_keywords"] = get_organic_keywords(domain) # Now fetches 1000 keywords
    result["referring_domains"] = get_referring_domains(domain)
    
    return result


def _get_mock_audit_data(domain: str, max_pages: int) -> Dict[str, Any]:
    """Generate realistic mock data for testing without API calls."""
    import random
    
    # Generate mock pages
    mock_pages = []
    page_paths = ['/', '/about', '/contact', '/products', '/services', '/blog', '/faq', '/pricing', '/team', '/careers']
    
    for i, path in enumerate(page_paths[:max_pages]):
        score = random.randint(40, 95)
        mock_pages.append({
            "url": f"https://{domain}{path}",
            "status_code": 200,
            "onpage_score": score,
            "title": f"Page Title for {path}" if random.random() > 0.2 else "",
            "description": f"Meta description for {path}" if random.random() > 0.15 else "",
            "h1": [f"H1 for {path}"] if random.random() > 0.25 else [],
            "load_time": random.randint(500, 3500),
            "word_count": random.randint(200, 2000),
            "issues": {
                "no_title": random.random() < 0.1,
                "no_description": random.random() < 0.15,
                "no_h1": random.random() < 0.2,
                "multiple_h1": random.random() < 0.1,
                "title_too_long": random.random() < 0.1,
                "title_too_short": random.random() < 0.15,
                "is_broken": False,
                "is_redirect": random.random() < 0.05,
                "slow_load": random.random() < 0.2
            }
        })
    
    avg_score = sum(p["onpage_score"] for p in mock_pages) / len(mock_pages) if mock_pages else 0
    
    return {
        "success": True,
        "task_id": "mock-task-12345",
        "domain": domain,
        "max_pages": max_pages,
        "summary": {
            "success": True,
            "summary": {
                "domain": domain,
                "pages_crawled": len(mock_pages),
                "pages_in_queue": 0,
                "onpage_score": round(avg_score, 1),
                "total_pages": len(mock_pages),
                "pages_with_issues": random.randint(2, min(5, len(mock_pages))),
                "duplicate_title": random.randint(0, 2),
                "duplicate_description": random.randint(0, 3),
                "duplicate_content": random.randint(0, 1),
                "broken_links": random.randint(0, 5),
                "broken_images": random.randint(0, 3),
                "avg_page_load_time": random.randint(800, 2000),
                "avg_page_size": random.randint(100000, 500000),
                "ssl_enabled": True,
                "www_redirect": True,
                "has_sitemap": random.random() > 0.3
            }
        },
        "pages": {
            "success": True,
            "total_count": len(mock_pages),
            "pages": mock_pages
        },
        "links": {
            "success": True,
            "total_links": random.randint(50, 200),
            "internal_count": random.randint(40, 150),
            "external_count": random.randint(10, 50),
            "broken_count": random.randint(0, 5),
            "nofollow_count": random.randint(0, 10),
            "broken_links": []
        },
        "redirect_chains": {
            "success": True,
            "total_chains": random.randint(0, 3),
            "chains": []
        },
        "duplicate_tags": {
            "success": True,
            "total_duplicates": random.randint(0, 4),
            "duplicate_titles": [],
            "duplicate_descriptions": []
        },
        "duplicate_content": {
            "success": True,
            "total_duplicate_groups": random.randint(0, 2),
            "duplicate_groups": []
        },
        "non_indexable": {
            "success": True,
            "total_non_indexable": random.randint(0, 2),
            "pages": []
        },
        # Add the additional data fields for slides (mock versions)
        "domain_rank": {
            "se_type": "google",
            "metrics": {
                "organic": {
                    "pos_1": random.randint(5, 50),
                    "pos_2_3": random.randint(10, 80),
                    "pos_4_10": random.randint(50, 200),
                    "pos_11_20": random.randint(100, 300),
                    "etv": random.randint(1000, 15000),
                    "count": random.randint(500, 3000),
                }
            }
        },
        "backlinks_summary": {},  # Empty - requires separate subscription
        "organic_keywords": [
            {
                "keyword_data": {
                    "keyword": "sample keyword 1",
                    "keyword_info": {
                        "search_volume": random.randint(100, 1000),
                        "cpc": round(random.uniform(0.5, 3.0), 2),
                        "competition_level": "LOW"
                    }
                },
                "ranked_serp_element": {
                    "serp_item": {
                        "url": f"https://{domain}/page1",
                        "rank_absolute": random.randint(1, 20)
                    }
                }
            },
            {
                "keyword_data": {
                    "keyword": "sample keyword 2",
                    "keyword_info": {
                        "search_volume": random.randint(50, 500),
                        "cpc": round(random.uniform(0.2, 2.0), 2),
                        "competition_level": "MEDIUM"
                    }
                },
                "ranked_serp_element": {
                    "serp_item": {
                        "url": f"https://{domain}/page2",
                        "rank_absolute": random.randint(5, 30)
                    }
                }
            }
        ],
        "referring_domains": []  # Empty - requires separate subscription
    }



def fetch_domain_metrics(domain: str) -> Dict[str, Any]:
    """
    Fetch domain-level metrics including TOTAL keyword count and TOTAL traffic.
    Uses DataForSEO Domain Rank Overview API.
    
    This gives accurate totals even for sites ranking for 10,000+ keywords,
    unlike fetch_ranked_keywords which is limited to 1000 keywords.
    
    Args:
        domain: The domain to analyze
        
    Returns:
        Dict with total_keywords, total_traffic, and other domain metrics
    """
    endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/domain_rank_overview/live"
    
    payload = [{
        "target": domain,
        "location_code": 2356,  # India
        "language_code": "en"
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            
            # Get metrics from the first item (organic search)
            metrics = {}
            for item in items:
                if item.get('se_type') == 'organic':
                    metrics = item.get('metrics', {})
                    break
            
            # If no organic found, try first item
            if not metrics and items:
                metrics = items[0].get('metrics', {})
            
            # Extract key metrics
            total_keywords = metrics.get('organic', {}).get('count', 0) or 0
            total_traffic = metrics.get('organic', {}).get('etv', 0) or 0  # Estimated Traffic Value
            paid_keywords = metrics.get('paid', {}).get('count', 0) or 0
            paid_traffic = metrics.get('paid', {}).get('etv', 0) or 0
            
            # Also get position distribution if available
            pos_distribution = metrics.get('organic', {}).get('pos_1', 0) or 0
            
            print(f"DEBUG domain_metrics: domain={domain}, total_keywords={total_keywords}, total_traffic={total_traffic}", flush=True)
            
            return {
                "success": True,
                "total_keywords": int(total_keywords),
                "total_traffic": int(total_traffic),
                "paid_keywords": int(paid_keywords),
                "paid_traffic": int(paid_traffic),
                "top_1_keywords": metrics.get('organic', {}).get('pos_1', 0) or 0,
                "top_3_keywords": (metrics.get('organic', {}).get('pos_1', 0) or 0) + 
                                 (metrics.get('organic', {}).get('pos_2_3', 0) or 0),
                "top_10_keywords": (metrics.get('organic', {}).get('pos_1', 0) or 0) + 
                                  (metrics.get('organic', {}).get('pos_2_3', 0) or 0) +
                                  (metrics.get('organic', {}).get('pos_4_10', 0) or 0),
                "raw_metrics": metrics
            }
        else:
            error_msg = data.get('status_message', 'Unknown error')
            print(f"DEBUG domain_metrics: API error - {error_msg}", flush=True)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        print(f"DEBUG domain_metrics: Exception - {e}", flush=True)
        return {"success": False, "error": str(e)}


def fetch_ranked_keywords(domain: str, limit: int = 1000) -> Dict[str, Any]:
    """
    Fetch ranked keywords for a domain from DataForSEO SERP API.
    
    Args:
        domain: The domain to analyze
        limit: Max keywords
        
    Returns:
        Dict with keywords in FULL DataForSEO format (for dashboard compatibility)
    """
    # Use DataForSEO Labs API for "Ranked Keywords"
    endpoint = f"{DATAFORSEO_API_URL}/dataforseo_labs/google/ranked_keywords/live"
    
    payload = [{
        "target": domain,
        "location_code": 2356,  # India (was 2840 US)
        "language_code": "en",
        "limit": limit,
        "include_serp_info": True  # Required to get keyword_difficulty
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            total_count = result.get('total_count', len(items))
            
            # Calculate traffic from items
            total_traffic = 0
            for item in items:
                serp = item.get('ranked_serp_element', {}).get('serp_item', {})
                total_traffic += serp.get('etv', 0) or 0
            
            # Return FULL items (not simplified) for dashboard compatibility
            # DEBUG: Log structure of first item to understand KD location
            if items:
                first_item = items[0]
                print(f"DEBUG KD: First keyword item keys: {list(first_item.keys())}", file=sys.stderr)
                if 'keyword_data' in first_item:
                    kd = first_item['keyword_data']
                    print(f"DEBUG KD: keyword_data keys: {list(kd.keys())}", file=sys.stderr)
                    if 'serp_info' in kd:
                        print(f"DEBUG KD: serp_info keys: {list(kd['serp_info'].keys())}", file=sys.stderr)
                        print(f"DEBUG KD: keyword_difficulty = {kd['serp_info'].get('keyword_difficulty')}", file=sys.stderr)
            
            return {
                "success": True,
                "total_count": total_count,
                "keywords_at_limit": len(items) >= 1000,
                "estimated_traffic": int(total_traffic),
                "keywords": items  # FULL DataForSEO format
            }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_backlinks_summary(domain: str) -> Dict[str, Any]:
    """
    Fetch backlinks summary for a domain.
    """
    endpoint = f"{DATAFORSEO_API_URL}/backlinks/summary/live"
    
    payload = [{
        "target": domain,
        "internal_list_limit": 10
    }]
    
    try:
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
             result = (data['tasks'][0].get('result') or [{}])[0] or {}
             
             return {
                 "success": True,
                 "total_backlinks": result.get('total_backlinks', 0),
                 "referring_domains": result.get('referring_domains', 0),
                 "rank": result.get('rank', 0),
                 "broken_backlinks": result.get('broken_backlinks', 0),
                 "referring_domains_nofollow": result.get('referring_domains_nofollow', 0),
                 "referring_domains_dofollow": result.get('referring_domains_dofollow', 0)
             }
        else:
            return {"success": False, "error": data.get('status_message', 'Unknown error')}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


def capture_screenshot_via_dataforseo(url: str, browser_preset: str = "desktop_chrome") -> Optional[str]:
    """
    Capture a high-quality screenshot using DataForSEO On-Page Screenshot API.
    This bypasses local headless browser issues by using DataForSEO's infrastructure.
    
    Args:
        url: URL to screenshot
        browser_preset: 'desktop_chrome', 'mobile_chrome', 'desktop_firefox', etc.
        
    Returns:
        URL of the screenshot (DataForSEO hosted) or None
    """
    endpoint = f"{DATAFORSEO_API_URL}/on_page/page_screenshot"
    
    payload = [{
        "url": url,
        "full_page_screenshot": False
    }]
    
    try:
        print(f"DEBUG: Requesting DataForSEO screenshot for {url}")
        response = requests.post(
            endpoint,
            headers={**get_auth_header(), "Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get('status_code') == 20000 and data.get('tasks'):
            result = (data['tasks'][0].get('result') or [{}])[0] or {}
            items = result.get('items') or []
            
            if items and len(items) > 0:
                image_url = items[0].get('image')
                if image_url:
                    print(f"DEBUG: DataForSEO screenshot success: {image_url}")
                    return image_url
            
            print(f"DEBUG: DataForSEO returned valid response but no image found in items: {result}")
            return None
        else:
            print(f"DEBUG: DataForSEO error: {data.get('status_message')}")
            return None
            
    except Exception as e:
        print(f"ERROR: DataForSEO screenshot failed: {e}")
        return None

if __name__ == '__main__':
    # Test the client (requires credentials in env)
    print("DataForSEO Client loaded. Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD to use.")
