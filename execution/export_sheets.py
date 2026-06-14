"""
Export Audit Results to Google Sheets — 17 Tabs
Deterministic script. Called by the API route.

Tab Spec:
1.  Overview                     — Summary: Area, Audit Notes, Recommendation, Task, Priority, Status
2.  Redirection 3xx              — Address, Redirect URL
3.  Client Error(4xx)            — Address, Status Code
4.  Index Management             — Address, Indexability Status
5.  Page Titles Over 65 Characters — Address, Title
6.  Duplicate Titles             — Address, Title
7.  Duplicate Description        — Address
8.  Meta Titles Missing          — Address
9.  Meta Description Missing     — Address
10. Meta Description Over 155    — Address, Description, Characters, Pixel
11. H1 Missing                   — Address
12. H1 Multiple                  — Address
13. H2 Missing                   — Address
14. H2 Duplicate                 — Address, H2
15. Image Over 100 KB            — Address
16. Orphan URLs                  — Address
17. Image Alt Text Missing       — Address
"""
import os
import sys
import httplib2
import ssl

# Force unverified SSL context (matches deep_audit_slides.py pattern)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Header color — matches reference: blue (#2D5AB5) with white text
HEADER_BG = {'red': 45/255, 'green': 90/255, 'blue': 181/255}
OVERVIEW_TITLE_BG = {'red': 204/255, 'green': 51/255, 'blue': 51/255}  # Red banner
GREEN_OK = {'red': 0/255, 'green': 176/255, 'blue': 80/255}

# Recommendation text for Overview tab
RECOMMENDATIONS = {
    'redirection_3xx':       {'label': 'Redirection 3xx',                   'rec': 'Needs to find & Remove The 3XX redirections to improve the site.',         'task': '', 'priority': 'High'},
    'client_errors_4xx':     {'label': 'Broken Links (401-404 Pages)',       'rec': 'Need to find & remove the 4xx Url links to optimize',                     'task': '', 'priority': 'High'},
    'index_management':      {'label': 'Index Management',                  'rec': 'Already OK',                                                               'task': '', 'priority': 'High'},
    'title_over_65':         {'label': 'Page Titles Over 65 Characters',    'rec': 'Shorten the descriptions & keep it under 65 characters',                   'task': '', 'priority': 'High'},
    'title_duplicate':       {'label': 'Duplicate Titles',                  'rec': 'Needs to add Relevant titles related to the page',                         'task': '', 'priority': 'High'},
    'desc_duplicate':        {'label': 'Duplicate Description',             'rec': 'Needs to add Relevant description related to the page',                    'task': '', 'priority': 'High'},
    'title_missing':         {'label': 'Meta Titles Missing',               'rec': 'Already OK',                                                               'task': '', 'priority': 'High'},
    'desc_missing':          {'label': 'Meta Description Missing',          'rec': 'Needs to add relevant description to the page',                            'task': '', 'priority': 'High'},
    'desc_over_155':         {'label': 'Meta Description Over 155',         'rec': 'Shorten the descriptions & keep them under 160 characters',                'task': '', 'priority': 'High'},
    'h1_missing':            {'label': 'H1 Missing',                        'rec': 'Needs to add relevant h1 headings to the page',                            'task': '', 'priority': 'High'},
    'h1_multiple':           {'label': 'H1 Multiple',                       'rec': 'Multiple h1 heading tags needs to be removed',                             'task': '', 'priority': 'High'},
    'h2_missing':            {'label': 'H2 Missing',                        'rec': 'Needs to add relevant h2 subheadings to the page',                        'task': '', 'priority': 'High'},
    'h2_duplicate':          {'label': 'H2 Duplicate',                      'rec': 'Needs to make H2 headings unique across pages',                            'task': '', 'priority': 'High'},
    'images_large':          {'label': 'Image Over 100 KB',                 'rec': 'Compress the image. Make them below 100 kb size',                          'task': '', 'priority': 'High'},
    'orphan_urls':           {'label': 'Orphan URLs',                       'rec': 'Add internal links pointing to these orphan pages',                        'task': '', 'priority': 'High'},
    'image_alt_missing':     {'label': 'Image Alt Text Missing',            'rec': 'Put alt text into the image alt section.',                                 'task': '', 'priority': 'High'},
}


