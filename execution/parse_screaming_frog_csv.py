"""
Parse Screaming Frog 'Internal All' CSV into the same categorized audit structure
used by DataForSEO audits. This allows CSV-imported audits to render identically
in the dashboard and export to Google Sheets.

Input:  CSV file content (bytes or string)
Output: { 'categorized': {...}, 'pages': [...], 'summary': {...} }
        Same shape as what categorize_audit_issues() produces + raw pages.
"""
import csv
import io
from collections import defaultdict

from typing import Union


def parse_screaming_frog_csv(csv_content: Union[bytes, str]) -> dict:
    """
    Parse a Screaming Frog Internal All CSV export.

    Returns a dict with:
      - 'pages': list of page dicts (compatible with export_sheets)
      - 'categorized': dict matching categorize_audit_issues() output
      - 'summary': basic summary stats
    """
    if isinstance(csv_content, bytes):
        csv_content = csv_content.decode('utf-8-sig')  # Handle BOM

    reader = csv.DictReader(io.StringIO(csv_content))

    pages = []
    
    # Tracking collections for duplicate detection
    all_titles = defaultdict(list)      # title -> [urls]
    all_descs = defaultdict(list)       # desc  -> [urls]
    all_h2s = defaultdict(list)         # h2    -> [urls]

    # Counters
    total_html_pages = 0

    for row in reader:
        url = row.get('Address', '').strip()
        content_type = row.get('Content Type', '').strip()
        status_code_str = row.get('Status Code', '200').strip()
        indexability = row.get('Indexability', '').strip()
        indexability_status = row.get('Indexability Status', '').strip()

        try:
            status_code = int(status_code_str)
        except (ValueError, TypeError):
            status_code = 0

        title = row.get('Title 1', '').strip()
        title_length = _safe_int(row.get('Title 1 Length', '0'))
        desc = row.get('Meta Description 1', '').strip()
        desc_length = _safe_int(row.get('Meta Description 1 Length', '0'))
        desc_pixel = _safe_int(row.get('Meta Description 1 Pixel Width', '0'))

        h1_1 = row.get('H1-1', '').strip()
        h1_2 = row.get('H1-2', '').strip()
        h2_1 = row.get('H2-1', '').strip()
        h2_2 = row.get('H2-2', '').strip()

        size_bytes = _safe_int(row.get('Size (bytes)', '0'))
        word_count = _safe_int(row.get('Word Count', '0'))
        inlinks = _safe_int(row.get('Inlinks', '0'))
        redirect_url = row.get('Redirect URL', '').strip()

        is_html = 'text/html' in content_type
        is_image = content_type.startswith('image/')

        # Build a page dict compatible with the existing system
        h1_list = [h for h in [h1_1, h1_2] if h]
        h2_list = [h for h in [h2_1, h2_2] if h]

        page = {
            'url': url,
            'status_code': status_code,
            'meta': {
                'title': title,
                'description': desc,
                'h1': h1_list,
                'h2': h2_list,
                'images_count': 0,
                'images_size': 0,
            },
            'checks': {
                'is_redirect': 300 <= status_code < 400,
                'is_broken': 400 <= status_code < 600,
                'no_h1': is_html and len(h1_list) == 0,
                'no_image_alt': False,  # Will be set below for images
                'seo_friendly_url': True,
                'is_orphan_page': is_html and inlinks == 0 and status_code == 200,
                'is_marked_as_noindex': indexability == 'Non-Indexable' and 'noindex' in indexability_status.lower(),
            },
            'content': {
                'word_count': word_count,
            },
            'title': title,
            'description': desc,
            'size_bytes': size_bytes,
            'content_type': content_type,
            'redirect_url': redirect_url,
            'indexability': indexability,
            'indexability_status': indexability_status,
        }

        # For images: track size for >100KB detection
        if is_image:
            page['meta']['images_count'] = 1
            page['meta']['images_size'] = size_bytes

        pages.append(page)

        # Track for duplicate detection (only HTML pages)
        if is_html and status_code == 200:
            total_html_pages += 1
            if title:
                all_titles[title].append(url)
            if desc:
                all_descs[desc].append(url)
            if h2_1:
                all_h2s[h2_1].append(url)

    # Now build the categorized structure
    categorized = _build_categorized(pages, all_titles, all_descs, all_h2s, total_html_pages)

    # Summary
    summary = {
        'total_pages': len(pages),
        'total_html_pages': total_html_pages,
        'crawl_source': 'screaming_frog_csv',
    }

    return {
        'pages': pages,
        'categorized': categorized,
        'summary': summary,
    }


