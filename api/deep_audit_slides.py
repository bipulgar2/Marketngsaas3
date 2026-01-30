"""
Deep Audit Slides Generator (V2)
Generates a comprehensive 29-slide SEO presentation matching the "Nine Ventures" format.
"""
import os
import sys
import json
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
import httplib2
import ssl

# Force unverified SSL context
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Extended Color Scheme
# Nine Ventures Color Scheme
COLORS = {
    'primary': {'red': 51/255, 'green': 102/255, 'blue': 204/255},      # #3366cc
    'dark_blue': {'red': 30/255, 'green': 64/255, 'blue': 153/255},     # #1e4099
    'white': {'red': 1, 'green': 1, 'blue': 1},
    'dark': {'red': 26/255, 'green': 43/255, 'blue': 72/255},           # #1a2b48
    'red': {'red': 204/255, 'green': 51/255, 'blue': 51/255},           # #cc3333
    'orange': {'red': 234/255, 'green': 134/255, 'blue': 45/255},       # #ea862d
    'green': {'red': 46/255, 'green': 139/255, 'blue': 87/255},         # #2e8b57
    'gray': {'red': 128/255, 'green': 128/255, 'blue': 128/255},        # #808080
    'blue': {'red': 51/255, 'green': 102/255, 'blue': 204/255},         # Alias for primary
    'error': {'red': 204/255, 'green': 51/255, 'blue': 51/255},         # Alias for red
    'warning': {'red': 234/255, 'green': 134/255, 'blue': 45/255},      # Alias for orange
    'success': {'red': 46/255, 'green': 139/255, 'blue': 87/255},       # Alias for green
    'yellow': {'red': 255/255, 'green': 193/255, 'blue': 7/255},        # Amber
    'light_blue': {'red': 128/255, 'green': 222/255, 'blue': 234/255},  # Cyan 200
    'light_gray': {'red': 245/255, 'green': 245/255, 'blue': 245/255},  # #f5f5f5 - Light gray background
    'blue_accent': {'red': 66/255, 'green': 133/255, 'blue': 244/255}   # #4285f4 - Google Blue
}


SCARE_CONTENT = {
    'organic': {
        'title': 'WHY ORGANIC VISIBILITY MATTERS',
        'body': 'Your website is often the first impression potential customers have of your business. When users search for solutions you offer, showing up on Google\'s first page is about more than just traffic. It\'s about credibility and trust. A strong content strategy directly builds your domain authority, signaling to search engines that your site is a reliable source of information. Without this authority, even the best products struggle to reach their audience.\n\nEqually important is your site architecture. A well-structured website helps Google\'s crawlers understand and index your pages efficiently. Poor architecture leads to orphaned pages, broken internal links, and missed ranking opportunities. When competitors have cleaner structures, they capture the market share you\'re leaving on the table.',
        'stat': ''
    },
    'meta': {
        'title': 'THE COST OF POOR META TAGS',
        'body': '• Meta titles and descriptions are your digital shop window to the world.\n• They are the very first interaction a potential user has with your brand.\n• Poor tags lead to low click-through rates regardless of your search rank.\n• Clear metadata helps search engines accurately categorize your pages.\n• Optimization can increase organic traffic by 30% without more content.',
        'stat': 'Optimized meta tags can increase Click-Through Rate (CTR) by up to 30%.'
    },
    'headings': {
        'title': 'HEADINGS STRUCTURE & RANKING',
        'body': '• Headings establish content hierarchy for both users and search robots.\n• Missing H1 tags make it nearly impossible for Google to identify topics.\n• Structured subheadings (H2, H3) keep readers engaged and scrolling.\n• Broken hierarchy causes "pogo-sticking" which hurts your rankings.\n• Proper heading usage is a primary signal for search engine crawlers.',
        'stat': 'PROPER H1-H6 USE IS A TOP 5 RANKING FACTOR.'
    },
    'backlinks': {
        'title': 'AUTHORITY & TRUST',
        'body': '• Backlinks act as digital "votes of confidence" for your domain.\n• Domain authority is driven primarily by the quality of external links.\n• High-quality, relevant links are a top three Google ranking factor.\n• Spammy or toxic backlinks can trigger severe algorithmic penalties.\n• A strong Link profile makes it easier for new content to rank fast.',
        'stat': 'The #1 Result in Google has 3.8x more backlinks than positions 2-10.'
    },
    'speed': {
        'title': 'SLOW SPEED KILLS CONVERSIONS',
        'body': '• Website speed is a direct ranking factor in the mobile-first era.\n• Users expect pages to load in under 2 seconds or they will leave.\n• High bounce rates from slow speeds tell Google your site is poor.\n• Slow performance kills conversion rates on desktops and mobiles.\n• Faster sites enjoy better crawl budgets and more frequent updates.',
        'stat': '53% of visits are abandoned if a mobile site takes >3 seconds to load.'
    },
    'content': {
        'title': 'CONTENT & READABILITY',
        'body': '• Thin or low-quality content is the fastest way to lose rankings.\n• Google prioritizes pages that provide genuine value and clarity.\n• Complex jargon drives users away and signals poor UX to crawlers.\n• High dwell time is a key metric for sustained search performance.\n• Simple and readable content ensures users find the answers they seek.',
        'stat': 'The average reading level of a #1 ranking page is Grade 8.'
    }
}


# Dynamic Annotation Helpers - generate context-aware annotations based on metrics
def get_traffic_annotation(traffic):
    """Returns annotation based on traffic level."""
    traffic = traffic or 0
    if traffic >= 50000:
        return "✅ Strong Organic Traffic"
    elif traffic >= 20000:
        return "📈 Good Traffic Growth"
    else:
        return "🔴 Low Organic Traffic"

def get_traffic_annotation_with_needs_work(traffic, needs_work_count=0):
    """Returns annotation based on traffic and keywords that need work.
    - < 20k traffic: Low Organic Traffic
    - >= 20k traffic AND needs_work > 20: Many keywords need work
    - >= 20k traffic AND needs_work <= 20: Strong Organic Traffic
    """
    traffic = traffic or 0
    needs_work_count = needs_work_count or 0
    
    if traffic < 20000:
        return "🔴 Low Organic Traffic"
    elif needs_work_count > 20:
        return "📈 Many keywords need work"
    else:
        return "✅ Strong Organic Traffic"

def get_keywords_annotation(count, needs_work_count=0):
    """Returns annotation based on keyword count and ranking quality.
    Focus on opportunity/potential rather than current status.
    """
    count = count or 0
    needs_work_count = needs_work_count or 0
    
    # If significant keywords or needs work, show potential
    if needs_work_count > 50 or count >= 100:
        return "📈 Has potential for more visitors"
    else:
        return "⚠️ Limited Keywords"

def get_backlinks_annotation(referring_domains, high_spam_count=0):
    """Returns annotation based on referring domains."""
    referring_domains = referring_domains or 0
    
    # Less than 100 domains = needs more links
    if referring_domains < 100:
        return "⚠️ Needs Link Building"
    else:
        # >= 100 domains: focus on spam as the improvement area
        return "🔴 Many High Spam Backlinks"


def get_speed_annotation(score):
    """Returns annotation based on PageSpeed score. Poor only if < 90."""
    score = score or 0
    if score >= 90:
        return "✅ Excellent Speed"
    elif score >= 50:
        return "⚠️ Needs Optimization"
    else:
        return "🔴 Poor Performance"

def get_readability_annotation(grade):
    """Returns annotation based on readability grade."""
    grade = grade or 0
    if grade > 9:
        return "🔴 Poor Page Readability"
    else:
        return "✅ Content Readability is Apt"

def get_issues_annotation(issue_count, issue_type):
    """Returns annotation based on issue count."""
    if issue_count == 0:
        return f"✅ No {issue_type} Issues"
    elif issue_count <= 5:
        return f"⚠️ {issue_count} {issue_type} Issues"
    else:
        return f"🔴 {issue_count}+ {issue_type} Issues"