def export_audit_to_sheets(audit_data: dict, domain: str, creds=None) -> dict:
    """
    Main entry point. Creates a Google Spreadsheet with 12 tabs.

    Args:
        audit_data: The full audit record (from Supabase audits table).
                    Must contain results.categorized and results.pages.
        domain:     The domain being audited (for naming).
        creds:      Optional Google credentials. Falls back to get_google_credentials().

    Returns:
        {'success': True, 'spreadsheet_url': '...', 'spreadsheet_id': '...'}
    """
    if not creds:
        from api.google_auth import get_google_credentials
        creds = get_google_credentials()

    http = httplib2.Http(disable_ssl_certificate_validation=True)
    authorized_http = AuthorizedHttp(creds, http=http)

    sheets_service = build('sheets', 'v4', http=authorized_http)
    drive_service = build('drive', 'v3', http=authorized_http)

    results = audit_data.get('results', {})
    categorized = results.get('categorized', {})
    pages = results.get('pages', [])

    # Build data for all 12 tabs
    tabs_data = _build_all_tabs(categorized, pages, domain)

    # ── Step 1: Create the spreadsheet with tab stubs ──
    sheet_properties = []
    for i, tab in enumerate(tabs_data):
        sheet_properties.append({
            'properties': {
                'sheetId': i,
                'title': tab['title'],
                'index': i,
            }
        })

    spreadsheet_body = {
        'properties': {'title': f'SEO Audit — {domain}'},
        'sheets': sheet_properties
    }
    spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet_body).execute()
    spreadsheet_id = spreadsheet['spreadsheetId']

    # ── Step 2: Write data to each tab ──
    data_ranges = []
    for tab in tabs_data:
        title = tab['title']
        rows = tab['rows']  # list of lists
        if rows:
            data_ranges.append({
                'range': f"'{title}'!A1",
                'values': rows
            })

    if data_ranges:
        sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                'valueInputOption': 'USER_ENTERED',
                'data': data_ranges
            }
        ).execute()

    # ── Step 3: Format headers + styling ──
    format_requests = []
    for i, tab in enumerate(tabs_data):
        rows = tab['rows']
        if not rows:
            continue

        num_cols = max(len(r) for r in rows) if rows else 1
        is_overview = (tab['title'] == 'Overview')

        # --- Row 1 header: title banner (only for non-overview tabs) ---
        if not is_overview:
            # Row 1: Tab title banner (blue bg, white text, bold, merged)
            format_requests.append({
                'repeatCell': {
                    'range': {'sheetId': i, 'startRowIndex': 0, 'endRowIndex': 1,
                              'startColumnIndex': 0, 'endColumnIndex': num_cols},
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': HEADER_BG,
                            'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                                           'bold': True, 'fontSize': 12},
                            'horizontalAlignment': 'LEFT',
                            'verticalAlignment': 'MIDDLE',
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                }
            })
            # Merge row 1 across columns
            if num_cols > 1:
                format_requests.append({
                    'mergeCells': {
                        'range': {'sheetId': i, 'startRowIndex': 0, 'endRowIndex': 1,
                                  'startColumnIndex': 0, 'endColumnIndex': num_cols},
                        'mergeType': 'MERGE_ALL'
                    }
                })
            # Row 2: Column headers (bold)
            if len(rows) > 1:
                format_requests.append({
                    'repeatCell': {
                        'range': {'sheetId': i, 'startRowIndex': 1, 'endRowIndex': 2,
                                  'startColumnIndex': 0, 'endColumnIndex': num_cols},
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {'bold': True, 'fontSize': 11},
                            }
                        },
                        'fields': 'userEnteredFormat(textFormat)'
                    }
                })
        else:
            # Overview tab: Row 1 red banner, Row 2 blue headers
            format_requests.append({
                'repeatCell': {
                    'range': {'sheetId': i, 'startRowIndex': 0, 'endRowIndex': 1,
                              'startColumnIndex': 0, 'endColumnIndex': num_cols},
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': OVERVIEW_TITLE_BG,
                            'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                                           'bold': True, 'fontSize': 14},
                            'horizontalAlignment': 'CENTER',
                            'verticalAlignment': 'MIDDLE',
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                }
            })
            if num_cols > 1:
                format_requests.append({
                    'mergeCells': {
                        'range': {'sheetId': i, 'startRowIndex': 0, 'endRowIndex': 1,
                                  'startColumnIndex': 0, 'endColumnIndex': num_cols},
                        'mergeType': 'MERGE_ALL'
                    }
                })
            # Row 2: blue column headers
            format_requests.append({
                'repeatCell': {
                    'range': {'sheetId': i, 'startRowIndex': 1, 'endRowIndex': 2,
                              'startColumnIndex': 0, 'endColumnIndex': num_cols},
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': HEADER_BG,
                            'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                                           'bold': True, 'fontSize': 11},
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                }
            })
            # Green "OK" cells — find them and color
            for row_idx, row in enumerate(rows):
                if row_idx < 2:
                    continue
                for col_idx, cell_val in enumerate(row):
                    if cell_val == 'OK':
                        format_requests.append({
                            'repeatCell': {
                                'range': {'sheetId': i, 'startRowIndex': row_idx, 'endRowIndex': row_idx + 1,
                                          'startColumnIndex': col_idx, 'endColumnIndex': col_idx + 1},
                                'cell': {
                                    'userEnteredFormat': {
                                        'backgroundColor': GREEN_OK,
                                        'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True},
                                    }
                                },
                                'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                            }
                        })
                    # Also color the recommendation column green for "Already OK"
                    if isinstance(cell_val, str) and cell_val.startswith('Already OK'):
                        format_requests.append({
                            'repeatCell': {
                                'range': {'sheetId': i, 'startRowIndex': row_idx, 'endRowIndex': row_idx + 1,
                                          'startColumnIndex': col_idx, 'endColumnIndex': col_idx + 1},
                                'cell': {
                                    'userEnteredFormat': {
                                        'backgroundColor': GREEN_OK,
                                        'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                                    }
                                },
                                'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                            }
                        })

        # Auto-resize columns
        format_requests.append({
            'autoResizeDimensions': {
                'dimensions': {'sheetId': i, 'dimension': 'COLUMNS',
                               'startIndex': 0, 'endIndex': num_cols}
            }
        })

    if format_requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': format_requests}
        ).execute()

    # ── Step 4: Make shareable ──
    drive_service.permissions().create(
        fileId=spreadsheet_id,
        body={'type': 'anyone', 'role': 'writer'}
    ).execute()

    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

    return {
        'success': True,
        'spreadsheet_id': spreadsheet_id,
        'spreadsheet_url': spreadsheet_url
    }


# ─────────────────────────────────────────────────────────────
# Tab Builders
# ─────────────────────────────────────────────────────────────

def _build_all_tabs(categorized: dict, pages: list, domain: str) -> list:
    """Build the row data for all 12 tabs."""

    # Flatten categorized into quick lookup
    usability = categorized.get('usability', {})
    accessibility = categorized.get('accessibility', {})

    # Pre-process pages for detail tabs
    redirect_pages = []
    error_4xx_pages = []
    index_management_pages = []
    title_over_65_pages = []
    title_missing_pages = []
    duplicate_title_pages = []
    duplicate_desc_pages = []
    desc_missing_pages = []
    desc_over_155_pages = []
    h1_missing_pages = []
    h1_multiple_pages = []
    h2_missing_pages = []
    h2_duplicate_pages = []
    image_large_pages = []
    orphan_url_pages = []
    image_alt_missing_pages = []

    for page in pages:
        url = page.get('url', '')
        status_code = page.get('status_code', 200)
        meta = page.get('meta', {})
        checks = page.get('checks', page.get('dfs_checks', page.get('issues', {})))
        if not checks:
            checks = {}

        title = meta.get('title', '') or page.get('title', '') or ''
        desc = meta.get('description', '') or page.get('description', '') or ''
        h1_list = meta.get('h1', []) or page.get('h1', []) or []
        h2_list = meta.get('h2', []) or page.get('h2', []) or []
        if isinstance(h1_list, str):
            h1_list = [h1_list]
        if isinstance(h2_list, str):
            h2_list = [h2_list]
        
        content_type = page.get('content_type', '')
        is_html = 'text/html' in content_type if content_type else True  # Default True for DataForSEO pages
        is_image = content_type.startswith('image/') if content_type else False
        indexability = page.get('indexability', '')
        indexability_status = page.get('indexability_status', '')
        redirect_url = page.get('redirect_url', '')

        # Redirects
        if checks.get('is_redirect') or (300 <= status_code < 400):
            redirect_pages.append({'url': url, 'redirect_url': redirect_url or url})

        # 4xx errors
        if status_code >= 400 and status_code < 500:
            error_4xx_pages.append({'url': url, 'status_code': status_code})

        # Index Management (Non-Indexable pages)
        if indexability == 'Non-Indexable' or checks.get('is_marked_as_noindex'):
            index_management_pages.append({'url': url, 'reason': indexability_status or 'noindex'})

        # Title missing
        if is_html and status_code == 200 and not title:
            title_missing_pages.append({'url': url})

        # Title over 65
        if len(title) > 65:
            title_over_65_pages.append({'url': url, 'title': title})

        # Duplicate titles
        if checks.get('duplicate_title') or checks.get('duplicate_title_tag'):
            duplicate_title_pages.append({'url': url, 'title': title})

        # Duplicate descriptions
        if checks.get('duplicate_description'):
            duplicate_desc_pages.append({'url': url})

        # Description missing
        if is_html and status_code == 200 and (not desc or len(desc) < 5):
            desc_missing_pages.append({'url': url})

        # Description over 155
        if len(desc) > 155:
            desc_over_155_pages.append({
                'url': url,
                'description': desc[:100] + '...' if len(desc) > 100 else desc,
                'characters': len(desc),
                'pixel': int(len(desc) * 6.5)  # Rough pixel estimate
            })

        # H1 missing
        if is_html and status_code == 200 and (checks.get('no_h1') or checks.get('no_h1_tag') or len(h1_list) == 0):
            h1_missing_pages.append({'url': url})

        # H1 multiple
        if len(h1_list) > 1:
            h1_multiple_pages.append({'url': url})

        # H2 missing
        if is_html and status_code == 200 and len(h2_list) == 0:
            h2_missing_pages.append({'url': url})

        # Large images
        if is_image:
            size_bytes = page.get('size_bytes', 0) or 0
            if size_bytes > 102400:
                image_large_pages.append({'url': url})
        else:
            images_count = page.get('images_count', 0) or meta.get('images_count', 0) or 1
            images_size = page.get('images_size', 0) or meta.get('images_size', 0) or 0
            if images_count > 0 and (images_size / max(images_count, 1)) > 102400:
                image_large_pages.append({'url': url})

        # Orphan URLs
        if is_html and status_code == 200 and checks.get('is_orphan_page'):
            orphan_url_pages.append({'url': url})

        # Image alt missing
        if checks.get('no_image_alt'):
            image_alt_missing_pages.append({'url': url})

    # Also pull from categorized items (some come from summary, not pages — e.g. sitemap)
    # Use categorized items as fallback if page-level parsing missed some
    def _extend_from_categorized(target_list, cat_key, section='usability'):
        source = categorized.get(section, {}).get(cat_key, {})
        existing_urls = {item.get('url', item) if isinstance(item, dict) else item for item in target_list}
        for item_url in (source.get('items', []) or []):
            if isinstance(item_url, str) and item_url not in existing_urls:
                target_list.append({'url': item_url})

    _extend_from_categorized(redirect_pages, 'links_redirect_3xx')
    _extend_from_categorized(image_alt_missing_pages, 'image_alt_missing', 'accessibility')
    _extend_from_categorized(image_large_pages, 'images_large', 'accessibility')
    _extend_from_categorized(orphan_url_pages, 'orphan_urls')

    # For CSV imports: pull duplicate data from categorized (cross-page detection)
    def _extend_duplicates(target_list, cat_key, title_key=None):
        source = categorized.get('usability', {}).get(cat_key, {})
        existing_urls = {item.get('url', item) if isinstance(item, dict) else item for item in target_list}
        for item_url in (source.get('items', []) or []):
            if isinstance(item_url, str) and item_url not in existing_urls:
                entry = {'url': item_url}
                if title_key:
                    entry[title_key] = ''  # We don't have the value from categorized items
                target_list.append(entry)

    _extend_duplicates(duplicate_title_pages, 'title_duplicate', 'title')
    _extend_duplicates(duplicate_desc_pages, 'desc_duplicate')

    # H2 Duplicate — pull from categorized (it's built during CSV parse cross-page detection)
    h2_dup_source = categorized.get('usability', {}).get('h2_duplicate', {})
    for item_url in (h2_dup_source.get('items', []) or []):
        if isinstance(item_url, str):
            h2_duplicate_pages.append({'url': item_url, 'h2': ''})

    # ── Build tabs ──
    tabs = []

    # 1. Overview
    tabs.append(_build_overview_tab(categorized, {
        'redirection_3xx': len(redirect_pages),
        'client_errors_4xx': len(error_4xx_pages),
        'index_management': len(index_management_pages),
        'title_over_65': len(title_over_65_pages),
        'title_duplicate': len(duplicate_title_pages),
        'desc_duplicate': len(duplicate_desc_pages),
        'title_missing': len(title_missing_pages) or usability.get('title_missing', {}).get('issues', 0),
        'desc_missing': len(desc_missing_pages),
        'desc_over_155': len(desc_over_155_pages),
        'h1_missing': len(h1_missing_pages),
        'h1_multiple': len(h1_multiple_pages),
        'h2_missing': len(h2_missing_pages) or usability.get('h2_missing', {}).get('issues', 0),
        'h2_duplicate': len(h2_duplicate_pages),
        'images_large': len(image_large_pages),
        'orphan_urls': len(orphan_url_pages) or usability.get('orphan_urls', {}).get('issues', 0),
        'image_alt_missing': len(image_alt_missing_pages),
    }, domain))

    # 2. Redirection 3xx
    rows_3xx = [['Redirection 3xx', ''], ['Address', 'Redirect URL']]
    for p in redirect_pages:
        rows_3xx.append([p.get('url', ''), p.get('redirect_url', '')])
    tabs.append({'title': 'Redirection 3xx', 'rows': rows_3xx})

    # 3. Client Error(4xx)
    rows_4xx = [['Client Error(4xx)', ''], ['Address', 'Status Code']]
    for p in error_4xx_pages:
        rows_4xx.append([p.get('url', ''), p.get('status_code', '')])
    tabs.append({'title': 'Client Error(4xx)', 'rows': rows_4xx})

    # 4. Index Management
    rows_idx = [['Index Management', ''], ['Address', 'Indexability Status']]
    for p in index_management_pages:
        rows_idx.append([p.get('url', ''), p.get('reason', '')])
    tabs.append({'title': 'Index Management', 'rows': rows_idx})

    # 5. Page Titles Over 65 Characters
    rows_t65 = [['Page Titles Over 65 Characters', ''], ['Address', 'Title']]
    for p in title_over_65_pages:
        rows_t65.append([p.get('url', ''), p.get('title', '')])
    tabs.append({'title': 'Titles Over 65', 'rows': rows_t65})

    # 6. Duplicate Titles
    rows_dup_t = [['Duplicate Titles', ''], ['Address', 'Title']]
    for p in duplicate_title_pages:
        rows_dup_t.append([p.get('url', ''), p.get('title', '')])
    tabs.append({'title': 'Duplicate Titles', 'rows': rows_dup_t})

    # 7. Duplicate Description
    rows_dup_d = [['Duplicate Description', ''], ['Address']]
    for p in duplicate_desc_pages:
        rows_dup_d.append([p.get('url', '')])
    tabs.append({'title': 'Duplicate Description', 'rows': rows_dup_d})

    # 8. Meta Titles Missing
    rows_tm = [['Meta Titles Missing', ''], ['Address']]
    for p in title_missing_pages:
        rows_tm.append([p.get('url', '')])
    tabs.append({'title': 'Meta Titles Missing', 'rows': rows_tm})

    # 9. Meta Description Missing
    rows_dm = [['Meta Description Missing', ''], ['Address']]
    for p in desc_missing_pages:
        rows_dm.append([p.get('url', '')])
    tabs.append({'title': 'Desc Missing', 'rows': rows_dm})

    # 10. Meta Description Over 155
    rows_d155 = [['Meta Description Over 155', '', '', ''], ['Address', 'Description', 'Characters', 'Pixel']]
    for p in desc_over_155_pages:
        rows_d155.append([p.get('url', ''), p.get('description', ''), p.get('characters', ''), p.get('pixel', '')])
    tabs.append({'title': 'Desc Over 155', 'rows': rows_d155})

    # 11. H1 Missing
    rows_h1m = [['H1 Missing', ''], ['Address']]
    for p in h1_missing_pages:
        rows_h1m.append([p.get('url', '')])
    tabs.append({'title': 'H1 Missing', 'rows': rows_h1m})

    # 12. H1 Multiple
    rows_h1x = [['H1 Multiple', ''], ['Address']]
    for p in h1_multiple_pages:
        rows_h1x.append([p.get('url', '')])
    tabs.append({'title': 'H1 Multiple', 'rows': rows_h1x})

    # 13. H2 Missing
    rows_h2m = [['H2 Missing', ''], ['Address']]
    for p in h2_missing_pages:
        rows_h2m.append([p.get('url', '')])
    tabs.append({'title': 'H2 Missing', 'rows': rows_h2m})

    # 14. H2 Duplicate (NEW)
    rows_h2d = [['H2 Duplicate', ''], ['Address', 'H2']]
    for p in h2_duplicate_pages:
        rows_h2d.append([p.get('url', ''), p.get('h2', '')])
    tabs.append({'title': 'H2 Duplicate', 'rows': rows_h2d})

    # 15. Image Over 100 KB
    rows_img = [['Image Over 100 KB', ''], ['Address']]
    for p in image_large_pages:
        rows_img.append([p.get('url', '')])
    tabs.append({'title': 'Image Over 100 KB', 'rows': rows_img})

    # 16. Orphan URLs
    rows_orphan = [['Orphan URLs', ''], ['Address']]
    for p in orphan_url_pages:
        rows_orphan.append([p.get('url', '')])
    tabs.append({'title': 'Orphan URLs', 'rows': rows_orphan})

    # 17. Image Alt Text Missing
    rows_alt = [['Image Alt Text Missing', ''], ['Address']]
    for p in image_alt_missing_pages:
        rows_alt.append([p.get('url', '')])
    tabs.append({'title': 'Image Alt Text Missing', 'rows': rows_alt})

    return tabs


def _build_overview_tab(categorized: dict, counts: dict, domain: str) -> dict:
    """Build the Overview summary tab matching the reference."""

    rows = [
        ['Audit Optimization Recommendations & Implementations', '', '', '', '', ''],
        ['Area Of Improvements Checklist', 'Audit Notes', 'Recommendation Notes', 'Recommended Task', 'Priority', 'Status'],
    ]

    # Order matches the reference screenshot
    overview_keys = [
        'redirection_3xx', 'client_errors_4xx', 'index_management',
        'title_over_65', 'title_duplicate', 'desc_duplicate',
        'title_missing', 'desc_missing', 'desc_over_155',
        'h1_missing', 'h1_multiple', 'h2_missing', 'h2_duplicate',
        'images_large', 'orphan_urls', 'image_alt_missing'
    ]

    for key in overview_keys:
        rec = RECOMMENDATIONS.get(key, {})
        count = counts.get(key, 0)
        audit_note = str(count) if count > 0 else 'OK'
        recommendation = rec.get('rec', '')
        priority = rec.get('priority', 'High')
        status = 'Status'  # Placeholder dropdown-like

        rows.append([
            rec.get('label', key.replace('_', ' ').title()),
            audit_note,
            recommendation,
            rec.get('task', ''),
            priority,
            status,
        ])

    return {'title': 'Overview', 'rows': rows}
