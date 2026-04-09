import os
import json
import logging
from flask import Blueprint, request, jsonify, redirect, url_for, session
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# NOTE: supabase, supabase_admin, etc. are imported lazily inside route
# functions to avoid circular imports with api.index

logger = logging.getLogger(__name__)

# Allow OAuth over HTTP for local development only
if not os.getenv('RAILWAY_ENVIRONMENT') and not os.getenv('RAILWAY_PUBLIC_DOMAIN'):
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
# Allow Google to return extra scopes (e.g. drive from Slides feature)
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

google_integration_bp = Blueprint('google_integration', __name__)

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

def get_client_config():
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    if not client_id or not client_secret:
        raise ValueError("Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET")
    
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
        }
    }

def get_redirect_uri(request):
    # Dynamically determine the redirect URI based on the request host
    protocol = "https" if request.is_secure or request.headers.get('X-Forwarded-Proto', 'http') == 'https' else "http"
    host = request.headers.get('Host')
    return f"{protocol}://{host}/api/google/callback"

@google_integration_bp.route('/api/google/auth', methods=['GET'])
def google_auth():
    """Initiates the OAuth flow."""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Missing user_id'}), 400
        
    try:
        client_config = get_client_config()
        redirect_uri = get_redirect_uri(request)
        
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        
        # We pass the user_id in the state parameter
        auth_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=user_id
        )
        
        # Store the code_verifier in session for PKCE (required by newer google-auth-oauthlib)
        session['google_code_verifier'] = flow.code_verifier
        
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Google Auth Error: {e}")
        return jsonify({'error': str(e)}), 500

@google_integration_bp.route('/api/google/callback', methods=['GET'])
def google_callback():
    """Handles the OAuth callback from Google."""
    try:
        state = request.args.get('state') # This is the user_id
        if not state:
            return "Missing state (user_id)", 400
            
        user_id = state
        
        client_config = get_client_config()
        redirect_uri = get_redirect_uri(request)
        
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        
        # Restore the PKCE code_verifier from the session
        code_verifier = session.pop('google_code_verifier', None)
        if code_verifier:
            flow.code_verifier = code_verifier
        
        # Use the full URL to fetch the token
        authorization_response = request.url
        # If running behind a proxy (like Railway), the URL might be http but callback expects https
        if "http://" in authorization_response and "https://" in redirect_uri:
            authorization_response = authorization_response.replace("http://", "https://")
            
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        
        # Get the email of the connected account
        try:
            from googleapiclient.discovery import build
            oauth2_client = build('oauth2', 'v2', credentials=credentials)
            user_info = oauth2_client.userinfo().get().execute()
            connected_email = user_info.get('email', '')
        except Exception as e:
            logger.error(f"Failed to fetch user email: {e}")
            connected_email = 'Unknown'
        
        # Use supabase_admin to save the credentials
        from api.index import supabase, supabase_admin
        client = supabase_admin or supabase
        
        # Check if an integration already exists for this user
        existing = client.table('agency_integrations').select('*').eq('user_id', user_id).eq('provider', 'google').execute()
        
        data = {
            'user_id': user_id,
            'provider': 'google',
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'connected_email': connected_email
        }
        
        if existing.data:
            # If refresh_token is None (happens if not prompted for consent again), keep the old one
            if not credentials.refresh_token:
                data.pop('refresh_token', None)
            client.table('agency_integrations').update(data).eq('id', existing.data[0]['id']).execute()
            integration_id = existing.data[0]['id']
        else:
            if not credentials.refresh_token:
                return "Google did not provide a refresh token. Please disconnect the app in your Google account and try again.", 400
            result = client.table('agency_integrations').insert(data).execute()
            integration_id = result.data[0]['id']
            
        # Redirect back to settings page
        return redirect('/dashboard?google_connected=true')

    except Exception as e:
        logger.error(f"Google Callback Error: {e}")
        return f"Authentication failed: {str(e)}", 500