# Formatting helpers
def format_number(n):
    """Format large numbers: 18500 -> 18.5K"""
    if n is None:
        return '0'
    n = float(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(int(n))

def format_currency(n):
    """Format currency: 0.2199999 -> $0.22"""
    if n is None:
        return '$0.00'
    return f"${float(n):.2f}"

def create_deep_audit_slides(data, domain, creds=None, screenshots=None, annotations=None, issue_counts=None):
    """
    Creates the specific 29-slide deck using full_audit_data.
    annotations: Optional dict with Gemini-generated short annotations for each slide.
    issue_counts: Optional dict with frontend-calculated issue counts for accurate slide bullets.
    """
    if not creds:
        from api.google_auth import get_google_credentials
        creds = get_google_credentials()
    
    http = httplib2.Http(disable_ssl_certificate_validation=True)
    authorized_http = AuthorizedHttp(creds, http=http)
    
    slides_service = build('slides', 'v1', http=authorized_http)
    drive_service = build('drive', 'v3', http=authorized_http)
    
    # Extract data parts
    rank_overview = data.get('domain_rank', {})
    backlinks = data.get('backlinks_summary', {})
    keywords = data.get('organic_keywords', [])
    spammy_links = data.get('referring_domains', [])
    raw_pages = data.get('pages')
    if isinstance(raw_pages, dict):
        pages = raw_pages.get('pages', [])
    elif isinstance(raw_pages, list):
        pages = raw_pages
    else:
        pages = []
    raw_summary = data.get('summary', {})
    if isinstance(raw_summary, str):
        try:
            raw_summary = json.loads(raw_summary)
        except:
            raw_summary = {}
    summary = raw_summary.get('summary', {}) if isinstance(raw_summary, dict) else {}

    # Create presentation
    presentation_title = f"SEO Strategy Deck - {domain}"
    folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

    if folder_id:
        # Create blank presentation in the specific folder via Drive API
        print(f"Creating presentation in folder: {folder_id}")
        file_metadata = {
            'name': presentation_title,
            'mimeType': 'application/vnd.google-apps.presentation',
            'parents': [folder_id]
        }
        file = drive_service.files().create(body=file_metadata, fields='id').execute()
        pid = file.get('id')
        
        # Get the presentation object via Slides API to continue working with it
        presentation = slides_service.presentations().get(presentationId=pid).execute()
    else:
        # Fallback to default behavior (might fail if SA has no quota)
        presentation = {'title': presentation_title}
        presentation = slides_service.presentations().create(body=presentation).execute()
        pid = presentation.get('presentationId')
    
    requests = []
    
    # helper to clean blank slide
    if len(presentation.get('slides', [])) > 0:
        requests.append({'deleteObject': {'objectId': presentation['slides'][0]['objectId']}})
    
    # DEBUG: Log what data we received
    print(f"DEBUG SLIDES: rank_overview keys: {list(rank_overview.keys()) if rank_overview else 'EMPTY'}", file=sys.stderr)
    print(f"DEBUG SLIDES: keywords count: {len(keywords)}", file=sys.stderr)
    print(f"DEBUG SLIDES: pages count: {len(pages)}", file=sys.stderr)
    print(f"DEBUG SLIDES: backlinks keys: {list(backlinks.keys()) if backlinks else 'EMPTY'}", file=sys.stderr)
    
    # Get traffic and keywords DIRECTLY from data (new correct path)
    # First try direct fields (from our fixed API), then fallback to rank_overview
    total_traffic = data.get('total_traffic', 0) or 0
    total_keywords = data.get('total_keywords', 0) or 0
    
    # Fallback to rank_overview.metrics.organic if direct fields are 0
    if total_traffic == 0 or total_keywords == 0:
        metrics = rank_overview.get('metrics', {}) if rank_overview else {}
        organic_metrics = metrics.get('organic') if metrics else {}
        organic_metrics = organic_metrics if organic_metrics else {}
        if total_traffic == 0:
            total_traffic = organic_metrics.get('etv', 0) or 0
        if total_keywords == 0:
            total_keywords = organic_metrics.get('count', 0) or 0
    
    # Final fallback: use keyword list length if still 0
    if total_keywords == 0 and keywords:
        total_keywords = len(keywords)
    
    print(f"DEBUG SLIDES: total_traffic = {total_traffic}, total_keywords = {total_keywords}", file=sys.stderr)
    
    # Get position breakdown from rank_overview if available
    metrics = rank_overview.get('metrics', {}) if rank_overview else {}
    organic_metrics = metrics.get('organic') if metrics else {}
    organic_metrics = organic_metrics if organic_metrics else {}
    pos_1 = organic_metrics.get('pos_1', 0) or 0
    pos_2_3 = organic_metrics.get('pos_2_3', 0) or 0
    pos_4_10 = organic_metrics.get('pos_4_10', 0) or 0
    top_10_count = pos_1 + pos_2_3 + pos_4_10
    
    # Calculate needs_work count (keywords ranking 21+)
    needs_work_count = 0
    for kw in keywords:
        pos = kw.get('position', 100)
        if not pos:
            serp_item = kw.get('ranked_serp_element', {}).get('serp_item', {})
            pos = serp_item.get('rank_absolute', 100)
        if pos and pos > 20 and pos <= 100:
            needs_work_count += 1
    
    # Calculate high spam backlink count for backlink annotation
    referring_domains = backlinks.get('referring_domains', 0) or 0
    high_spam_count = 0  # Would need to be passed from frontend or calculated from data
    
    # Setup annotations (use Gemini-generated or defaults)
    if not annotations:
        annotations = {}
    
    # --- SLIDES GENERATION ---
    
    # 1. Cover
    requests.extend(create_slide_cover(generate_id(), domain))
    
    # 2. Homepage Snapshot (Full-screen website preview)
    if screenshots and screenshots.get('homepage'):
        requests.extend(create_slide_homepage_snapshot(generate_id(), screenshots['homepage']))
    
    # --- SECTION 1: ORGANIC TRAFFIC & KEYWORDS ---
    
    # Explainer: Organic Visibility
    sc = SCARE_CONTENT['organic']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: SEO Overview (Traffic/DR) - NO ANNOTATION BOX per user request
    if screenshots and screenshots.get('traffic_overview'):
        requests.extend(create_slide_image(generate_id(), "SEO OVERVIEW", screenshots['traffic_overview'], None))
    else:
        requests.extend(create_slide_traffic_dashboard(generate_id(), rank_overview, backlinks, domain, keywords))

    # Slide: Organic Keywords Report OR Table
    if screenshots and screenshots.get('keywords_report'):
        kw_annotation = annotations.get('keywords_report', get_keywords_annotation(total_keywords, needs_work_count))
        requests.extend(create_slide_image(generate_id(), "ORGANIC KEYWORDS", screenshots['keywords_report'], kw_annotation))
    else:
        requests.extend(create_slide_kw_table(generate_id(), keywords[:7]))
    
    # --- SECTION 2: META ISSUES ---
    
    # Explainer: Meta Issues
    sc = SCARE_CONTENT['meta']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: Meta Issues Screenshot + Bullets
    if screenshots and screenshots.get('meta_issues'):
        # Calculate counts directly from page metadata (robust fallback)
        print(f"DEBUG SLIDES META: issue_counts={issue_counts}, pages count={len(pages)}", file=sys.stderr)
        if issue_counts:
            title_too_long_count = issue_counts.get('titleTooLong', 0)
            missing_desc_count = issue_counts.get('noDesc', 0)
            desc_too_long_count = issue_counts.get('descTooLong', 0)
            print(f"DEBUG SLIDES META: Using issue_counts - title={title_too_long_count}, desc_missing={missing_desc_count}, desc_long={desc_too_long_count}", file=sys.stderr)
        else:
            title_too_long_count = 0
            missing_desc_count = 0
            desc_too_long_count = 0
            
            for p in pages:
                meta = p.get('meta', {}) if isinstance(p.get('meta'), dict) else {}
                title = meta.get('title') or p.get('title') or ''
                desc = meta.get('description') or p.get('description') or ''
                
                if len(title) > 60: title_too_long_count += 1
                if not desc or len(desc) < 5: missing_desc_count += 1
                elif len(desc) > 160: desc_too_long_count += 1
    
        meta_bullets = []
        if title_too_long_count > 0: meta_bullets.append(f"{title_too_long_count} {'page' if title_too_long_count == 1 else 'pages'} with titles too long")
        if missing_desc_count > 0: meta_bullets.append(f"{missing_desc_count} {'page' if missing_desc_count == 1 else 'pages'} missing description")
        if desc_too_long_count > 0: meta_bullets.append(f"{desc_too_long_count} {'page' if desc_too_long_count == 1 else 'pages'} with description too long")
        
        if not meta_bullets: meta_bullets = ["No major meta issues found"]
        
        requests.extend(create_slide_image_with_bullets(generate_id(), "META ISSUES", screenshots['meta_issues'], meta_bullets[:6]))
    else:
        # Fallback table
        bad_meta = [p for p in pages if p.get('issues', {}).get('title_too_long')][:5]
        requests.extend(create_slide_issue_table(generate_id(), "Meta Title Issues", bad_meta, "Title > 60 chars", "title_too_long"))

    # --- SECTION 3: HEADING ISSUES ---

    # Explainer: Headings
    sc = SCARE_CONTENT['headings']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: Heading Issues Screenshot + Bullets
    if screenshots and screenshots.get('heading_issues'):
        if issue_counts:
             no_h1_count = issue_counts.get('noH1', 0)
             multi_h1_count = issue_counts.get('multiH1', 0)
             no_h2_count = issue_counts.get('noH2', 0)
             many_h2_count = issue_counts.get('manyH2', 0)
             no_h3_count = issue_counts.get('noH3', 0)
             many_h3_count = issue_counts.get('manyH3', 0)
             dup_h1_count = issue_counts.get('dupH1', 0)
             dup_h2_count = issue_counts.get('dupH2', 0)
             dup_h3_count = issue_counts.get('dupH3', 0)
        else:
             no_h1_count = 0
             multi_h1_count = 0
             no_h2_count = 0
             many_h2_count = 0
             no_h3_count = 0
             many_h3_count = 0
             dup_h1_count = 0
             dup_h2_count = 0
             dup_h3_count = 0
             
             h1_map, h2_map, h3_map = {}, {}, {}
             
             for p in pages:
                 meta = p.get('meta', {}) if isinstance(p.get('meta'), dict) else {}
                 h1_list = meta.get('h1') or p.get('h1') or []
                 h2_list = meta.get('h2') or p.get('h2') or []
                 h3_list = meta.get('h3') or p.get('h3') or []
                 
                 h1_cnt = len(h1_list) if isinstance(h1_list, list) else (1 if h1_list else 0)
                 h2_cnt = meta.get('h2_count') or p.get('h2_count') or (len(h2_list) if isinstance(h2_list, list) else 0)
                 h3_cnt = meta.get('h3_count') or p.get('h3_count') or (len(h3_list) if isinstance(h3_list, list) else 0)
                 
                 if h1_cnt == 0: no_h1_count += 1
                 if h1_cnt > 1: multi_h1_count += 1
                 if h2_cnt == 0: no_h2_count += 1
                 if h2_cnt > 10: many_h2_count += 1
                 if h3_cnt == 0: no_h3_count += 1
                 if h3_cnt > 15: many_h3_count += 1
                 
                 # Duplicate tracking (simplified version of JS logic)
                 for tag_list, tag_map in [(h1_list, h1_map), (h2_list, h2_map), (h3_list, h3_map)]:
                     if isinstance(tag_list, list):
                         for val in tag_list:
                             key = str(val).lower().strip()
                             if len(key) > 3:
                                 tag_map[key] = tag_map.get(key, 0) + 1
             
             dup_h1_count = len([k for k, v in h1_map.items() if v > 1])
             dup_h2_count = len([k for k, v in h2_map.items() if v > 1])
             dup_h3_count = len([k for k, v in h3_map.items() if v > 1])

        heading_bullets = []
        if no_h1_count > 0: heading_bullets.append(f"{no_h1_count} {'page' if no_h1_count == 1 else 'pages'} missing H1")
        if multi_h1_count > 0: heading_bullets.append(f"{multi_h1_count} {'page' if multi_h1_count == 1 else 'pages'} with multiple H1s")
        if dup_h1_count > 0: heading_bullets.append(f"{dup_h1_count} duplicate H1 {'heading' if dup_h1_count == 1 else 'headings'} found")
        if no_h2_count > 0: heading_bullets.append(f"{no_h2_count} {'page' if no_h2_count == 1 else 'pages'} missing H2")
        if many_h2_count > 0: heading_bullets.append(f"{many_h2_count} {'page' if many_h2_count == 1 else 'pages'} with too many H2")
        if dup_h2_count > 0: heading_bullets.append(f"{dup_h2_count} duplicate H2 {'heading' if dup_h2_count == 1 else 'headings'} found")
        if no_h3_count > 0: heading_bullets.append(f"{no_h3_count} {'page' if no_h3_count == 1 else 'pages'} missing H3")
        if many_h3_count > 0: heading_bullets.append(f"{many_h3_count} {'page' if many_h3_count == 1 else 'pages'} with too many H3")
        if dup_h3_count > 0: heading_bullets.append(f"{dup_h3_count} duplicate H3 {'heading' if dup_h3_count == 1 else 'headings'} found")
        
        if not heading_bullets: heading_bullets = ["No major heading issues found"]
        
        requests.extend(create_slide_image_with_bullets(generate_id(), "HEADING ISSUES", screenshots['heading_issues'], heading_bullets[:8]))
    
    # --- SECTION 4: BACKLINKS PROFILE ---

    # Explainer: Backlinks
    sc = SCARE_CONTENT['backlinks']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: Backlinks
    if screenshots and screenshots.get('backlinks'):
        backlinks_annotation = annotations.get('backlinks', get_backlinks_annotation(referring_domains, high_spam_count))
        requests.extend(create_slide_image(generate_id(), "BACKLINK PROFILE", screenshots['backlinks'], backlinks_annotation))

    # --- SECTION 5: CONTENT READABILITY ---
    
    # Explainer: Content
    sc = SCARE_CONTENT['content']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: Content Readability
    if screenshots and screenshots.get('content_readability'):
        # Get average readability grade from data if available
        readability_results = data.get('readability_results', [])
        print(f"DEBUG SLIDES: readability_results type: {type(readability_results)}, len: {len(readability_results) if isinstance(readability_results, list) else 'N/A'}", file=sys.stderr)
        avg_grade = 0
        if readability_results:
            grades = [r.get('flesch_kincaid_grade', 0) for r in readability_results if isinstance(r, dict)]
            print(f"DEBUG SLIDES: grades extracted: {grades}", file=sys.stderr)
            avg_grade = sum(grades) / len(grades) if grades else 0
        print(f"DEBUG SLIDES: avg_grade = {avg_grade}, annotation = {get_readability_annotation(avg_grade)}", file=sys.stderr)
        content_annotation = get_readability_annotation(avg_grade)
        requests.extend(create_slide_image(generate_id(), "CONTENT ANALYSIS", screenshots['content_readability'], content_annotation))

    # --- SECTION 6: WEBSITE SPEED ---

    # Explainer: Speed
    sc = SCARE_CONTENT['speed']
    requests.extend(create_slide_scare_explainer(generate_id(), sc['title'], sc['body'], sc['stat']))

    # Slide: Website Speed
    if screenshots and screenshots.get('speed_analysis'):
        # Get actual performance score from pagespeed data
        pagespeed_data = data.get('pagespeed', {})
        if isinstance(pagespeed_data, str):
            try:
                import json
                pagespeed_data = json.loads(pagespeed_data)
            except:
                pagespeed_data = {}
        scores = pagespeed_data.get('scores', {})
        performance_score = scores.get('performance', 0) or 0
        speed_annotation = annotations.get('speed_analysis', get_speed_annotation(performance_score))
        requests.extend(create_slide_image(generate_id(), "WEBSITE SPEED", screenshots['speed_analysis'], speed_annotation))
    else:
        avg_load_time = sum(p.get('load_time', 0) for p in pages) / max(1, len(pages)) if pages else 0
        requests.extend(create_slide_speed(generate_id(), avg_load_time))


    # 25. Thank You
    requests.extend(create_slide_thank_you(generate_id()))

    # BATCH EXECUTE
    # chunk requests to avoid payload limits (though 29 slides is fine)
    print(f"Generating {len(requests)} requests for 29 slides...")
    slides_service.presentations().batchUpdate(presentationId=pid, body={'requests': requests}).execute()
    
    # PERMISSIONS
    drive_service.permissions().create(fileId=pid, body={'type': 'anyone', 'role': 'reader'}).execute()
    
    return {
        "presentation_id": pid,
        "presentation_url": f"https://docs.google.com/presentation/d/{pid}/edit"
    }

# --- HELPER FUNCTIONS ---
def generate_id():
    return f"slide_{os.urandom(8).hex()}"
    
def create_slide_image(sid, title, image_url, annotation=""):
    """Creates a slide with a screenshot image - no white card overlay (screenshot has its own background)."""
    reqs = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Blue header bar at top
        {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}}, 'fields': 'pageBackgroundFill'}},
        # Title
        {'createShape': {'objectId': f"{sid}_t", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 15, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_t", 'text': title}},
        {'updateTextStyle': {'objectId': f"{sid}_t", 'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
    ]
    
    # Image positioned to fit within slide bounds (no white card - screenshot already has background)
    reqs.append({
        'createImage': {
            'objectId': f"{sid}_img",
            'url': image_url,
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 320, 'unit': 'PT'}, 'width': {'magnitude': 680, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 20, 'translateY': 75, 'unit': 'PT'}
            }
        }
    })
    
    # Add annotation box if provided (top-right, narrower and taller to avoid header overlap)
    if annotation:
        reqs.append({
            'createShape': {
                'objectId': f"{sid}_ann_bg",
                'shapeType': 'ROUND_RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 42, 'unit': 'PT'}, 'width': {'magnitude': 160, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 540, 'translateY': 10, 'unit': 'PT'}
                }
            }
        })
        reqs.append({
            'updateShapeProperties': {
                'objectId': f"{sid}_ann_bg",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['warning']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        })
        reqs.append({'insertText': {'objectId': f"{sid}_ann_bg", 'text': annotation}})
        reqs.append({
            'updateTextStyle': {
                'objectId': f"{sid}_ann_bg",
                'style': {
                    'fontSize': {'magnitude': 12, 'unit': 'PT'},
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}},
                    'bold': True
                },
                'fields': 'fontSize,foregroundColor,bold'
            }
        })
        reqs.append({
            'updateParagraphStyle': {
                'objectId': f"{sid}_ann_bg",
                'style': {'alignment': 'CENTER'},
                'fields': 'alignment'
            }
        })
    
    return reqs


