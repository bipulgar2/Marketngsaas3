import os
import sys
import json
from datetime import datetime
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
import httplib2
import ssl

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# The user's Google Slides template ID
TEMPLATE_ID = '1qXzhrZRuDdyJQS9XTgfcaY8clTitvXm8Pss7h2V-rEc'

def create_rankjacker_audit_slides(data, domain, creds=None, issue_counts=None):
    if not creds:
        from api.google_auth import get_google_credentials
        creds = get_google_credentials()
    
    http = httplib2.Http(disable_ssl_certificate_validation=True)
    authorized_http = AuthorizedHttp(creds, http=http)
    
    slides_service = build('slides', 'v1', http=authorized_http)
    drive_service = build('drive', 'v3', http=authorized_http)
    
    # 1. Duplicate the template
    body = {'name': f"SEO Growth Audit - {domain}"}
    try:
        response = drive_service.files().copy(fileId=TEMPLATE_ID, body=body).execute()
        new_presentation_id = response.get('id')
    except Exception as e:
        return {'error': f"Failed to clone template: {str(e)}"}
    
    # Extract data parts
    rank_overview = data.get('domain_rank') or data.get('domain_metrics', {})
    backlinks = data.get('backlinks_summary', {})
    keywords = data.get('organic_keywords', [])
    
    raw_pages = data.get('pages')
    if isinstance(raw_pages, dict):
        pages = raw_pages.get('pages', [])
    elif isinstance(raw_pages, list):
        pages = raw_pages
    else:
        pages = []
        
    # Get traffic and keywords
    total_traffic = data.get('total_traffic', 0) or 0
    total_keywords = data.get('total_keywords', 0) or 0
    
    if total_traffic == 0 or total_keywords == 0:
        metrics = rank_overview.get('metrics', {}) if rank_overview else {}
        organic_metrics = metrics.get('organic') if metrics else {}
        organic_metrics = organic_metrics if organic_metrics else {}
        if total_traffic == 0:
            total_traffic = organic_metrics.get('etv', 0) or 0
        if total_keywords == 0:
            total_keywords = organic_metrics.get('count', 0) or 0
            
    if total_keywords == 0 and keywords:
        total_keywords = len(keywords)
    if total_traffic == 0 and keywords:
        total_traffic = sum((k.get('traffic_etv') or k.get('traffic_cost') or k.get('ranked_serp_element', {}).get('serp_item', {}).get('etv') or 0) for k in keywords)

    # Keywords not on page 1
    needs_work_count = 0
    page_1_count = 0
    for kw in keywords:
        pos = kw.get('position', 100)
        if not pos:
            serp_item = kw.get('ranked_serp_element', {}).get('serp_item', {})
            pos = serp_item.get('rank_absolute', 100)
        if pos and pos > 10 and pos <= 100:
            needs_work_count += 1
        elif pos and pos <= 10:
            page_1_count += 1
            
    # Fallback to labs api metrics if keywords list is empty
    if page_1_count == 0 and not keywords:
        metrics = rank_overview.get('metrics', {}) if rank_overview else {}
        organic_metrics = metrics.get('organic') if metrics else {}
        if organic_metrics:
            page_1_count = organic_metrics.get('pos_1', 0) + organic_metrics.get('pos_2_3', 0) + organic_metrics.get('pos_4_10', 0)
            needs_work_count = total_keywords - page_1_count
            
    not_page_1_pct = round((needs_work_count / total_keywords * 100) if total_keywords > 0 else 0)
    
    # Technical issues
    if issue_counts:
        title_too_long = issue_counts.get('titleTooLong', 0)
        no_desc = issue_counts.get('noDesc', 0)
        desc_too_long = issue_counts.get('descTooLong', 0)
        no_h1 = issue_counts.get('noH1', 0)
        dups = issue_counts.get('dupH1', 0) + issue_counts.get('dupH2', 0)
        tech_issues_count = title_too_long + no_desc + desc_too_long + no_h1 + dups
    else:
        title_too_long = sum(1 for p in pages if len(p.get('meta', {}).get('title', '')) > 65)
        no_desc = sum(1 for p in pages if not p.get('meta', {}).get('desc'))
        desc_too_long = sum(1 for p in pages if len(p.get('meta', {}).get('desc', '')) > 155)
        no_h1 = sum(1 for p in pages if not p.get('meta', {}).get('htags', {}).get('h1'))
        
        dups = 0
        for p in pages:
            h1s = p.get('meta', {}).get('htags', {}).get('h1')
            h2s = p.get('meta', {}).get('htags', {}).get('h2')
            if isinstance(h1s, list) and len(h1s) > 1:
                dups += 1
            if isinstance(h2s, list) and len(h2s) > 1:
                dups += 1
                
        tech_issues_count = title_too_long + no_desc + desc_too_long + no_h1 + dups
        
    # Backlink gap vs competitor
    domain_authority = backlinks.get('rank', 0) or 0
    total_backlinks = backlinks.get('backlinks', 0) or 0
    spam_score = backlinks.get('spam_score', 12) or 12 # Default visual
    
    # Sort top pages by traffic, filtering out non-HTML assets
    valid_pages = []
    excluded_exts = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.css', '.js', '.woff', '.woff2', '.pdf', '.json', '.xml')
    for p in pages:
        url = p.get('url', '').lower()
        if not url.endswith(excluded_exts) and 'wp-content/uploads' not in url:
            valid_pages.append(p)
            
    sorted_pages = sorted(valid_pages, key=lambda x: x.get('traffic', 0) or 0, reverse=True)
    if not sorted_pages:
        sorted_pages = pages # fallback just in case
    
    top_url_1 = sorted_pages[0].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 0 else 'N/A'
    top_traf_1 = format_number(sorted_pages[0].get('traffic', 0)) if len(sorted_pages) > 0 else '0'
    top_kw_1 = format_number(len(sorted_pages[0].get('keywords', [])) if len(sorted_pages) > 0 and isinstance(sorted_pages[0].get('keywords'), list) else 0)
    
    top_url_2 = sorted_pages[1].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 1 else 'N/A'
    top_traf_2 = format_number(sorted_pages[1].get('traffic', 0)) if len(sorted_pages) > 1 else '0'
    top_kw_2 = format_number(len(sorted_pages[1].get('keywords', [])) if len(sorted_pages) > 1 and isinstance(sorted_pages[1].get('keywords'), list) else 0)

    top_url_3 = sorted_pages[2].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 2 else 'N/A'
    top_traf_3 = format_number(sorted_pages[2].get('traffic', 0)) if len(sorted_pages) > 2 else '0'
    top_kw_3 = format_number(len(sorted_pages[2].get('keywords', [])) if len(sorted_pages) > 2 and isinstance(sorted_pages[2].get('keywords'), list) else 0)

    # Speed
    pagespeed_data = data.get('pagespeed', {})
    if isinstance(pagespeed_data, str):
        try:
            import json
            pagespeed_data = json.loads(pagespeed_data)
        except:
            pagespeed_data = {}
    mobile_ps = pagespeed_data.get('mobile', {})
    desktop_ps = pagespeed_data.get('desktop', {})
    mobile_scores = mobile_ps.get('scores', pagespeed_data.get('scores', {}))
    desktop_scores = desktop_ps.get('scores', {})
    mobile_perf = mobile_scores.get('performance')
    desktop_perf = desktop_scores.get('performance')
    
    mobile_perf_str = str(mobile_perf) if mobile_perf else 'N/A'
    desktop_perf_str = str(desktop_perf) if desktop_perf else 'N/A'
    
    # 2. Build the replace requests
    replacements = {
        '{{CLIENT_WEBSITE}}': domain.upper(),
        '[CLIENT WEBSITE]': domain.upper(),
        'WEBSITE': domain.upper(),
        '{{DATE}}': datetime.now().strftime("%B %d, %Y"),
        '{{NOT_PAGE_1_PCT}}': str(not_page_1_pct),
        '{{CRITICAL_ISSUES_COUNT}}': str(tech_issues_count),
        '{{BACKLINK_GAP}}': '0', # Hardcoded or dynamic if you have competitor
        
        '{{TOTAL_KEYWORDS}}': format_number(total_keywords),
        '{{PAGE_1_RANKINGS}}': format_number(page_1_count),
        '{{MONTHLY_TRAFFIC}}': format_number(total_traffic),
        '{{DOMAIN_AUTHORITY}}': str(domain_authority),
        
        '{{TOP_URL_1}}': top_url_1, '{{TOP_TRAF_1}}': top_traf_1, '{{TOP_KW_1}}': top_kw_1,
        '{{TOP_URL_2}}': top_url_2, '{{TOP_TRAF_2}}': top_traf_2, '{{TOP_KW_2}}': top_kw_2,
        '{{TOP_URL_3}}': top_url_3, '{{TOP_TRAF_3}}': top_traf_3, '{{TOP_KW_3}}': top_kw_3,
        
        '{{OPPORTUNITY_KEYWORDS}}': format_number(needs_work_count),
        '{{OPPORTUNITY_TRAFFIC}}': format_number(needs_work_count * 12), # Estimated potential
        
        '{{DESKTOP_SPEED}}': desktop_perf_str,
        '{{MOBILE_SPEED}}': mobile_perf_str,
        
        '{{THIN_CONTENT_PAGES}}': str(len([p for p in pages if (p.get('meta', {}).get('word_count') or 0) < 300])),
        '{{MISSING_PAGES_COUNT}}': '14', # Static sales estimate if no keyword gap API is present
        '{{MISSING_BLOGS_COUNT}}': '28', # Static sales estimate if no keyword gap API is present
        '{{EXPANSION_PAGES_COUNT}}': str(len(pages) // 3),
        
        '{{TOTAL_BACKLINKS}}': format_number(total_backlinks),
        '{{SPAM_SCORE}}': str(spam_score) if total_backlinks > 0 else '0',
        '{{COMPETITOR_DOMAINS_GAP}}': '0',
        
        '{{CRITICAL_ISSUE_1}}': 'Missing H1 Tags on Core Landing Pages' if no_h1 > 0 else 'Technical Audit Required',
        '{{CRITICAL_ISSUE_2}}': 'Unoptimized Meta Descriptions Across Site' if no_desc > 0 else 'Content Review Required',
        '{{CRITICAL_ISSUE_3}}': 'Slow Mobile Page Speed Blocking Indexation' if (mobile_perf and mobile_perf < 50) else 'Backlink Analysis Required',
        
        '{{VISIBILITY_PROJECTION}}': '45',
        '{{TRAFFIC_PROJECTION}}': '85',
        '{{REVENUE_PROJECTION}}': '120'
    }

    requests = []
    for tag, value in replacements.items():
        requests.append({
            'replaceAllText': {
                'containsText': {
                    'text': tag,
                    'matchCase': True
                },
                'replaceText': str(value)
            }
        })
        
    # Execute text replacements
    try:
        if requests:
            slides_service.presentations().batchUpdate(
                presentationId=new_presentation_id, 
                body={'requests': requests}
            ).execute()
    except Exception as e:
        print(f"Error replacing text: {str(e)}")

    # PERMISSIONS
    drive_service.permissions().create(fileId=new_presentation_id, body={'type': 'anyone', 'role': 'reader'}).execute()
    
    return {
        "presentation_id": new_presentation_id,
        "presentation_url": f"https://docs.google.com/presentation/d/{new_presentation_id}/edit"
    }

def format_number(n):
    if n is None:
        return '0'
    try:
        n = float(n)
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        elif n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(int(n))
    except:
        return str(n)
