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
    """Fetches live metrics from GSC and GA4 for specific properties."""
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
        start_date = data.get('start_date', '30daysAgo')
        end_date = data.get('end_date', 'today')
        
        if not gsc_property and not ga4_property:
            return jsonify({'error': 'Must provide gsc_property or ga4_property'}), 400
            
        # Get the integration credentials
        integration = client.table('agency_integrations').select('*').eq('user_id', user_id).eq('provider', 'google').execute()
        if not integration.data:
            return jsonify({'error': 'Google integration not found'}), 404
            
        refresh_token = integration.data[0].get('refresh_token')
        if not refresh_token:
            return jsonify({'error': 'No refresh token available'}), 400
            
        # Reconstruct credentials (client_id and secret needed)
        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )
        
        results = {'gsc': None, 'ga4': None}
        
        # 1. Fetch GSC Metrics
        if gsc_property:
            try:
                gsc_service = build('searchconsole', 'v1', credentials=creds)
                request_body = {
                    'startDate': start_date,
                    'endDate': end_date,
                    'dimensions': ['date']
                }
                response = gsc_service.searchanalytics().query(siteUrl=gsc_property, body=request_body).execute()
                
                rows = response.get('rows', [])
                total_clicks = sum(row['clicks'] for row in rows)
                total_impressions = sum(row['impressions'] for row in rows)
                avg_position = sum(row['position'] for row in rows) / len(rows) if rows else 0
                avg_ctr = sum(row['ctr'] for row in rows) / len(rows) if rows else 0
                
                results['gsc'] = {
                    'clicks': total_clicks,
                    'impressions': total_impressions,
                    'ctr': avg_ctr,
                    'position': avg_position,
                    'timeseries': rows
                }
            except Exception as e:
                logger.error(f"GSC Metrics Error: {e}")
                results['gsc'] = {'error': str(e)}
                
        # 2. Fetch GA4 Metrics
        if ga4_property:
            try:
                ga_data = build('analyticsdata', 'v1beta', credentials=creds)
                # GA4 properties are structured as "properties/12345"
                if not ga4_property.startswith('properties/'):
                    ga4_property = f'properties/{ga4_property}'
                    
                request_body = {
                    'dateRanges': [{'startDate': start_date, 'endDate': end_date}],
                    'metrics': [
                        {'name': 'activeUsers'},
                        {'name': 'sessions'},
                        {'name': 'conversions'}
                    ]
                }
                response = ga_data.properties().runReport(property=ga4_property, body=request_body).execute()
                
                rows = response.get('rows', [])
                if rows:
                    metric_values = rows[0].get('metricValues', [])
                    results['ga4'] = {
                        'activeUsers': int(metric_values[0].get('value', 0)) if len(metric_values) > 0 else 0,
                        'sessions': int(metric_values[1].get('value', 0)) if len(metric_values) > 1 else 0,
                        'conversions': float(metric_values[2].get('value', 0)) if len(metric_values) > 2 else 0
                    }
                else:
                    results['ga4'] = {'activeUsers': 0, 'sessions': 0, 'conversions': 0}
            except Exception as e:
                logger.error(f"GA4 Metrics Error: {e}")
                results['ga4'] = {'error': str(e)}
                
        return jsonify(results)
        
    except Exception as e:
        logger.error(f"Metrics Endpoint Error: {e}")
        return jsonify({'error': str(e)}), 500