def create_slide_image_with_bullets(sid, title, image_url, bullet_items):
    """
    Creates a slide with:
    - Blue header bar at top
    - Narrower screenshot on the LEFT (taking ~60% width)
    - Bullet point summary on the RIGHT (taking ~40% width)
    Used for Meta Issues and Heading Issues summary slides.
    """
    reqs = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Blue header bar at top
        {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}}, 'fields': 'pageBackgroundFill'}},
        # Title
        {'createShape': {'objectId': f"{sid}_t", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 15, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_t", 'text': title}},
        {'updateTextStyle': {'objectId': f"{sid}_t", 'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
    ]
    
    # Screenshot on LEFT - narrower (60% of slide width)
    reqs.append({
        'createImage': {
            'objectId': f"{sid}_img",
            'url': image_url,
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 420, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 15, 'translateY': 75, 'unit': 'PT'}
            }
        }
    })
    
    # Bullet summary on RIGHT - white card background
    reqs.append({
        'createShape': {
            'objectId': f"{sid}_bullets_bg",
            'shapeType': 'ROUND_RECTANGLE',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 290, 'unit': 'PT'}, 'width': {'magnitude': 250, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 450, 'translateY': 80, 'unit': 'PT'}
            }
        }
    })
    reqs.append({
        'updateShapeProperties': {
            'objectId': f"{sid}_bullets_bg",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['white']}}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }
    })
    
    # "ISSUES FOUND" label
    reqs.append({
        'createShape': {
            'objectId': f"{sid}_label",
            'shapeType': 'TEXT_BOX',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 25, 'unit': 'PT'}, 'width': {'magnitude': 220, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 465, 'translateY': 90, 'unit': 'PT'}
            }
        }
    })
    reqs.append({'insertText': {'objectId': f"{sid}_label", 'text': 'ISSUES FOUND'}})
    reqs.append({
        'updateTextStyle': {
            'objectId': f"{sid}_label",
            'style': {
                'fontSize': {'magnitude': 14, 'unit': 'PT'},
                'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}},
                'bold': True
            },
            'fields': 'fontSize,foregroundColor,bold'
        }
    })
    
    # Bullet points text
    if bullet_items:
        bullet_text = '\n'.join([f"• {item}" for item in bullet_items])
        reqs.append({
            'createShape': {
                'objectId': f"{sid}_bullets",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 240, 'unit': 'PT'}, 'width': {'magnitude': 230, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 460, 'translateY': 120, 'unit': 'PT'}
                }
            }
        })
        reqs.append({'insertText': {'objectId': f"{sid}_bullets", 'text': bullet_text}})
        reqs.append({
            'updateTextStyle': {
                'objectId': f"{sid}_bullets",
                'style': {
                    'fontSize': {'magnitude': 13, 'unit': 'PT'},
                    'foregroundColor': {'opaqueColor': {'rgbColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2}}},
                },
                'fields': 'fontSize,foregroundColor'
            }
        })
        reqs.append({
            'updateParagraphStyle': {
                'objectId': f"{sid}_bullets",
                'style': {'lineSpacing': 160, 'spaceAbove': {'magnitude': 4, 'unit': 'PT'}},
                'fields': 'lineSpacing,spaceAbove'
            }
        })
    
    return reqs

def create_slide_text_summary(sid, title, body, list_items=None):
    """
    Creates an Explainer slide matching reference design:
    - White/light gray background
    - Funnel graphic on left (placeholder shape)
    - Large blue title on right
    - Gray body text
    - Blue separator line
    - Blue list items (no bullets)
    """
    # Parse list items from body if they're on separate lines
    if list_items is None:
        lines = body.strip().split('\n')
        # Check if there's a paragraph followed by list items
        paragraph_lines = []
        list_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # If line is short (likely a list item) and we already have paragraph
            if len(line) < 50 and len(paragraph_lines) > 0:
                list_lines.append(line)
            else:
                if not list_lines:  # Only add to paragraph if we haven't started list
                    paragraph_lines.append(line)
                else:
                    list_lines.append(line)
        body_text = ' '.join(paragraph_lines)
        list_items = list_lines if list_lines else None
    else:
        body_text = body
    
    requests = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Light gray background
        {'updatePageProperties': {
            'objectId': sid, 
            'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.95, 'green': 0.95, 'blue': 0.95}}}}}, 
            'fields': 'pageBackgroundFill'
        }},
        
        # Funnel graphic area (left side) - using stacked shapes as funnel
        # Top layer - Red (ATTRACT)
        {'createShape': {
            'objectId': f"{sid}_funnel1",
            'shapeType': 'TRAPEZOID',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 60, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 40, 'translateY': 60, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_funnel1",
            'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.9, 'green': 0.2, 'blue': 0.2}}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill,outline'
        }},
        {'insertText': {'objectId': f"{sid}_funnel1", 'text': 'ATTRACT'}},
        {'updateTextStyle': {'objectId': f"{sid}_funnel1", 'style': {'fontSize': {'magnitude': 16, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        {'updateParagraphStyle': {'objectId': f"{sid}_funnel1", 'style': {'alignment': 'CENTER'}, 'fields': 'alignment'}},
        
        # Second layer - Orange (ENGAGE)
        {'createShape': {
            'objectId': f"{sid}_funnel2",
            'shapeType': 'TRAPEZOID',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 55, 'unit': 'PT'}, 'width': {'magnitude': 180, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 115, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_funnel2",
            'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.95, 'green': 0.65, 'blue': 0.1}}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill,outline'
        }},
        {'insertText': {'objectId': f"{sid}_funnel2", 'text': 'ENGAGE'}},
        {'updateTextStyle': {'objectId': f"{sid}_funnel2", 'style': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        {'updateParagraphStyle': {'objectId': f"{sid}_funnel2", 'style': {'alignment': 'CENTER'}, 'fields': 'alignment'}},
        
        # Third layer - Teal (INFLUENCE)
        {'createShape': {
            'objectId': f"{sid}_funnel3",
            'shapeType': 'TRAPEZOID',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 160, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 60, 'translateY': 165, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_funnel3",
            'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.2, 'green': 0.6, 'blue': 0.6}}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill,outline'
        }},
        {'insertText': {'objectId': f"{sid}_funnel3", 'text': 'INFLUENCE'}},
        {'updateTextStyle': {'objectId': f"{sid}_funnel3", 'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        {'updateParagraphStyle': {'objectId': f"{sid}_funnel3", 'style': {'alignment': 'CENTER'}, 'fields': 'alignment'}},
        
        # Bottom layer - Green (CONVERT)
        {'createShape': {
            'objectId': f"{sid}_funnel4",
            'shapeType': 'TRAPEZOID',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 45, 'unit': 'PT'}, 'width': {'magnitude': 140, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 70, 'translateY': 210, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_funnel4",
            'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.6, 'green': 0.8, 'blue': 0.2}}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill,outline'
        }},
        {'insertText': {'objectId': f"{sid}_funnel4", 'text': 'CONVERT'}},
        {'updateTextStyle': {'objectId': f"{sid}_funnel4", 'style': {'fontSize': {'magnitude': 11, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        {'updateParagraphStyle': {'objectId': f"{sid}_funnel4", 'style': {'alignment': 'CENTER'}, 'fields': 'alignment'}},
        
        # Title (right side) - Large blue text, wider box to avoid wrapping
        {'createShape': {
            'objectId': f"{sid}_title",
            'shapeType': 'TEXT_BOX',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 420, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 50, 'unit': 'PT'}
            }
        }},
        {'insertText': {'objectId': f"{sid}_title", 'text': title}},
        {'updateTextStyle': {
            'objectId': f"{sid}_title",
            'style': {
                'fontSize': {'magnitude': 36, 'unit': 'PT'},
                'fontFamily': 'Arial',
                'bold': True,
                'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}
            },
            'fields': 'fontSize,fontFamily,bold,foregroundColor'
        }},
    ]
    
    # Body text - Gray (only add if not empty)
    if body_text and body_text.strip():
        requests.extend([
            {'createShape': {
                'objectId': f"{sid}_body",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 160, 'unit': 'PT'}, 'width': {'magnitude': 420, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 105, 'unit': 'PT'}
                }
            }},
            {'insertText': {'objectId': f"{sid}_body", 'text': body_text}},
            {'updateTextStyle': {
                'objectId': f"{sid}_body",
                'style': {
                    'fontSize': {'magnitude': 16, 'unit': 'PT'},
                    'fontFamily': 'Arial',
                    'foregroundColor': {'opaqueColor': {'rgbColor': {'red': 0.3, 'green': 0.3, 'blue': 0.3}}}
                },
                'fields': 'fontSize,fontFamily,foregroundColor'
            }},
            {'updateParagraphStyle': {
                'objectId': f"{sid}_body",
                'style': {'lineSpacing': 130},
                'fields': 'lineSpacing'
            }},
        ])
    
    
    # Add list items in blue (if any)
    if list_items:
        # Add blue separator line before list
        requests.extend([
            {'createShape': {
                'objectId': f"{sid}_line",
                'shapeType': 'RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 5, 'unit': 'PT'}, 'width': {'magnitude': 60, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 225, 'unit': 'PT'}
                }
            }},
            {'updateShapeProperties': {
                'objectId': f"{sid}_line",
                'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
                'fields': 'shapeBackgroundFill,outline'
            }},
        ])
        
        list_text = '\n'.join(list_items)
        requests.extend([
            {'createShape': {
                'objectId': f"{sid}_list",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 100, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 240, 'unit': 'PT'}
                }
            }},
            {'insertText': {'objectId': f"{sid}_list", 'text': list_text}},
            {'updateTextStyle': {
                'objectId': f"{sid}_list",
                'style': {
                    'fontSize': {'magnitude': 22, 'unit': 'PT'},
                    'fontFamily': 'Arial',
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}
                },
                'fields': 'fontSize,fontFamily,foregroundColor'
            }},
            {'updateParagraphStyle': {
                'objectId': f"{sid}_list",
                'style': {'lineSpacing': 150, 'spaceAbove': {'magnitude': 8, 'unit': 'PT'}},
                'fields': 'lineSpacing,spaceAbove'
            }}
        ])
        
    return requests

def create_slide_content_strategy(sid, title, body_text):
    """Creates Content Strategy slide with target/bullseye graphic instead of funnel."""
    requests = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        
        # Light gray background
        {'updatePageProperties': {
            'objectId': sid,
            'pageProperties': {
                'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['light_gray']}}}
            },
            'fields': 'pageBackgroundFill'
        }},
        
        # Target/Bullseye Graphic (3 concentric circles)
        # Outer circle - Light blue
        {'createShape': {
            'objectId': f"{sid}_target_outer",
            'shapeType': 'ELLIPSE',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 200, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 100, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_target_outer",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.85, 'green': 0.92, 'blue': 1.0}}}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }},
        
        # Middle circle - Medium blue
        {'createShape': {
            'objectId': f"{sid}_target_middle",
            'shapeType': 'ELLIPSE',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 140, 'unit': 'PT'}, 'width': {'magnitude': 140, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 80, 'translateY': 130, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_target_middle",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0.5, 'green': 0.7, 'blue': 0.95}}}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }},
        
        # Inner circle - Primary blue (bullseye)
        {'createShape': {
            'objectId': f"{sid}_target_inner",
            'shapeType': 'ELLIPSE',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 80, 'unit': 'PT'}, 'width': {'magnitude': 80, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 110, 'translateY': 160, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_target_inner",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }},
        
        # Arrow pointing to center (optional decorative element)
        {'createShape': {
            'objectId': f"{sid}_arrow",
            'shapeType': 'RIGHT_ARROW',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 60, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 200, 'translateY': 185, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_arrow",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['yellow']}}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }},
        
        # Title (right side) - Large blue text
        {'createShape': {
            'objectId': f"{sid}_title",
            'shapeType': 'TEXT_BOX',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 420, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 50, 'unit': 'PT'}
            }
        }},
        {'insertText': {'objectId': f"{sid}_title", 'text': title}},
        {'updateTextStyle': {
            'objectId': f"{sid}_title",
            'style': {
                'fontSize': {'magnitude': 36, 'unit': 'PT'},
                'fontFamily': 'Arial',
                'bold': True,
                'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}
            },
            'fields': 'fontSize,fontFamily,bold,foregroundColor'
        }},
        
        # Body text - Gray
        {'createShape': {
            'objectId': f"{sid}_body",
            'shapeType': 'TEXT_BOX',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 200, 'unit': 'PT'}, 'width': {'magnitude': 420, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 290, 'translateY': 105, 'unit': 'PT'}
            }
        }},
        {'insertText': {'objectId': f"{sid}_body", 'text': body_text}},
        {'updateTextStyle': {
            'objectId': f"{sid}_body",
            'style': {
                'fontSize': {'magnitude': 16, 'unit': 'PT'},
                'fontFamily': 'Arial',
                'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['gray']}}
            },
            'fields': 'fontSize,fontFamily,foregroundColor'
        }},
        {'updateParagraphStyle': {
            'objectId': f"{sid}_body",
            'style': {'lineSpacing': 130},
            'fields': 'lineSpacing'
        }},
    ]
    return requests

def create_slide_text_list(sid, title, items, subtitle=""):
    # Build the list text - pass items directly
    body = ""  # Empty body, items go to list
    reqs = create_slide_text_summary(sid, title, body, list_items=items)
    return reqs

def create_slide_traffic_dashboard(sid, rank, backlinks, domain, keywords=None):
    # Mocking the dashboard look with shapes
    reqs = [
         {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
         {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}}, 'fields': 'pageBackgroundFill'}},
         {'createShape': {'objectId': f"{sid}_t", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 20, 'unit': 'PT'}}}},
         {'insertText': {'objectId': f"{sid}_t", 'text': "Organic Traffic Overview"}},
          {'updateTextStyle': {'objectId': f"{sid}_t", 'style': {'fontSize': {'magnitude': 30, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
    ]
    
    # White card background
    reqs.append({'createShape': {'objectId': f"{sid}_card", 'shapeType': 'RECTANGLE', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 250, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 80, 'unit': 'PT'}}}})
    reqs.append({'updateShapeProperties': {'objectId': f"{sid}_card", 'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['white']}}}}, 'fields': 'shapeBackgroundFill'}})
    
    # Try rank_overview first, then calculate from keywords
    rank_metrics = rank.get('metrics') if rank else None
    organic = rank_metrics.get('organic') if rank_metrics else None
    
    if organic:
        etv = organic.get('etv', 0) or 0
        kw_count = organic.get('count', 0) or 0
    else:
        # Calculate from keywords data
        keywords = keywords or []
        etv = 0
        for kw in keywords:
            kw_info = kw.get('keyword_data', {}).get('keyword_info', {})
            vol = kw_info.get('search_volume', 0) or 0
            pos = kw.get('ranked_serp_element', {}).get('serp_item', {}).get('rank_absolute', 100)
            # Estimate traffic: volume * CTR based on position
            ctr = max(0.01, 0.3 - (pos * 0.02)) if pos <= 10 else 0.01
            etv += int(vol * ctr)
        kw_count = len(keywords)
    
    # Get backlink data
    ref_domains = backlinks.get('referring_domains', 0) if backlinks else 0
    total_backlinks = backlinks.get('total_backlinks', 0) if backlinks else 0
    
    metrics = {
        'Org. Keywords': format_number(kw_count),
        'Org. Traffic': format_number(etv),
        'Traffic Value': f"${etv * 2:,.0f}" if etv else "N/A",
        'Ref. Domains': format_number(ref_domains) if ref_domains else "N/A",
        'Backlinks': format_number(total_backlinks) if total_backlinks else "N/A",
        'Avg. Position': f"{sum(kw.get('ranked_serp_element', {}).get('serp_item', {}).get('rank_absolute', 0) for kw in (keywords or [])) / max(1, len(keywords or [])):.1f}" if keywords else "N/A"
    }

    x_start = 50
    y_start = 100
    idx = 0
    for label, val in metrics.items():
        x = x_start + (idx % 3) * 200
        y = y_start + (idx // 3) * 100
        reqs.extend([
             {'createShape': {'objectId': f"{sid}_m{idx}_l", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 20, 'unit': 'PT'}, 'width': {'magnitude': 150, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': x, 'translateY': y, 'unit': 'PT'}}}},
             {'insertText': {'objectId': f"{sid}_m{idx}_l", 'text': label}},
             {'updateTextStyle': {'objectId': f"{sid}_m{idx}_l", 'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['dark']}}}, 'fields': 'fontSize,foregroundColor'}},
             
             {'createShape': {'objectId': f"{sid}_m{idx}_v", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 150, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': x, 'translateY': y + 20, 'unit': 'PT'}}}},
             {'insertText': {'objectId': f"{sid}_m{idx}_v", 'text': str(val)}},
             {'updateTextStyle': {'objectId': f"{sid}_m{idx}_v", 'style': {'fontSize': {'magnitude': 24, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        ])
        idx += 1
    
    # Low Traffic Warning
    reqs.extend([
        {'createShape': {'objectId': f"{sid}_warn", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 450, 'translateY': 100, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_warn", 'text': "Low Organic Visibility"}},
        {'updateTextStyle': {'objectId': f"{sid}_warn", 'style': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['error']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}}
    ])
    
    return reqs

def create_slide_top_pages(sid, page_list):
    reqs = create_basic_slide(sid, "TOP PAGES")
    
    # Table header
    headers = ["URL", "Top Keyword", "Est. Traffic"]
    reqs.append({'createTable': {'objectId': f"{sid}_tbl", 'rows': len(page_list) + 1, 'columns': 3, 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 100, 'unit': 'PT'}}}})
    
    # Fill Headers
    for i, h in enumerate(headers):
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': i}, 'text': h}})
    
    # Fill Rows
    for r, p in enumerate(page_list):
        row = r + 1
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 0}, 'text': p.get('url', '')[:40]}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 1}, 'text': p.get('top_kw', '')}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 2}, 'text': format_number(p.get('traffic', 0))}})
    
    return reqs

def create_slide_organic_kw_summary(sid, rank, keywords=None):
    reqs = create_slide_traffic_dashboard(sid, rank, {}, "dummy", keywords) # Reuse visual
    # Change Title
    reqs.append({'deleteText': {'objectId': f"{sid}_t", 'textRange': {'type': 'ALL'}}})
    reqs.append({'insertText': {'objectId': f"{sid}_t", 'text': "Organic Keywords"}})
    return reqs

def create_slide_kw_table(sid, keywords):
    reqs = create_basic_slide(sid, "ORGANIC KW REPORT")
    headers = ["Keyword", "Volume", "KD", "CPC", "Pos"]
    reqs.append({'createTable': {'objectId': f"{sid}_tbl", 'rows': len(keywords) + 1, 'columns': 5, 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 100, 'unit': 'PT'}}}})
    
    for i, h in enumerate(headers):
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': i}, 'text': h}})
        
    for r, kw in enumerate(keywords):
        row = r + 1
        kd = kw.get('keyword_data', {})
        kw_info = kd.get('keyword_info', {})
        serp = kw.get('ranked_serp_element', {}).get('serp_item', {})
        
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 0}, 'text': kd.get('keyword', '')[:30]}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 1}, 'text': format_number(kw_info.get('search_volume', 0))}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 2}, 'text': str(kw_info.get('competition_level', '-'))}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 3}, 'text': format_currency(kw_info.get('cpc', 0))}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 4}, 'text': str(serp.get('rank_absolute', '-'))}})
    return reqs

