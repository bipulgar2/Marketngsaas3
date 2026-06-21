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

def create_rankjacker_audit_slides(data, domain, creds=None, issue_counts=None, competitor_url=None):
    """
    Creates a new Google Slides presentation by duplicating the RankJacker template
    and replacing variables with audit data, using deep fallback logic for missing data.
    """
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
        if rank_overview and 'top_10_keywords' in rank_overview:
            page_1_count = rank_overview.get('top_10_keywords', 0)
            needs_work_count = max(0, total_keywords - page_1_count)
        else:
            metrics = rank_overview.get('metrics', {}) if rank_overview else {}
            organic_metrics = metrics.get('organic') if metrics else {}
            if organic_metrics:
                page_1_count = organic_metrics.get('pos_1', 0) + organic_metrics.get('pos_2_3', 0) + organic_metrics.get('pos_4_10', 0)
                needs_work_count = max(0, total_keywords - page_1_count)
            
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
        
    import re
    clean_domain = re.sub(r'\(.*?\)', '', domain).strip().lower()
    clean_domain = clean_domain.replace('https://', '').replace('http://', '').replace('www.', '').strip('/')
    
    if '.' not in clean_domain:
        if pages and len(pages) > 0:
            first_url = pages[0].get('url', '')
            if first_url:
                extracted = first_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
                if '.' in extracted:
                    clean_domain = extracted
    print(f"DEBUG SLIDES: Clean domain is {clean_domain}", file=sys.stderr)

    # Backlink gap vs competitor
    raw_rank = backlinks.get('rank', 0) or 0
    total_backlinks = backlinks.get('total_backlinks', backlinks.get('backlinks', 0)) or 0
    ref_domains = backlinks.get('referring_domains', 0) or 0
    
    if total_backlinks == 0 and ref_domains > 0:
        total_backlinks = ref_domains
        backlinks['total_backlinks'] = ref_domains
    
    if total_backlinks == 0:
        print(f"DEBUG SLIDES: Total backlinks is 0, attempting deep fetch for {clean_domain}", file=sys.stderr)
        try:
            from api.dataforseo_client import fetch_backlinks_summary
            bl_data = fetch_backlinks_summary(clean_domain)
            if bl_data and bl_data.get('success'):
                raw_rank = bl_data.get('rank', 0) or 0
                total_backlinks = bl_data.get('total_backlinks', 0) or 0
                ref_domains = bl_data.get('referring_domains', 0) or 0
                if total_backlinks == 0 and ref_domains > 0:
                    total_backlinks = ref_domains
                backlinks['spam_score'] = bl_data.get('spam_score', 12)
                backlinks['rank'] = raw_rank
                backlinks['total_backlinks'] = total_backlinks
        except Exception as e:
            print(f"DEBUG SLIDES: Exception fetching backlinks: {e}", file=sys.stderr)

    # DataForSEO rank is out of 1000. Ahrefs/Moz DA is out of 100. Divide by 10 for a close approximation.
    domain_authority = int(round(raw_rank / 10)) if raw_rank > 0 else 0
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
        
    # If this is a CSV import, all traffic will be 0. We must fetch true top pages on-the-fly from DataForSEO
    if sorted_pages and (sorted_pages[0].get('traffic', 0) or 0) == 0:
        print(f"DEBUG SLIDES: Traffic is 0, attempting deep fix for {domain}", file=sys.stderr)
        try:
            from api.dataforseo_client import fetch_ranked_keywords
            # clean_domain is now defined earlier
            # Fetch top 100 keywords to extract the best URLs
            res = fetch_ranked_keywords(clean_domain, limit=100)
            if res and res.get('success'):
                fetched_kws = res.get('keywords', [])
                print(f"DEBUG SLIDES: Fetched {len(fetched_kws)} keywords", file=sys.stderr)
                url_map = {}
                for kw_item in fetched_kws:
                    serp_item = kw_item.get('ranked_serp_element', {}).get('serp_item', {})
                    kw_str = kw_item.get('keyword_data', {}).get('keyword', '')
                    url = serp_item.get('url', '')
                    traffic = serp_item.get('etv', 0) or 0
                    
                    if not url or url.endswith(excluded_exts) or 'wp-content/uploads' in url:
                        continue
                        
                    if url not in url_map:
                        url_map[url] = {'url': url, 'traffic': 0, 'keywords': []}
                    url_map[url]['traffic'] += traffic
                    if kw_str:
                        url_map[url]['keywords'].append(kw_str)
                
                if url_map:
                    sorted_pages = sorted(list(url_map.values()), key=lambda x: x.get('traffic', 0), reverse=True)
                    print(f"DEBUG SLIDES: Sorted pages successfully updated, top URL is {sorted_pages[0].get('url')} with {sorted_pages[0].get('traffic')} traffic", file=sys.stderr)
                else:
                    print(f"DEBUG SLIDES: URL map was empty after processing keywords", file=sys.stderr)
            else:
                print(f"DEBUG SLIDES: fetch_ranked_keywords returned failure or None: {res}", file=sys.stderr)
        except Exception as e:
            print(f"DEBUG SLIDES: Exception in deep fix: {e}", file=sys.stderr)
            pass
            
    
    top_url_1 = sorted_pages[0].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 0 else 'N/A'
    top_traf_1 = format_number(sorted_pages[0].get('traffic', 0)) if len(sorted_pages) > 0 else '0'
    top_kw_1 = format_number(len(sorted_pages[0].get('keywords', [])) if len(sorted_pages) > 0 and isinstance(sorted_pages[0].get('keywords'), list) else 0)
    
    top_url_2 = sorted_pages[1].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 1 else 'N/A'
    top_traf_2 = format_number(sorted_pages[1].get('traffic', 0)) if len(sorted_pages) > 1 else '0'
    top_kw_2 = format_number(len(sorted_pages[1].get('keywords', [])) if len(sorted_pages) > 1 and isinstance(sorted_pages[1].get('keywords'), list) else 0)

    top_url_3 = sorted_pages[2].get('url', '').replace('https://', '').replace('http://', '').replace('www.', '') if len(sorted_pages) > 2 else 'N/A'
    top_traf_3 = format_number(sorted_pages[2].get('traffic', 0)) if len(sorted_pages) > 2 else '0'
    top_kw_3 = format_number(len(sorted_pages[2].get('keywords', [])) if len(sorted_pages) > 2 and isinstance(sorted_pages[2].get('keywords'), list) else 0)

    # Automated Competitor & Gap Analysis
    backlink_gap_multiplier = '0'
    missing_pages_count = '14'
    missing_blogs_count = '28'
    
    if clean_domain and clean_domain != 'unknown':
        try:
            import requests
            from api.dataforseo_client import get_auth_header
            # Map common 2-letter country codes to DataForSEO location codes
            country_code = data.get('location', 'US').upper()
            location_map = {
                'US': 2840,
                'CA': 2124,
                'UK': 2826,
                'GB': 2826,
                'AU': 2036,
                'IN': 2356,
                'NZ': 2554,
                'ZA': 2710
            }
            loc_code = location_map.get(country_code, 2840)
            
            # 1. Fetch Top Competitor
            if competitor_url:
                print(f"DEBUG SLIDES: Using explicit competitor URL: {competitor_url}", file=sys.stderr)
                # Clean the provided URL just in case
                top_competitor = competitor_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
                comp_total_keywords = 0 # Default to 0, we don't know without a separate API call, but we will fetch backlinks
            else:
                print(f"DEBUG SLIDES: Fetching competitor for {clean_domain} in loc {loc_code}", file=sys.stderr)
                comp_res = requests.post(
                    'https://api.dataforseo.com/v3/dataforseo_labs/google/competitors_domain/live',
                    headers={**get_auth_header(), 'Content-Type': 'application/json'},
                    json=[{'target': clean_domain, 'location_code': loc_code, 'language_code': 'en', 'limit': 15}]
                )
                
                if comp_res.status_code == 200:
                    comp_data = comp_res.json()
                    items = comp_data.get('tasks', [{}])[0].get('result', [{}])[0].get('items', [])
                    
                    # Find the first true competitor that isn't the target domain or a giant generic site
                    ignore_list = ['amazon.com', 'wikipedia.org', 'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com', 'reddit.com', 'pinterest.com', 'apple.com', 'yelp.com']
                    
                    for item in items:
                        c_domain = item.get('domain', '')
                        if c_domain and c_domain != clean_domain and c_domain not in ignore_list:
                            top_competitor = c_domain
                            comp_total_keywords = item.get('full_domain_metrics', {}).get('organic', {}).get('count', 0)
                            break
            
            if top_competitor:
                print(f"DEBUG SLIDES: Found top competitor: {top_competitor}", file=sys.stderr)
                # Content Gap Math: Assume 10% of their keywords are high-intent missing pages, up to a reasonable cap
                if comp_total_keywords > total_keywords:
                    raw_missing = comp_total_keywords - total_keywords
                    missing_pages_count = str(min(max(raw_missing // 50, 5), 150))
                    missing_blogs_count = str(min(max(raw_missing // 20, 10), 300))
                
                # 2. Fetch Backlinks for Competitor
                bl_res = requests.post(
                    'https://api.dataforseo.com/v3/backlinks/summary/live',
                    headers={**get_auth_header(), 'Content-Type': 'application/json'},
                    json=[{'target': top_competitor}]
                )
                if bl_res.status_code == 200:
                    bl_data = bl_res.json()
                    comp_backlinks = bl_data.get('tasks', [{}])[0].get('result', [{}])[0].get('backlinks', 0)
                    if comp_backlinks > 0:
                        # Avoid division by zero
                        client_bls = max(total_backlinks, 1)
                        multiplier = max(round(comp_backlinks / client_bls, 1), 1.0)
                        if multiplier == int(multiplier):
                            backlink_gap_multiplier = str(int(multiplier))
                        else:
                            backlink_gap_multiplier = str(multiplier)
                        print(f"DEBUG SLIDES: Competitor backlinks: {comp_backlinks}, Gap multiplier: {backlink_gap_multiplier}x", file=sys.stderr)
        except Exception as e:
            print(f"DEBUG SLIDES: Failed to fetch competitor data: {e}", file=sys.stderr)

    # Speed
    pagespeed_data = data.get('pagespeed', {})
    if isinstance(pagespeed_data, str):
        try:
            pagespeed_data = json.loads(pagespeed_data)
        except:
            pagespeed_data = {}
    if not pagespeed_data and domain and domain != 'unknown':
        from execution.pagespeed_insights import fetch_pagespeed_scores
        import re
        clean_domain = re.sub(r'\(.*?\)', '', domain).strip().lower()
        clean_domain = clean_domain.replace('https://', '').replace('http://', '').replace('www.', '').strip('/')
        
        if '.' not in clean_domain:
            if pages and len(pages) > 0:
                first_url = pages[0].get('url', '')
                if first_url:
                    extracted = first_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
                    if '.' in extracted:
                        clean_domain = extracted
        pagespeed_data = fetch_pagespeed_scores(f"https://{clean_domain}") or {}
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
        '{{CLIENT_WEBSITE}}': clean_domain.upper(),
        '[CLIENT WEBSITE]': clean_domain.upper(),
        'WEBSITE': clean_domain.upper(),
        '{{DATE}}': datetime.now().strftime("%B %d, %Y"),
        '{{NOT_PAGE_1_PCT}}': str(not_page_1_pct),
        '{{CRITICAL_ISSUES_COUNT}}': str(tech_issues_count),
        '{{BACKLINK_GAP}}': backlink_gap_multiplier,
        
        '{{TOTAL_KEYWORDS}}': format_number(total_keywords),
        '{{TOTAL KEYWORDS}}': format_number(total_keywords),
        '{{PAGE_1_RANKINGS}}': format_number(page_1_count),
        '{{PAGE 1 RANKINGS}}': format_number(page_1_count),
        '{{MONTHLY_TRAFFIC}}': format_number(total_traffic),
        '{{MONTHLY TRAFFIC}}': format_number(total_traffic),
        '{{DOMAIN_AUTHORITY}}': str(domain_authority),
        '{{DOMAIN AUTHORITY}}': str(domain_authority),
        
        '{{TOP_URL_1}}': top_url_1, '{{TOP_TRAF_1}}': top_traf_1, '{{TOP_KW_1}}': top_kw_1,
        '{{TOP URL 1}}': top_url_1, '{{TOP TRAF 1}}': top_traf_1, '{{TOP KW 1}}': top_kw_1,
        '{{TOP_URL_2}}': top_url_2, '{{TOP_TRAF_2}}': top_traf_2, '{{TOP_KW_2}}': top_kw_2,
        '{{TOP URL 2}}': top_url_2, '{{TOP TRAF 2}}': top_traf_2, '{{TOP KW 2}}': top_kw_2,
        '{{TOP_URL_3}}': top_url_3, '{{TOP_TRAF_3}}': top_traf_3, '{{TOP_KW_3}}': top_kw_3,
        '{{TOP URL 3}}': top_url_3, '{{TOP TRAF 3}}': top_traf_3, '{{TOP KW 3}}': top_kw_3,
        
        '{{OPPORTUNITY_KEYWORDS}}': format_number(needs_work_count),
        '{{OPPORTUNITY KEYWORDS}}': format_number(needs_work_count),
        '{{OPPORTUNITY_TRAFFIC}}': format_number(needs_work_count * 12), # Estimated potential
        '{{OPPORTUNITY TRAFFIC}}': format_number(needs_work_count * 12), # Estimated potential
        
        '{{DESKTOP_SPEED}}': desktop_perf_str,
        '{{MOBILE_SPEED}}': mobile_perf_str,
        
        # Use real word counts if we have them (from deep crawl), otherwise use a double-bound estimate.
        # This double-bounding prevents telling a 5-page site they have 50 thin pages, while also
        # preventing a spider-trap from saying they have 10,000 thin pages.
        '{{THIN_CONTENT_PAGES}}': str(len([p for p in valid_pages if 0 < (p.get('meta', {}).get('word_count') or 0) < 300]) or max(1, min(len(valid_pages) // 2, int(total_keywords * 0.15)))),
        '{{MISSING_PAGES_COUNT}}': missing_pages_count,
        '{{MISSING_BLOGS_COUNT}}': missing_blogs_count,
        '{{EXPANSION_PAGES_COUNT}}': str(max(1, min(len(valid_pages) // 3, int(total_keywords * 0.08))) if total_keywords > 0 else max(1, min(len(valid_pages) // 4, 30))),
        
        '{{TOTAL_BACKLINKS}}': format_number(total_backlinks),
        '{{SPAM_SCORE}}': str(spam_score) if total_backlinks > 0 else '0',
        '{{COMPETITOR_DOMAINS_GAP}}': backlink_gap_multiplier,
        
        '{{CRITICAL_ISSUE_1}}': 'Missing H1 Tags on Core Landing Pages' if no_h1 > 0 else 'Technical Audit Required',
        '{{CRITICAL_ISSUE_DESC_1}}': f'We found {no_h1} key pages missing an H1 tag. Without a primary heading, Google struggles to understand the topical relevance of your page, actively suppressing your ability to rank for high-intent keywords.' if no_h1 > 0 else 'A deep-dive technical audit is required to identify the root structural causes preventing your site from achieving higher organic visibility.',
        
        '{{CRITICAL_ISSUE_2}}': 'Unoptimized Meta Descriptions Across Site' if no_desc > 0 else 'Content Review Required',
        '{{CRITICAL_ISSUE_DESC_2}}': f'We identified {no_desc} pages with missing or unoptimized meta descriptions. This directly lowers your click-through rate (CTR) in the search results, meaning you are bleeding potential traffic and leads to competitors.' if no_desc > 0 else 'A comprehensive review of your content architecture is needed to ensure every page is fully optimized to capture and convert search intent.',
        
        '{{CRITICAL_ISSUE_3}}': 'Slow Mobile Page Speed Blocking Indexation' if (mobile_perf and mobile_perf < 50) else 'Severe Competitor Link Gap',
        '{{CRITICAL_ISSUE_DESC_3}}': f'Your mobile performance score is critically low ({mobile_perf_str}/100). Google operates on a mobile-first index, meaning slow load times are actively penalizing your rankings and causing potential customers to bounce.' if (mobile_perf and mobile_perf < 50) else f'Your domain is operating at a massive link equity deficit compared to the top performers in your niche. A targeted link building campaign is strictly required to close the {backlink_gap_multiplier}x gap.',
        
        '{{VISIBILITY_PROJECTION}}': '45',
        '{{TRAFFIC_PROJECTION}}': '85',
        '{{REVENUE_PROJECTION}}': '120',
        
        '{{NEXT_STEP_1}}': f'Walk through the {tech_issues_count} technical roadblocks identified and answer every question' if tech_issues_count > 0 else 'Walk through your audit findings in detail and answer every question',
        '{{NEXT_STEP_2}}': f'Present a phased SEO Growth Roadmap built specifically to capture your {format_number(needs_work_count * 12)} lost monthly traffic potential' if needs_work_count > 0 else 'Present a phased SEO Growth Roadmap built specifically for your business',
        '{{NEXT_STEP_3}}': 'Share exact timelines to resolve your structural gaps and deploy the new content architecture',
        '{{NEXT_STEP_4}}': 'Explain exactly how our Lifecycle Link Building framework builds authority and drives measurable revenue'
    }

    # Expand replacements to handle template typos (e.g. spaces inside braces)
    expanded_replacements = {}
    for tag, value in replacements.items():
        expanded_replacements[tag] = value
        if tag.startswith('{{') and tag.endswith('}}'):
            inner = tag[2:-2].strip()
            # Handle common spacing typos inside braces seen in templates
            expanded_replacements[f'{{{{ {inner} }}}}'] = value
            expanded_replacements[f'{{{{{inner} }}}}'] = value
            expanded_replacements[f'{{{{ {inner}}}}}'] = value
            expanded_replacements[f'{{{{{inner}  }}}}'] = value
            expanded_replacements[f'{{{{  {inner}  }}}}'] = value
            # Handle the specific typo seen in the screenshot: {{FOO} }
            expanded_replacements[f'{{{{{inner}}} }}'] = value
            expanded_replacements[f'{{{{{inner}}}  }}'] = value
            # Handle space before the closing braces: {{FOO }}
            expanded_replacements[f'{{{{{inner} }}}}'] = value
            expanded_replacements[f'{{{{{inner}  }}}}'] = value
            # Handle missing underscores
            if '_' in inner:
                expanded_replacements[f'{{{{{inner.replace("_", " ")}}}}}'] = value
                expanded_replacements[f'{{{{ {inner.replace("_", " ")} }}}}'] = value

    requests = []
    # Sort by length descending so longer exact matches are replaced before shorter ones if they overlap
    for tag in sorted(expanded_replacements.keys(), key=len, reverse=True):
        value = expanded_replacements[tag]
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