@google_integration_bp.route('/api/google/sync-properties', methods=['POST'])
def sync_google_properties():
    """Fetches GSC and GA4 properties and saves them to the DB."""
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
        
    try:
        from api.index import supabase, supabase_admin
        user_id = session['user']['id']
        client = supabase_admin or supabase
        
        # Get the integration
        integration = client.table('agency_integrations').select('*').eq('user_id', user_id).eq('provider', 'google').execute()
        if not integration.data:
            return jsonify({'error': 'Google integration not found. Please connect your Google account first.'}), 404
            
        refresh_token = integration.data[0].get('refresh_token')
        if not refresh_token:
            return jsonify({'error': 'No refresh token available. Please reconnect your Google account.'}), 400
            
        integration_id = integration.data[0]['id']
        
        # Reconstruct credentials
        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )
        
        synced_properties = []
        errors = []
        
        # 1. Fetch GSC Properties
        try:
            gsc_service = build('searchconsole', 'v1', credentials=creds)
            site_list = gsc_service.sites().list().execute()
            sites = site_list.get('siteEntry', [])
            
            for site in sites:
                site_url = site.get('siteUrl')
                existing = client.table('connected_properties').select('*').eq('integration_id', integration_id).eq('property_type', 'gsc').eq('property_url_or_id', site_url).execute()
                if not existing.data:
                    client.table('connected_properties').insert({
                        'integration_id': integration_id,
                        'property_type': 'gsc',
                        'property_url_or_id': site_url,
                        'property_name': site_url
                    }).execute()
                synced_properties.append({'type': 'gsc', 'name': site_url, 'id': site_url})
        except Exception as e:
            logger.error(f"Error fetching GSC properties: {e}")
            errors.append(f"GSC: {str(e)}")
            
        # 2. Fetch GA4 Properties
        try:
            # Try v1beta first, then v1alpha
            ga_admin = None
            for version in ['v1beta', 'v1alpha']:
                try:
                    ga_admin = build('analyticsadmin', version, credentials=creds)
                    break
                except Exception:
                    continue
            
            if ga_admin:
                account_summaries = ga_admin.accountSummaries().list().execute()
                for account in account_summaries.get('accountSummaries', []):
                    for property_summary in account.get('propertySummaries', []):
                        prop_id = property_summary.get('property')
                        prop_name = property_summary.get('displayName')
                        
                        existing = client.table('connected_properties').select('*').eq('integration_id', integration_id).eq('property_type', 'ga4').eq('property_url_or_id', prop_id).execute()
                        if not existing.data:
                            client.table('connected_properties').insert({
                                'integration_id': integration_id,
                                'property_type': 'ga4',
                                'property_url_or_id': prop_id,
                                'property_name': prop_name
                            }).execute()
                        synced_properties.append({'type': 'ga4', 'name': prop_name, 'id': prop_id})
            else:
                errors.append("GA4: Could not initialize Analytics Admin API")
        except Exception as e:
            logger.error(f"Error fetching GA4 properties: {e}")
            errors.append(f"GA4: {str(e)}")
            
        result = {
            'success': True, 
            'message': f'Synced {len(synced_properties)} properties',
            'properties': synced_properties
        }
        if errors:
            result['warnings'] = errors
            
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Sync Properties Error: {e}")
        return jsonify({'error': str(e)}), 500

@google_integration_bp.route('/api/google/properties', methods=['GET'])
def get_google_properties():
    """Returns the list of synced GSC and GA4 properties from the database."""
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
        
    try:
        from api.index import supabase, supabase_admin
        user_id = session['user']['id']
        client = supabase_admin or supabase
        
        # Get the integration to find the ID
        integration = client.table('agency_integrations').select('id').eq('user_id', user_id).eq('provider', 'google').execute()
        if not integration.data:
            return jsonify({'gsc': [], 'ga4': []})
            
        integration_id = integration.data[0]['id']
        
        # Get all properties for this integration
        properties = client.table('connected_properties').select('*').eq('integration_id', integration_id).execute()
        
        gsc_props = [p for p in properties.data if p['property_type'] == 'gsc']
        ga4_props = [p for p in properties.data if p['property_type'] == 'ga4']
        
        return jsonify({
            'gsc': gsc_props,
            'ga4': ga4_props
        })
        
    except Exception as e:
        logger.error(f"Get Properties Error: {e}")
        return jsonify({'error': str(e)}), 500