def create_slide_issue_table(sid, title, pages_with_issue, issue_desc, issue_key):
    """Creates a slide with a table of URLs that have a specific issue."""
    reqs = create_basic_slide(sid, title)
    
    # Count indicator
    count = len(pages_with_issue)
    reqs.append({'createShape': {'objectId': f"{sid}_count", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 520, 'translateY': 60, 'unit': 'PT'}}}})
    color = COLORS['error'] if count > 0 else COLORS['success']
    reqs.append({'insertText': {'objectId': f"{sid}_count", 'text': f"⚠ {count} pages affected" if count > 0 else "✓ No issues found"}})
    reqs.append({'updateTextStyle': {'objectId': f"{sid}_count", 'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': color}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    if pages_with_issue:
        # Create table with URLs
        rows = min(len(pages_with_issue), 5)  # Max 5 rows
        reqs.append({'createTable': {'objectId': f"{sid}_tbl", 'rows': rows + 1, 'columns': 2, 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 250, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 100, 'unit': 'PT'}}}})
        
        # Headers
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': 0}, 'text': 'URL'}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': 1}, 'text': 'Issue'}})
        
        # Data rows
        for r, p in enumerate(pages_with_issue[:5]):
            url = p.get('url', '')
            # Shorten URL for display
            display_url = url.replace('https://', '').replace('http://', '')[:50]
            reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': r + 1, 'columnIndex': 0}, 'text': display_url}})
            reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': r + 1, 'columnIndex': 1}, 'text': issue_desc}})
    else:
        # No issues message
        reqs.append({'createShape': {'objectId': f"{sid}_ok", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 180, 'translateY': 180, 'unit': 'PT'}}}})
        reqs.append({'insertText': {'objectId': f"{sid}_ok", 'text': "✓ No issues detected on crawled pages"}})
        reqs.append({'updateTextStyle': {'objectId': f"{sid}_ok", 'style': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['success']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    return reqs




def create_slide_homepage_snapshot(sid, image_url):
    """Creates a slide with the homepage snapshot filling the entire slide."""
    reqs = [
         {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
         {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0, 'green': 0, 'blue': 0}}}}}, 'fields': 'pageBackgroundFill'}},
         
         # Image (Full Screen 720x405)
         {'createImage': {
            'objectId': f"{sid}_img",
            'url': image_url,
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 405, 'unit': 'PT'}, 'width': {'magnitude': 720, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 0, 'unit': 'PT'}
            }
        }},
        
        # Overlay Title Box (Semi-transparent Black)
        {'createShape': {
            'objectId': f"{sid}_bg", 
            'shapeType': 'RECTANGLE', 
            'elementProperties': {
                'pageObjectId': sid, 
                'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 720, 'unit': 'PT'}}, 
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 355, 'unit': 'PT'} # Bottom bar
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_bg",
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': {'red': 0, 'green': 0, 'blue': 0}}, 'alpha': 0.7}},
                'outline': {'propertyState': 'NOT_RENDERED'}
            },
            'fields': 'shapeBackgroundFill,outline'
        }},
        
        # Title Text
        {'createShape': {'objectId': f"{sid}_t", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 500, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 20, 'translateY': 360, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_t", 'text': "Homepage Snapshot"}},
        {'updateTextStyle': {'objectId': f"{sid}_t", 'style': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
        
    ]
    return reqs

def create_slide_issue_screenshot(sid, title, page_data, issue_label):
    reqs = create_basic_slide(sid, title)
    
    if page_data:
        # Show URL
        reqs.append({'createShape': {'objectId': f"{sid}_url", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 80, 'unit': 'PT'}}}})
        reqs.append({'insertText': {'objectId': f"{sid}_url", 'text': f"URL: {page_data.get('url', '')}"}})
        reqs.append({'updateTextStyle': {'objectId': f"{sid}_url", 'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}}, 'fields': 'fontSize,foregroundColor'}})
        
        # Show Issue Box
        reqs.append({'createShape': {'objectId': f"{sid}_box", 'shapeType': 'RECTANGLE', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 150, 'unit': 'PT'}, 'width': {'magnitude': 500, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 100, 'translateY': 150, 'unit': 'PT'}}}})
        reqs.append({'updateShapeProperties': {'objectId': f"{sid}_box", 'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['white']}}}}, 'fields': 'shapeBackgroundFill'}})
        
        reqs.append({'createShape': {'objectId': f"{sid}_issue", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 300, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 200, 'translateY': 200, 'unit': 'PT'}}}})
        reqs.append({'insertText': {'objectId': f"{sid}_issue", 'text': f"❌ {issue_label}"}})
        reqs.append({'updateTextStyle': {'objectId': f"{sid}_issue", 'style': {'fontSize': {'magnitude': 20, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['error']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
        
    return reqs

# Asset IDs for static graphics
ASSET_IDS = {
    'cover_phone': '1CwI37IBvke9dq0efr7ijpK-FCl-UCtZ-',
    'funnel': '1nOlC8bTQF6JpzbV_iUG4H7OVdbkw75cL',
    'pillars': '15_8zxssOQR2Cs1nVJAYtB7098SCUEOpH'
}

def create_slide_cover(sid, domain):
    """Creates the 'Content Marketing Audit' Cover Slide (Slide 1) - Reference Match"""
    requests = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Left Panel (Blue) - Covers ~55%
        {
            'createShape': {
                'objectId': f"{sid}_panel",
                'shapeType': 'RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 405, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 0, 'unit': 'PT'}
                }
            }
        },
        {
             'updateShapeProperties': {
                'objectId': f"{sid}_panel",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },

        # Red Pill "Content Marketing Audit"
        {
            'createShape': {
                'objectId': f"{sid}_pill",
                'shapeType': 'ROUND_RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 30, 'translateY': 40, 'unit': 'PT'}
                }
            }
        },
        {
            'updateShapeProperties': {
                'objectId': f"{sid}_pill",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['red']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_pill",
                'text': "Content Marketing Audit"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_pill",
                'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True},
                'fields': 'fontSize,foregroundColor,bold'
            }
        },
        {
             'updateParagraphStyle': {
                'objectId': f"{sid}_pill",
                'style': {'alignment': 'CENTER'},
                'fields': 'alignment'
             }
        },
        # Main Title "What Does It Take..."
        {
            'createShape': {
                'objectId': f"{sid}_title",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 200, 'unit': 'PT'}, 'width': {'magnitude': 350, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 30, 'translateY': 90, 'unit': 'PT'}
                }
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_title",
                'text': "What Does It\nTake To Win in\nGoogle\nSearches?"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_title",
                'style': {
                    'fontSize': {'magnitude': 36, 'unit': 'PT'},
                    'bold': True,
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}
                },
                'fields': 'fontSize,bold,foregroundColor'
            }
        },
        # Personalized Subtitle with Website Name
        {
            'createShape': {
                'objectId': f"{sid}_subtitle",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 350, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 30, 'translateY': 280, 'unit': 'PT'}
                }
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_subtitle",
                'text': f"Content Audit for {domain}"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_subtitle",
                'style': {
                    'fontSize': {'magnitude': 22, 'unit': 'PT'},
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['yellow']}},
                    'bold': True
                },
                'fields': 'fontSize,foregroundColor,bold'
            }
        },
        # Image (Funnel) on Right Panel
        {
            'createImage': {
                'objectId': f"{sid}_funnel",
                'url': f"https://drive.google.com/uc?id={ASSET_IDS['funnel']}&export=download",
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 300, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 410, 'translateY': 50, 'unit': 'PT'}
                }
            }
        }
    ]
    return requests