def _build_categorized(pages, all_titles, all_descs, all_h2s, total_html_pages):
    """Build the exact same structure as categorize_audit_issues() in utils.py."""

    data = {
        "accessibility": {
            "core_web_vitals": {"score": 0, "issues": 0, "label": "N/A (CSV Import)"},
            "page_speed": {"score": 0, "issues": 0, "label": "N/A (CSV Import)"},
            "mobile_friendly": {"issues": 0, "items": []},
            "image_alt_missing": {"issues": 0, "items": []},
            "images_large": {"issues": 0, "items": []},
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
            "h2_duplicate": {"issues": 0, "items": []},  # NEW
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
            "site_architecture": {"issues": 0, "items": [], "label": "N/A (CSV Import)"},
        }
    }

    for page in pages:
        url = page['url']
        status_code = page['status_code']
        meta = page['meta']
        checks = page['checks']
        content_type = page.get('content_type', '')
        is_html = 'text/html' in content_type
        is_image = content_type.startswith('image/')
        size_bytes = page.get('size_bytes', 0)
        word_count = page.get('content', {}).get('word_count', 0)

        # --- Status Code Issues ---
        if 300 <= status_code < 400:
            data['usability']['links_redirect_3xx']['issues'] += 1
            data['usability']['links_redirect_3xx']['items'].append(url)

        if 400 <= status_code < 500:
            data['usability']['client_errors_4xx']['issues'] += 1
            data['usability']['client_errors_4xx']['items'].append(url)

        if status_code >= 500:
            data['usability']['server_errors_5xx']['issues'] += 1
            data['usability']['server_errors_5xx']['items'].append(url)

        # --- Only process HTML pages for on-page checks ---
        if not is_html or status_code != 200:
            # But still check images for size
            if is_image and size_bytes > 102400:
                data['accessibility']['images_large']['issues'] += 1
                data['accessibility']['images_large']['items'].append(url)
            continue

        title = meta.get('title', '')
        desc = meta.get('description', '')
        h1_list = meta.get('h1', [])
        h2_list = meta.get('h2', [])

        # No-Index
        if checks.get('is_marked_as_noindex'):
            data['usability']['no_index']['issues'] += 1
            data['usability']['no_index']['items'].append(url)

        # Title Missing
        if not title:
            data['usability']['title_missing']['issues'] += 1
            data['usability']['title_missing']['items'].append(url)
        elif len(title) > 65:
            data['usability']['title_over_65']['issues'] += 1
            data['usability']['title_over_65']['items'].append(url)

        # Desc Missing
        if not desc:
            data['usability']['desc_missing']['issues'] += 1
            data['usability']['desc_missing']['items'].append(url)
        elif len(desc) > 155:
            data['usability']['desc_over_155']['issues'] += 1
            data['usability']['desc_over_155']['items'].append(url)

        # H1
        if len(h1_list) == 0:
            data['usability']['h1_missing']['issues'] += 1
            data['usability']['h1_missing']['items'].append(url)
        elif len(h1_list) > 1:
            data['usability']['h1_multiple']['issues'] += 1
            data['usability']['h1_multiple']['items'].append(url)

        # H2
        if len(h2_list) == 0:
            data['usability']['h2_missing']['issues'] += 1
            data['usability']['h2_missing']['items'].append(url)

        # Orphan
        if checks.get('is_orphan_page'):
            data['usability']['orphan_urls']['issues'] += 1
            data['usability']['orphan_urls']['items'].append(url)

        # Low Word Count (< 300 words for HTML pages)
        if word_count > 0 and word_count < 300:
            data['usability']['low_word_count']['issues'] += 1
            data['usability']['low_word_count']['items'].append(url)

    # --- Duplicate Detection (cross-page) ---
    for title, urls in all_titles.items():
        if len(urls) > 1:
            for u in urls:
                data['usability']['title_duplicate']['issues'] += 1
                data['usability']['title_duplicate']['items'].append(u)

    for desc, urls in all_descs.items():
        if len(urls) > 1:
            for u in urls:
                data['usability']['desc_duplicate']['issues'] += 1
                data['usability']['desc_duplicate']['items'].append(u)

    for h2, urls in all_h2s.items():
        if len(urls) > 1:
            for u in urls:
                data['usability']['h2_duplicate']['issues'] += 1
                data['usability']['h2_duplicate']['items'].append(u)

    # Set status labels
    for cat in data:
        for key in data[cat]:
            item = data[cat][key]
            if item.get('issues', 0) > 0:
                item['status'] = 'fail'
            elif item.get('score') is not None and item.get('score') < 50:
                item['status'] = 'fail'
            else:
                item['status'] = 'pass'

    return data


def _safe_int(val):
    """Safely convert to int, returning 0 on failure."""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0