@google_integration_bp.route('/api/google/metrics', methods=['POST'])
def get_google_metrics():
    """Fetches comprehensive metrics from GSC and GA4 for the analytics dashboard."""
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
        
    try:
        from api.index import supabase, supabase_admin
        user_id = session['user']['id']
        client = supabase_admin or supabase
        
        # Parse request body
        data = request.json or {}
        gsc_property = data.get('gsc_property')
        ga4_property = data.get('ga4_property')
        duration = data.get('duration', '1m')
        
        if not gsc_property and not ga4_property:
            return jsonify({'error': 'Must provide gsc_property or ga4_property'}), 400
            
        # Get the integration credentials
        integration = client.table('agency_integrations').select('*').eq('user_id', user_id).eq('provider', 'google').execute()
        if not integration.data:
            return jsonify({'error': 'Google integration not found'}), 404
            
        refresh_token = integration.data[0].get('refresh_token')
        if not refresh_token:
            return jsonify({'error': 'No refresh token available'}), 400
            
        # Reconstruct credentials
        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )
        
        results = {'gsc': None, 'ga4': None}

        # =====================================================================
        # Compute date ranges dynamically
        # =====================================================================
        from datetime import datetime, timedelta
        
        duration_days_map = {
            '1m': 30,
            '3m': 90,
            '6m': 180,
            '12m': 365,
            'max': 480 # ~16 months for GSC max limit
        }
        days = duration_days_map.get(duration, 30)
        
        today = datetime.now().date()
        current_end = today.isoformat()
        current_start = (today - timedelta(days=days-1)).isoformat()
        prev_end = (today - timedelta(days=days)).isoformat()
        prev_start = (today - timedelta(days=(days*2)-1)).isoformat()
        
        results['meta'] = {
            'duration_days': days,
            'duration_label': f'vs previous {days} days',
            'current_start': current_start,
            'current_end': current_end,
            'prev_start': prev_start,
            'prev_end': prev_end
        }
        
        # =====================================================================
        # 1. GSC — Full Data Fetch
        # =====================================================================
        if gsc_property:
            try:
                gsc_service = build('searchconsole', 'v1', credentials=creds)
                
                def _gsc_query(dimensions, start, end, row_limit=5000):
                    """Helper to run a GSC query."""
                    body = {
                        'startDate': start,
                        'endDate': end,
                        'dimensions': dimensions,
                        'rowLimit': row_limit
                    }
                    resp = gsc_service.searchanalytics().query(siteUrl=gsc_property, body=body).execute()
                    return resp.get('rows', [])
                
                # a) Time-series (current period)
                ts_rows = _gsc_query(['date'], current_start, current_end)
                timeseries = []
                for row in ts_rows:
                    timeseries.append({
                        'date': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions'],
                        'ctr': row['ctr'],
                        'position': row['position']
                    })
                
                # b) Time-series (previous period)    
                prev_ts_rows = _gsc_query(['date'], prev_start, prev_end)
                prev_timeseries = []
                for row in prev_ts_rows:
                    prev_timeseries.append({
                        'date': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions'],
                        'ctr': row['ctr'],
                        'position': row['position']
                    })
                
                # c) Totals (current)
                total_clicks = sum(r['clicks'] for r in timeseries)
                total_impressions = sum(r['impressions'] for r in timeseries)
                avg_ctr = sum(r['ctr'] for r in timeseries) / len(timeseries) if timeseries else 0
                avg_position = sum(r['position'] for r in timeseries) / len(timeseries) if timeseries else 0
                
                # d) Totals (previous) for comparison
                prev_clicks = sum(r['clicks'] for r in prev_timeseries)
                prev_impressions = sum(r['impressions'] for r in prev_timeseries)
                prev_ctr = sum(r['ctr'] for r in prev_ts_rows) / len(prev_ts_rows) if prev_ts_rows else 0
                prev_position = sum(r['position'] for r in prev_ts_rows) / len(prev_ts_rows) if prev_ts_rows else 0
                
                # e) Device breakdown
                device_rows = _gsc_query(['device'], current_start, current_end)
                devices = []
                for row in device_rows:
                    devices.append({
                        'device': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions']
                    })
                
                # f) Country breakdown (top 10)
                country_rows = _gsc_query(['country'], current_start, current_end)
                # Sort by impressions descending, take top 10
                country_rows.sort(key=lambda x: x['impressions'], reverse=True)
                countries = []
                for row in country_rows[:10]:
                    countries.append({
                        'country': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions'],
                        'ctr': row['ctr'],
                        'position': row['position']
                    })
                
                # g) Top queries (top 25)
                query_rows = _gsc_query(['query'], current_start, current_end)
                query_rows.sort(key=lambda x: x['clicks'], reverse=True)
                queries = []
                for row in query_rows[:25]:
                    queries.append({
                        'query': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions'],
                        'ctr': row['ctr'],
                        'position': row['position']
                    })
                
                # h) Top pages (top 25)
                page_rows = _gsc_query(['page'], current_start, current_end)
                page_rows.sort(key=lambda x: x['clicks'], reverse=True)
                pages = []
                for row in page_rows[:25]:
                    pages.append({
                        'page': row['keys'][0],
                        'clicks': row['clicks'],
                        'impressions': row['impressions'],
                        'ctr': row['ctr'],
                        'position': row['position']
                    })
                
                # i) Unique queries & pages counts
                unique_queries = len(query_rows)
                unique_pages = len(page_rows)

                results['gsc'] = {
                    'clicks': total_clicks,
                    'impressions': total_impressions,
                    'ctr': avg_ctr,
                    'position': avg_position,
                    'uniqueQueries': unique_queries,
                    'uniquePages': unique_pages,
                    'prev': {
                        'clicks': prev_clicks,
                        'impressions': prev_impressions,
                        'ctr': prev_ctr,
                        'position': prev_position
                    },
                    'timeseries': timeseries,
                    'prevTimeseries': prev_timeseries,
                    'devices': devices,
                    'countries': countries,
                    'queries': queries,
                    'pages': pages
                }
            except Exception as e:
                logger.error(f"GSC Metrics Error: {e}")
                results['gsc'] = {'error': str(e)}
                
        # =====================================================================
        # 2. GA4 — Full Data Fetch
        # =====================================================================
        if ga4_property:
            try:
                ga_data = build('analyticsdata', 'v1beta', credentials=creds)
                if not ga4_property.startswith('properties/'):
                    ga4_property = f'properties/{ga4_property}'
                
                def _ga4_report(metrics, dimensions=None, start=current_start, end=current_end, limit=10):
                    """Helper to run a GA4 report."""
                    body = {
                        'dateRanges': [{'startDate': start, 'endDate': end}],
                        'metrics': [{'name': m} for m in metrics],
                        'limit': limit
                    }
                    if dimensions:
                        body['dimensions'] = [{'name': d} for d in dimensions]
                    resp = ga_data.properties().runReport(property=ga4_property, body=body).execute()
                    return resp
                
                def _parse_value(val, is_int=True):
                    """Parse a GA4 metric value."""
                    try:
                        return int(float(val)) if is_int else round(float(val), 2)
                    except (ValueError, TypeError):
                        return 0
                
                # a) KPI Totals — current period (core metrics only)
                core_metrics = [
                    'activeUsers', 'sessions', 'engagedSessions', 'bounceRate',
                    'averageSessionDuration', 'screenPageViews'
                ]
                kpi_resp = _ga4_report(core_metrics, start=current_start, end=current_end, limit=1)
                kpi_rows = kpi_resp.get('rows', [])
                kpi_vals = kpi_rows[0].get('metricValues', []) if kpi_rows else []
                
                def _kpi(idx, is_int=True):
                    return _parse_value(kpi_vals[idx].get('value', 0), is_int) if idx < len(kpi_vals) else 0
                
                kpi = {
                    'activeUsers': _kpi(0),
                    'sessions': _kpi(1),
                    'engagedSessions': _kpi(2),
                    'bounceRate': _kpi(3, False),
                    'avgSessionDuration': _kpi(4, False),
                    'pageViews': _kpi(5),
                    'conversions': 0,
                    'totalRevenue': 0,
                    'transactions': 0,
                    'purchasers': 0
                }
                
                # Try optional e-commerce / key events metrics (may not exist)
                try:
                    ecom_resp = _ga4_report(['keyEvents', 'totalRevenue', 'transactions'], start=current_start, end=current_end, limit=1)
                    ecom_rows = ecom_resp.get('rows', [])
                    if ecom_rows:
                        ev = ecom_rows[0].get('metricValues', [])
                        kpi['conversions'] = _parse_value(ev[0].get('value', 0), False) if len(ev) > 0 else 0
                        kpi['totalRevenue'] = _parse_value(ev[1].get('value', 0), False) if len(ev) > 1 else 0
                        kpi['transactions'] = _parse_value(ev[2].get('value', 0)) if len(ev) > 2 else 0
                except Exception as ecom_err:
                    logger.warning(f"GA4 e-commerce metrics not available: {ecom_err}")
                
                # b) KPI Totals — previous period
                prev_kpi_resp = _ga4_report(core_metrics, start=prev_start, end=prev_end, limit=1)
                prev_kpi_rows = prev_kpi_resp.get('rows', [])
                prev_kpi_vals = prev_kpi_rows[0].get('metricValues', []) if prev_kpi_rows else []
                
                def _prev_kpi(idx, is_int=True):
                    return _parse_value(prev_kpi_vals[idx].get('value', 0), is_int) if idx < len(prev_kpi_vals) else 0
                
                prev_kpi = {
                    'activeUsers': _prev_kpi(0),
                    'sessions': _prev_kpi(1),
                    'engagedSessions': _prev_kpi(2),
                    'bounceRate': _prev_kpi(3, False),
                    'avgSessionDuration': _prev_kpi(4, False),
                    'pageViews': _prev_kpi(5),
                    'conversions': 0,
                    'totalRevenue': 0,
                    'transactions': 0,
                    'purchasers': 0
                }
                
                # c) Time-series (sessions by day) — current
                ts_resp = _ga4_report(['sessions', 'activeUsers'], dimensions=['date'], limit=None) # Set limit=None for full timeseries
                ga4_timeseries = []
                for row in ts_resp.get('rows', []):
                    raw_date = row['dimensionValues'][0]['value']  # YYYYMMDD
                    formatted = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    ga4_timeseries.append({
                        'date': formatted,
                        'sessions': _parse_value(row['metricValues'][0]['value']),
                        'activeUsers': _parse_value(row['metricValues'][1]['value'])
                    })
                ga4_timeseries.sort(key=lambda x: x['date'])
                
                # d) Time-series — previous period
                prev_ts_resp = _ga4_report(['sessions', 'activeUsers'], dimensions=['date'], start=prev_start, end=prev_end, limit=None)
                ga4_prev_timeseries = []
                for row in prev_ts_resp.get('rows', []):
                    raw_date = row['dimensionValues'][0]['value']
                    formatted = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    ga4_prev_timeseries.append({
                        'date': formatted,
                        'sessions': _parse_value(row['metricValues'][0]['value']),
                        'activeUsers': _parse_value(row['metricValues'][1]['value'])
                    })
                ga4_prev_timeseries.sort(key=lambda x: x['date'])
                
                # e) Device breakdown
                device_resp = _ga4_report(['sessions'], dimensions=['deviceCategory'], limit=5)
                ga4_devices = []
                for row in device_resp.get('rows', []):
                    ga4_devices.append({
                        'device': row['dimensionValues'][0]['value'],
                        'sessions': _parse_value(row['metricValues'][0]['value'])
                    })
                
                # f) Country breakdown (top 10)
                country_resp = _ga4_report(['sessions', 'activeUsers'], dimensions=['country'], limit=10)
                ga4_countries = []
                for row in country_resp.get('rows', []):
                    ga4_countries.append({
                        'country': row['dimensionValues'][0]['value'],
                        'sessions': _parse_value(row['metricValues'][0]['value']),
                        'activeUsers': _parse_value(row['metricValues'][1]['value'])
                    })
                
                # g) Channel breakdown (top 10)
                channel_resp = _ga4_report(['sessions', 'activeUsers'], dimensions=['sessionDefaultChannelGroup'], limit=10)
                ga4_channels = []
                for row in channel_resp.get('rows', []):
                    ga4_channels.append({
                        'channel': row['dimensionValues'][0]['value'],
                        'sessions': _parse_value(row['metricValues'][0]['value']),
                        'activeUsers': _parse_value(row['metricValues'][1]['value'])
                    })
                
                # h) Top landing pages (top 15)
                lp_resp = _ga4_report(['sessions', 'activeUsers', 'bounceRate'], dimensions=['landingPagePlusQueryString'], limit=15)
                ga4_landing_pages = []
                for row in lp_resp.get('rows', []):
                    ga4_landing_pages.append({
                        'page': row['dimensionValues'][0]['value'],
                        'sessions': _parse_value(row['metricValues'][0]['value']),
                        'activeUsers': _parse_value(row['metricValues'][1]['value']),
                        'bounceRate': _parse_value(row['metricValues'][2]['value'], False)
                    })
                
                results['ga4'] = {
                    **kpi,
                    'prev': prev_kpi,
                    'timeseries': ga4_timeseries,
                    'prevTimeseries': ga4_prev_timeseries,
                    'devices': ga4_devices,
                    'countries': ga4_countries,
                    'channels': ga4_channels,
                    'landingPages': ga4_landing_pages
                }
            except Exception as e:
                logger.error(f"GA4 Metrics Error: {e}")
                results['ga4'] = {'error': str(e)}
                
        return jsonify(results)
        
    except Exception as e:
        logger.error(f"Metrics Endpoint Error: {e}")
        return jsonify({'error': str(e)}), 500