def create_slide_funnel(sid):
    """Creates the Content Audit Funnel slide (Slide 3) - Reference Match"""
    requests = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Left Panel (Blue) - Covers ~55%
        {
            'createShape': {
                'objectId': f"{sid}_panel",
                'shapeType': 'RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 405, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 0, 'unit': 'PT'}
                }
            }
        },
        {
             'updateShapeProperties': {
                'objectId': f"{sid}_panel",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },
        # Top Decoration (Light Blue Semi-Circle/Arc)
        {
            'createShape': {
                'objectId': f"{sid}_decor_top",
                'shapeType': 'CHORD', # Semi-circle look
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 80, 'unit': 'PT'}, 'width': {'magnitude': 100, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 250, 'translateY': -40, 'unit': 'PT'} # Peeking from top
                }
            }
        },
        {
             'updateShapeProperties': {
                'objectId': f"{sid}_decor_top",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['light_blue']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },
        # Red Pill "Content Marketing Audit"
        {
            'createShape': {
                'objectId': f"{sid}_pill",
                'shapeType': 'ROUND_RECTANGLE',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 30, 'translateY': 40, 'unit': 'PT'}
                }
            }
        },
        {
            'updateShapeProperties': {
                'objectId': f"{sid}_pill",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['red']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'}
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_pill",
                'text': "Content Marketing Audit"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_pill",
                'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True},
                'fields': 'fontSize,foregroundColor,bold'
            }
        },
        {
             'updateParagraphStyle': {
                'objectId': f"{sid}_pill",
                'style': {'alignment': 'CENTER'},
                'fields': 'alignment'
             }
        },
        # Main Title "What Does It Take..."
        {
            'createShape': {
                'objectId': f"{sid}_title",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 200, 'unit': 'PT'}, 'width': {'magnitude': 350, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 30, 'translateY': 90, 'unit': 'PT'}
                }
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_title",
                'text': "What Does It\nTake To Win in\nGoogle\nSearches?"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_title",
                'style': {
                    'fontSize': {'magnitude': 36, 'unit': 'PT'},
                    'bold': True,
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}
                },
                'fields': 'fontSize,bold,foregroundColor'
            }
        },
        # Yellow Arrow (Chevron)
        {
            'createShape': {
                'objectId': f"{sid}_arrow",
                'shapeType': 'CHEVRON', 
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 60, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 40, 'translateY': 320, 'unit': 'PT'}
                }
            }
        },
        {
            'updateShapeProperties': {
                'objectId': f"{sid}_arrow",
                'shapeProperties': {
                    'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['yellow']}}},
                    'outline': {'propertyState': 'NOT_RENDERED'},
                    'contentAlignment': 'MIDDLE' 
                },
                'fields': 'shapeBackgroundFill,outline'
            }
        },
        # Image (Funnel) on Right Panel
        {
            'createImage': {
                'objectId': f"{sid}_funnel",
                'url': f"https://drive.google.com/uc?id={ASSET_IDS['funnel']}&export=download",
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 300, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 410, 'translateY': 50, 'unit': 'PT'}
                }
            }
        }
    ]
    return requests

def create_slide_heading_issues(sid, pages, title, tag, condition):
    bad_pages = [p for p in pages if condition(p.get('meta', {}).get(tag, []) or [])][:3]
    reqs = create_basic_slide(sid, title)
    
    y = 120
    for p in bad_pages:
        reqs.append({'createShape': {'objectId': f"{sid}_p{y}", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': y, 'unit': 'PT'}}}})
        reqs.append({'insertText': {'objectId': f"{sid}_p{y}", 'text': f"• {p.get('url')}"}})
        reqs.append({'updateTextStyle': {'objectId': f"{sid}_p{y}", 'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}}, 'fields': 'fontSize,foregroundColor'}})
        y += 40
    return reqs

def create_slide_backlinks_table(sid, title, links, note):
    reqs = create_basic_slide(sid, title)
    headers = ["Referring Page", "DR", "Links"]
    reqs.append({'createTable': {'objectId': f"{sid}_tbl", 'rows': len(links) + 1, 'columns': 3, 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 300, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 100, 'unit': 'PT'}}}})
    
    for i, h in enumerate(headers):
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': i}, 'text': h}})
        
    for r, l in enumerate(links):
        row = r + 1
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 0}, 'text': l.get('url_from', l.get('domain', ''))[:40]}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 1}, 'text': str(l.get('rank', 0))}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': row, 'columnIndex': 2}, 'text': str(l.get('backlinks', 0))}})
    
    # Red note
    reqs.append({'createShape': {'objectId': f"{sid}_note", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 200, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 450, 'translateY': 80, 'unit': 'PT'}}}})
    reqs.append({'insertText': {'objectId': f"{sid}_note", 'text': f"⬇ {note}"}})
    reqs.append({'updateTextStyle': {'objectId': f"{sid}_note", 'style': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['error']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    return reqs

def create_slide_speed(sid, load_time):
    reqs = create_basic_slide(sid, "Website Speed Analysis")
    
    # Calculate score based on load time (lower is better)
    # Google recommends < 2.5s for LCP
    if load_time <= 0:
        score = 0
        load_time = 0
    else:
        # Score: 100 for < 1s, 90 for 1-2s, 80 for 2-3s, etc.
        score = max(0, min(100, 100 - int((load_time - 1000) / 50)))
    
    color = COLORS['success'] if score >= 80 else COLORS['warning'] if score >= 50 else COLORS['error']
    
    # Circle Gauge
    reqs.append({'createShape': {'objectId': f"{sid}_c", 'shapeType': 'ELLIPSE', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 150, 'unit': 'PT'}, 'width': {'magnitude': 150, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 285, 'translateY': 150, 'unit': 'PT'}}}})
    reqs.append({'updateShapeProperties': {'objectId': f"{sid}_c", 'shapeProperties': {'outline': {'outlineFill': {'solidFill': {'color': {'rgbColor': color}}}, 'weight': {'magnitude': 10, 'unit': 'PT'}}}, 'fields': 'outline'}})
    
    reqs.append({'createShape': {'objectId': f"{sid}_s", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 60, 'unit': 'PT'}, 'width': {'magnitude': 100, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 310, 'translateY': 190, 'unit': 'PT'}}}})
    reqs.append({'insertText': {'objectId': f"{sid}_s", 'text': str(score)}})
    reqs.append({'updateTextStyle': {'objectId': f"{sid}_s", 'style': {'fontSize': {'magnitude': 48, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': color}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    # Dynamic message based on score
    if score >= 80:
        msg = "Good Page Speed Performance"
        msg_color = COLORS['success']
    elif score >= 50:
        msg = "Page Speed Needs Improvement"
        msg_color = COLORS['warning']
    else:
        msg = "Critical: Page Speed Optimization Required"
        msg_color = COLORS['error']
    
    reqs.append({'createShape': {'objectId': f"{sid}_msg", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 40, 'unit': 'PT'}, 'width': {'magnitude': 450, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 140, 'translateY': 100, 'unit': 'PT'}}}})
    reqs.append({'insertText': {'objectId': f"{sid}_msg", 'text': msg}})
    reqs.append({'updateTextStyle': {'objectId': f"{sid}_msg", 'style': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': msg_color}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    # Add load time detail
    reqs.append({'createShape': {'objectId': f"{sid}_lt", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 30, 'unit': 'PT'}, 'width': {'magnitude': 300, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 220, 'translateY': 320, 'unit': 'PT'}}}})
    reqs.append({'insertText': {'objectId': f"{sid}_lt", 'text': f"Average Load Time: {load_time/1000:.2f}s"}})
    reqs.append({'updateTextStyle': {'objectId': f"{sid}_lt", 'style': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}, 'bold': False}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    return reqs

def create_slide_schema(sid):
    return create_slide_text_list(sid, "Structured Data", ["Organisational Schema", "Local Schema", "Product Schema"], "Missing Schema detected")

def create_slide_tech_list(sid, summary, pages):
    """Create comprehensive technical issues slide from actual audit data."""
    reqs = create_basic_slide(sid, "Technical Issues Summary")
    
    # Add count badge
    checks = summary.get('page_metrics', {}).get('checks', {})
    
    # Build issues list with counts
    issues = []
    
    # Render blocking
    rb = checks.get('has_render_blocking_resources', 0)
    if rb > 0:
        issues.append(f"Render Blocking Resources: {rb} pages")
    
    # Image issues
    no_alt = checks.get('no_image_alt', 0)
    if no_alt > 0:
        issues.append(f"Images Missing Alt Text: {no_alt} pages")
    
    no_title = checks.get('no_image_title', 0)
    if no_title > 0:
        issues.append(f"Images Missing Title: {no_title} pages")
    
    # HTML issues
    deprecated = checks.get('deprecated_html_tags', 0)
    if deprecated > 0:
        issues.append(f"Deprecated HTML Tags: {deprecated} pages")
    
    # Content issues
    low_content = checks.get('low_content_rate', 0)
    if low_content > 0:
        issues.append(f"Thin Content (Low Word Count): {low_content} pages")
    
    # Title/desc issues
    dup_meta = checks.get('duplicate_meta_tags', 0)
    if dup_meta > 0:
        issues.append(f"Duplicate Meta Tags: {dup_meta} pages")
    
    # Calculate average metrics from pages
    if pages:
        avg_load = sum(p.get('load_time', 0) for p in pages) / len(pages)
        avg_size = sum(p.get('page_size', 0) for p in pages) / len(pages)
        total_images = sum(p.get('images_count', 0) for p in pages)
        total_img_size = sum(p.get('images_size', 0) for p in pages)
        
        if avg_load > 3000:
            issues.append(f"Slow Page Load: Avg {avg_load/1000:.1f}s")
        if avg_size > 1000000:  # > 1MB
            issues.append(f"Large Page Size: Avg {avg_size/1000000:.1f}MB")
        if total_img_size > 2000000:  # > 2MB total images
            issues.append(f"Heavy Images: {total_img_size/1000000:.1f}MB across {total_images} images")
    
    # Create table with issues
    if issues:
        rows = min(len(issues), 8)
        reqs.append({'createTable': {'objectId': f"{sid}_tbl", 'rows': rows + 1, 'columns': 2, 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 280, 'unit': 'PT'}, 'width': {'magnitude': 650, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 35, 'translateY': 90, 'unit': 'PT'}}}})
        
        # Headers
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': 0}, 'text': 'Issue'}})
        reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': 0, 'columnIndex': 1}, 'text': 'Priority'}})
        
        # Data rows
        for r, issue in enumerate(issues[:8]):
            priority = "High" if any(x in issue.lower() for x in ['render', 'slow', 'heavy']) else "Medium"
            reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': r + 1, 'columnIndex': 0}, 'text': issue}})
            reqs.append({'insertText': {'objectId': f"{sid}_tbl", 'cellLocation': {'rowIndex': r + 1, 'columnIndex': 1}, 'text': priority}})
    else:
        reqs.append({'createShape': {'objectId': f"{sid}_ok", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 180, 'translateY': 180, 'unit': 'PT'}}}})
        reqs.append({'insertText': {'objectId': f"{sid}_ok", 'text': "✓ No major technical issues detected"}})
        reqs.append({'updateTextStyle': {'objectId': f"{sid}_ok", 'style': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['success']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}})
    
    return reqs

def create_slide_thank_you(sid):
    """Creates the Thank You slide (Slide 29)"""
    return [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # Royal Blue Background
        {
            'updatePageProperties': {
                'objectId': sid,
                'pageProperties': {
                    'pageBackgroundFill': {
                        'solidFill': {'color': {'rgbColor': COLORS['primary']}}
                    }
                },
                'fields': 'pageBackgroundFill'
            }
        },
        # Centered "Thank You"
        {
            'createShape': {
                'objectId': f"{sid}_thankyou",
                'shapeType': 'TEXT_BOX',
                'elementProperties': {
                    'pageObjectId': sid,
                    'size': {'height': {'magnitude': 100, 'unit': 'PT'}, 'width': {'magnitude': 400, 'unit': 'PT'}},
                    'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 160, 'translateY': 150, 'unit': 'PT'} 
                }
            }
        },
        {
            'insertText': {
                'objectId': f"{sid}_thankyou",
                'text': "Thank You"
            }
        },
        {
            'updateTextStyle': {
                'objectId': f"{sid}_thankyou",
                'style': {
                    'fontSize': {'magnitude': 60, 'unit': 'PT'},
                    'fontFamily': 'Arial',
                    'bold': True,
                    'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}
                },
                'fields': 'fontSize,fontFamily,bold,foregroundColor'
            }
        },
        {
             'updateParagraphStyle': {
                'objectId': f"{sid}_thankyou",
                'style': {'alignment': 'CENTER'},
                'fields': 'alignment'
             }
        }
    ]


def create_slide_scare_explainer(sid, title, body, stat):
    """
    Creates a 'Scare' explainer slide matching the 'Link Building' reference:
    - Wide Blue Sidebar on Left (~25% width)
    - Content on Right
    - Blue Title with small underline
    """
    reqs = [
        {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}},
        # White background
        {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['white']}}}}, 'fields': 'pageBackgroundFill'}},
        
        # 1. Wide Blue Sidebar (Left)
        {'createShape': {
            'objectId': f"{sid}_sidebar",
            'shapeType': 'RECTANGLE',
            'elementProperties': {
                'pageObjectId': sid,
                'size': {'height': {'magnitude': 405, 'unit': 'PT'}, 'width': {'magnitude': 180, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 0, 'unit': 'PT'}
            }
        }},
        {'updateShapeProperties': {
            'objectId': f"{sid}_sidebar",
            'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}, 'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill,outline'
        }},

        # 2. Title (Right Side)
        {'createShape': {'objectId': f"{sid}_title", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 70, 'unit': 'PT'}, 'width': {'magnitude': 500, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 220, 'translateY': 30, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_title", 'text': title}},
        {'updateTextStyle': {'objectId': f"{sid}_title", 'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'fontFamily': 'Arial', 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}}, 'fields': 'fontSize,fontFamily,foregroundColor'}},

        # 4. Body Text (Below title with proper spacing)
        {'createShape': {'objectId': f"{sid}_body", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 180, 'unit': 'PT'}, 'width': {'magnitude': 480, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 220, 'translateY': 115, 'unit': 'PT'}}}},
        {'insertText': {'objectId': f"{sid}_body", 'text': body}},
        {'updateTextStyle': {'objectId': f"{sid}_body", 'style': {'fontSize': {'magnitude': 13, 'unit': 'PT'}, 'fontFamily': 'Arial', 'foregroundColor': {'opaqueColor': {'rgbColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2}}}}, 'fields': 'fontSize,fontFamily,foregroundColor'}},
        {'updateParagraphStyle': {'objectId': f"{sid}_body", 'style': {'lineSpacing': 130, 'spaceAbove': {'magnitude': 3, 'unit': 'PT'}}, 'fields': 'lineSpacing,spaceAbove'}},
    ]
    
    # 5. Stat / Emphasis Text (Bottom) - only add if stat is not empty
    if stat:
        reqs.extend([
            {'createShape': {'objectId': f"{sid}_stat", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 480, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 220, 'translateY': 330, 'unit': 'PT'}}}},
            {'insertText': {'objectId': f"{sid}_stat", 'text': stat}},
            {'updateTextStyle': {'objectId': f"{sid}_stat", 'style': {'fontSize': {'magnitude': 13, 'unit': 'PT'}, 'fontFamily': 'Arial', 'bold': True, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['primary']}}}, 'fields': 'fontSize,fontFamily,bold,foregroundColor'}},
        ])
    
    return reqs

def create_basic_slide(sid, title):
    return [
         {'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'TITLE_ONLY'}}},
         {'updatePageProperties': {'objectId': sid, 'pageProperties': {'pageBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['primary']}}}}, 'fields': 'pageBackgroundFill'}},
         {'createShape': {'objectId': f"{sid}_t", 'shapeType': 'TEXT_BOX', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 50, 'unit': 'PT'}, 'width': {'magnitude': 600, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 50, 'translateY': 20, 'unit': 'PT'}}}},
         {'insertText': {'objectId': f"{sid}_t", 'text': title}},
         {'updateTextStyle': {'objectId': f"{sid}_t", 'style': {'fontSize': {'magnitude': 30, 'unit': 'PT'}, 'foregroundColor': {'opaqueColor': {'rgbColor': COLORS['white']}}, 'bold': True}, 'fields': 'fontSize,foregroundColor,bold'}},
         # White card overlay for body
         {'createShape': {'objectId': f"{sid}_card", 'shapeType': 'RECTANGLE', 'elementProperties': {'pageObjectId': sid, 'size': {'height': {'magnitude': 350, 'unit': 'PT'}, 'width': {'magnitude': 680, 'unit': 'PT'}}, 'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 20, 'translateY': 80, 'unit': 'PT'}}}},
         {'updateShapeProperties': {'objectId': f"{sid}_card", 'shapeProperties': {'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': COLORS['white']}}}}, 'fields': 'shapeBackgroundFill'}}
    ]
