#!/usr/bin/env python3
"""
SEO Agency Platform - Main API
Flask application with role-based authentication and multi-tenant support.
"""
import os
import logging
from datetime import datetime
from flask import Flask, jsonify, request, render_template, redirect, url_for, session
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps
from api.dataforseo_client import (
    start_onpage_audit,
    get_audit_status,
    get_audit_summary,
    get_audit_status,
    get_audit_summary,
    get_page_issues,
    get_domain_rank_overview
)
from api.utils import create_tasks_from_audit

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'public'),
    static_folder=os.path.join(BASE_DIR, 'public'),
    static_url_path=''
)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')
CORS(app)

# Supabase client
from supabase import create_client, Client

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

supabase: Client = None
supabase_admin: Client = None

# Use service role key as fallback if anon key not set
effective_key = SUPABASE_KEY or SUPABASE_SERVICE_KEY

if SUPABASE_URL and effective_key:
    supabase = create_client(SUPABASE_URL, effective_key)
    if SUPABASE_SERVICE_KEY:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    logger.info(f"Supabase client initialized (using {'anon' if SUPABASE_KEY else 'service_role'} key)")
else:
    logger.warning("Supabase credentials not found - running without database")

# =============================================================================
# ROLE DEFINITIONS
# =============================================================================

ROLES = {
    'admin': {
        'name': 'Administrator',
        'permissions': ['all']
    },
    'campaign_manager': {
        'name': 'Campaign Manager',
        'permissions': ['view_all_campaigns', 'assign_tasks', 'view_reports', 'manage_team']
    },
    'content_strategist': {
        'name': 'Content Strategist',
        'permissions': ['view_campaigns', 'manage_keywords', 'manage_content_calendar', 'create_briefs']
    },
    'content_creator': {
        'name': 'Content Creator',
        'permissions': ['view_assigned_tasks', 'create_content', 'submit_drafts']
    },
    'optimization_specialist': {
        'name': 'Optimization Specialist',
        'permissions': ['view_assigned_tasks', 'view_audits', 'fix_issues']
    },
    'link_builder': {
        'name': 'Link Builder',
        'permissions': ['view_assigned_tasks', 'manage_links', 'track_placements']
    },
    'reporting_manager': {
        'name': 'Reporting Manager',
        'permissions': ['view_all_campaigns', 'create_reports', 'export_data']
    },
    'viewer': {
        'name': 'Client Viewer',
        'permissions': ['view_own_campaign']
    }
}

# =============================================================================
# AUTH DECORATORS
# =============================================================================

def login_required(f):
    """Require user to be logged in."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def role_required(*roles):
    """Require user to have one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return jsonify({'error': 'Authentication required'}), 401
            user_role = session.get('user', {}).get('role', 'viewer')
            if user_role not in roles and user_role != 'admin':
                return jsonify({'error': 'Insufficient permissions'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def permission_required(permission):
    """Require user to have a specific permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return jsonify({'error': 'Authentication required'}), 401
            user_role = session.get('user', {}).get('role', 'viewer')
            role_perms = ROLES.get(user_role, {}).get('permissions', [])
            if 'all' not in role_perms and permission not in role_perms:
                return jsonify({'error': 'Insufficient permissions'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.route('/ping')
def ping():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'message': 'SEO Agency Platform API',
        'supabase_connected': supabase is not None
    })


@app.route('/')
def index():
    """Serve main page."""
    if 'user' in session:
        return redirect('/dashboard')
    return render_template('login.html')


@app.route('/dashboard')
@login_required
def dashboard():
    """Serve dashboard based on user role."""
    return render_template('dashboard.html')

# =============================================================================
# AUTH ROUTES
# =============================================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login with email/password via Supabase."""
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    if supabase is None:
        logger.error("Supabase client not initialized")
        return jsonify({'error': 'Database connection error. Please check server logs.'}), 500
    
    try:
        # Authenticate with Supabase
        response = supabase.auth.sign_in_with_password({
            'email': email,
            'password': password
        })
        
        user = response.user
        
        # Get user profile with role
        profile = supabase.table('profiles').select('*').eq('id', user.id).single().execute()
        
        # BACKFILL: If user has no organization, create one now
        if profile.data and not profile.data.get('organization_id'):
            try:
                # Reuse creation logic
                full_name = profile.data.get('full_name') or user.email.split('@')[0]
                org_name = f"{full_name}'s Org"
                slug = org_name.lower().replace(' ', '-').replace("'", "") + f"-{int(datetime.now().timestamp())}"
                
                admin = supabase_admin or supabase
                org_res = admin.table('organizations').insert({
                    'name': org_name,
                    'slug': slug,
                    'owner_id': user.id
                }).execute()
                
                if org_res.data:
                    org_id = org_res.data[0]['id']
                    
                    # 2. Update Profile with Org ID
                    updated_profile = admin.table('profiles').update({
                        'organization_id': org_id,
                        'role': 'admin'
                    }).eq('id', user.id).execute()
                    
                    # 3. MIGRATION: Adopt orphaned campaigns (Safe heuristics)
                    # If this is the "main" user (or first to migrate), give them the legacy data
                    # We check if this user effectively "owns" the legacy state
                    # For simplicity/safety in this specific context: Update ALL null-org campaigns
                    migration_res = admin.table('campaigns').update({'organization_id': org_id}).is_('organization_id', 'null').execute()
                    if migration_res.data:
                        logger.info(f"Migrated {len(migration_res.data)} orphaned campaigns to org {org_id}")

                    # Use updated profile data
                    if updated_profile.data:
                        profile = updated_profile
                        logger.info(f"Backfilled organization {org_id} for user {user.id}")
            except Exception as e:
                logger.error(f"Failed to backfill org for {user.email}: {e}")
        
        # Store in session
        session['user'] = {
            'id': user.id,
            'email': user.email,
            'role': profile.data.get('role', 'viewer') if profile.data else 'viewer',
            'organization_id': profile.data.get('organization_id') if profile.data else None,
            'full_name': profile.data.get('full_name') if profile.data else None
        }
        session['access_token'] = response.session.access_token
        
        return jsonify({
            'success': True,
            'user': session['user'],
            'role_info': ROLES.get(session['user']['role'], {})
        })
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'error': str(e)}), 401


@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """Register new user."""
    data = request.json
    email = data.get('email')
    password = data.get('password')
    full_name = data.get('full_name', '')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    try:
        # Create user in Supabase Auth with metadata
        response = supabase.auth.sign_up({
            'email': email,
            'password': password,
            'options': {
                'data': {
                    'full_name': full_name
                }
            }
        })
        
        user = response.user
        
        if not user:
            return jsonify({'error': 'Signup failed. Please try again.'}), 400
        
        # Profile is created automatically by trigger
        # NOW: Create Organization and assign it (Critical for data isolation)
        try:
            # Generate basic slug
            org_name = f"{full_name}'s Org" if full_name else "My Organization"
            slug = org_name.lower().replace(' ', '-').replace("'", "") + f"-{int(datetime.now().timestamp())}"
            
            # Use admin client to ensure we can create orgs and update profiles
            admin = supabase_admin or supabase
            
            # 1. Create Org
            org_res = admin.table('organizations').insert({
                'name': org_name,
                'slug': slug,
                'owner_id': user.id
            }).execute()
            
            if org_res.data:
                org_id = org_res.data[0]['id']
                
                # 2. Update Profile with Org ID
                admin.table('profiles').update({
                    'organization_id': org_id,
                    'role': 'admin' # First user is admin of their org
                }).eq('id', user.id).execute()
                
                logger.info(f"Created organization {org_id} for new user {user.id}")
                
        except Exception as e:
            logger.error(f"Failed to auto-create org for {email}: {e}")
            # Don't fail the whole signup, but log it. User will be caught by Login backfill.

        return jsonify({
            'success': True,
            'message': 'Account created! You can now sign in.'
        })
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Signup error: {error_msg}")
        
        # Parse common errors into user-friendly messages
        if 'already registered' in error_msg.lower() or 'already exists' in error_msg.lower():
            return jsonify({'error': 'An account with this email already exists. Please sign in.'}), 400
        elif 'duplicate key' in error_msg.lower() or 'profiles_pkey' in error_msg.lower():
            return jsonify({'error': 'Account already exists. Please sign in instead.'}), 400
        elif 'password' in error_msg.lower():
            return jsonify({'error': 'Password must be at least 6 characters.'}), 400
        else:
            return jsonify({'error': 'Signup failed. Please try again.'}), 400


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logout user."""
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/me')
@login_required
def get_current_user():
    """Get current user info."""
    return jsonify({
        'user': session.get('user'),
        'role_info': ROLES.get(session['user']['role'], {})
    })

# =============================================================================
# ORGANIZATION ROUTES
# =============================================================================

@app.route('/api/organizations', methods=['GET'])
@login_required
@role_required('admin')
def list_organizations():
    """List all organizations (admin only)."""
    try:
        response = supabase.table('organizations').select('*').execute()
        return jsonify({'organizations': response.data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/organizations', methods=['POST'])
@login_required
@role_required('admin')
def create_organization():
    """Create new organization."""
    data = request.json
    
    try:
        response = supabase.table('organizations').insert({
            'name': data.get('name'),
            'slug': data.get('slug'),
            'owner_id': session['user']['id']
        }).execute()
        
        return jsonify({'organization': response.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# CAMPAIGN ROUTES
# =============================================================================

@app.route('/api/campaigns', methods=['GET'])
@login_required
def list_campaigns():
    """List campaigns visible to user."""
    user = session['user']
    
    # Use admin client to bypass RLS (backend handles authorization)
    client = supabase_admin or supabase
    
    try:
        query = client.table('campaigns').select('*')
        
        # Filter by organization for non-admins
        if user['role'] != 'admin':
            if user.get('organization_id'):
                query = query.eq('organization_id', user['organization_id'])
            else:
                # CRITICAL: If no org ID, return nothing (prevent leak)
                return jsonify({'campaigns': []})
        
        response = query.order('created_at', desc=True).execute()
        return jsonify({'campaigns': response.data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/campaigns', methods=['POST'])
@login_required
@permission_required('view_all_campaigns')
def create_campaign():
    """Create new campaign."""
    data = request.json
    user = session['user']
    
    # Use admin client for write operations (bypasses RLS)
    client = supabase_admin or supabase
    
    try:
        response = client.table('campaigns').insert({
            'organization_id': user.get('organization_id'),
            'name': data.get('name'),
            'domain': data.get('domain'),
            'settings': data.get('settings', {}),
            'status': 'active'
        }).execute()
        
        return jsonify({'campaign': response.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/campaigns/<campaign_id>', methods=['GET'])
@login_required
def get_campaign(campaign_id):
    """Get single campaign."""
    client = supabase_admin or supabase
    try:
        response = client.table('campaigns').select('*').eq('id', campaign_id).single().execute()
        return jsonify({'campaign': response.data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/campaigns/<campaign_id>', methods=['PUT'])
@login_required
@permission_required('view_all_campaigns')
def update_campaign(campaign_id):
    """Update campaign."""
    data = request.json
    client = supabase_admin or supabase
    
    # Only include fields that are provided
    update_data = {}
    if 'name' in data:
        update_data['name'] = data['name']
    if 'domain' in data:
        update_data['domain'] = data['domain']
    if 'settings' in data:
        update_data['settings'] = data['settings']
    if 'status' in data:
        update_data['status'] = data['status']
    
    if not update_data:
        return jsonify({'error': 'No fields to update'}), 400
    
    try:
        response = client.table('campaigns').update(update_data).eq('id', campaign_id).execute()
        return jsonify({'campaign': response.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# TASK ROUTES
# =============================================================================

@app.route('/api/tasks', methods=['GET'])
@login_required
def list_tasks():
    """List tasks visible to user."""
    user = session['user']
    campaign_id = request.args.get('campaign_id')
    status = request.args.get('status')
    
    # Use admin client to bypass RLS
    client = supabase_admin or supabase
    
    try:
        query = client.table('tasks').select('*, campaigns(name, domain)')
        
        # Filter based on role
        if user['role'] not in ['admin', 'campaign_manager']:
            # Regular users see only their assigned tasks
            query = query.eq('assigned_to', user['id'])
        
        if campaign_id:
            query = query.eq('campaign_id', campaign_id)
        
        if status:
            query = query.eq('status', status)
        
        response = query.order('created_at', desc=True).execute()
        return jsonify({'tasks': response.data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tasks', methods=['POST'])
@login_required
@permission_required('assign_tasks')
def create_task():
    """Create new task."""
    data = request.json
    
    # Use admin client for write operations
    client = supabase_admin or supabase
    
    try:
        response = client.table('tasks').insert({
            'campaign_id': data.get('campaign_id'),
            'type': data.get('type'),  # technical, content, link_building, optimization
            'title': data.get('title'),
            'description': data.get('description'),
            'checklist': data.get('checklist', []),
            'assigned_to': data.get('assigned_to'),
            'assigned_role': data.get('assigned_role'),
            'priority': data.get('priority', 0),
            'due_date': data.get('due_date'),
            'status': 'pending'
        }).execute()
        
        return jsonify({'task': response.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tasks/<task_id>', methods=['PUT'])
@login_required
def update_task(task_id):
    """Update task (status, checklist, etc)."""
    data = request.json
    user = session['user']
    
    try:
        # First check if user can update this task
        task = supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        
        if not task.data:
            return jsonify({'error': 'Task not found'}), 404
        
        # Check permission
        if user['role'] not in ['admin', 'campaign_manager']:
            if task.data.get('assigned_to') != user['id']:
                return jsonify({'error': 'Not authorized'}), 403
        
        # Update
        update_data = {}
        if 'status' in data:
            update_data['status'] = data['status']
        if 'checklist' in data:
            update_data['checklist'] = data['checklist']
        if 'assigned_to' in data and user['role'] in ['admin', 'campaign_manager']:
            update_data['assigned_to'] = data['assigned_to']
        
        response = supabase.table('tasks').update(update_data).eq('id', task_id).execute()
        
        return jsonify({'task': response.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# AUDIT ROUTES
# =============================================================================

@app.route('/api/audits', methods=['GET'])
@login_required
def list_audits():
    """List audits for user's campaigns."""
    campaign_id = request.args.get('campaign_id')
    
    try:
        # Use admin client to bypass RLS or ensure context
        client = supabase_admin or supabase
        query = client.table('audits').select('*, campaigns(name, domain)')
        
        if campaign_id:
            query = query.eq('campaign_id', campaign_id)
        
        response = query.order('created_at', desc=True).execute()
        return jsonify({'audits': response.data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/audits', methods=['POST'])
@login_required
@permission_required('view_all_campaigns')
def create_audit():
    """Start a new audit."""
    data = request.json
    
    # Use admin client for write operations
    client = supabase_admin or supabase
    
    try:
        # Get campaign domain
        campaign = client.table('campaigns').select('domain').eq('id', data.get('campaign_id')).single().execute()
        if not campaign.data:
            return jsonify({'error': 'Campaign not found'}), 404
            
        domain = campaign.data['domain']
        
        # Start DataForSEO audit
        dfs_result = start_onpage_audit(domain)
        
        if not dfs_result.get('success'):
            return jsonify({'error': f"Failed to start audit: {dfs_result.get('error')}"}), 500
            
        task_id = dfs_result.get('task_id')

        # Create audit record
        response = client.table('audits').insert({
            'campaign_id': data.get('campaign_id'),
            'type': data.get('type', 'technical'),
            'status': 'crawling',
            'dataforseo_task_id': task_id,
            'results': {}
        }).execute()
        
        audit = response.data[0]
        
        return jsonify({'audit': audit, 'message': 'Audit started successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/audits/<audit_id>', methods=['GET'])
@login_required
def get_audit(audit_id):
    """Get audit status and results."""
    try:
        client = supabase_admin or supabase
        response = client.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
        audit = response.data
        
        # Lazy status check for running audits
        if audit['status'] == 'crawling' and audit.get('dataforseo_task_id'):
            task_id = audit['dataforseo_task_id']
            status = get_audit_status(task_id)
            
            if status.get('ready'):
                # Audit finished! Fetch results and update
                try:
                    # 1. Get Summary
                    summary = get_audit_summary(task_id)
                    
                    # 2. Get Page Issues
                    pages_result = get_page_issues(task_id, limit=100)
                    pages = pages_result.get('pages', [])
                    
                    # 3. Create Tasks
                    # Use admin client for writes if available
                    client = supabase_admin or supabase
                    create_tasks_from_audit(pages, audit['campaign_id'], client)
                    
                    # 4. Update Audit Record
                    update_data = {
                        'status': 'completed',
                        'results': {
                            'summary': summary.get('summary', {}),
                            'pages': pages
                        },
                        'summary': summary.get('summary', {})
                    }
                    
                    update_res = client.table('audits').update(update_data).eq('id', audit_id).execute()
                    audit = update_res.data[0] # Return updated audit
                    
                except Exception as e:
                     print(f"Error finalizing audit: {e}")
                     # Optionally set status to failed or keep crawling to retry
        
        return jsonify({'audit': audit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# COMPETITOR ROUTES
# =============================================================================

@app.route('/api/competitors/analyze', methods=['POST'])
@login_required
@permission_required('view_all_campaigns')
def analyze_competitors():
    """Analyze competitors against campaign domain."""
    data = request.json
    campaign_id = data.get('campaign_id')
    competitors = data.get('competitors', []) # List of domains
    
    if not campaign_id:
        return jsonify({'error': 'Campaign ID required'}), 400
        
    client = supabase_admin or supabase
    
    try:
        # Get campaign
        campaign_res = client.table('campaigns').select('*').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
            
        target_domain = campaign['domain']
        
        # 1. Fetch data for target domain
        target_stats = get_domain_rank_overview(target_domain)
        
        # 2. Fetch data for each competitor
        competitor_stats = []
        for comp_domain in competitors:
            if not comp_domain: continue
            stats = get_domain_rank_overview(comp_domain)
            competitor_stats.append(stats)
            
        # 3. Update campaign settings with this list (cache it)
        current_settings = campaign.get('settings') or {}
        current_settings['competitors'] = competitors
        current_settings['last_competitor_analysis'] = {
            'target': target_stats,
            'competitors': competitor_stats,
            'analyzed_at': datetime.now().isoformat()
        }
        
        client.table('campaigns').update({'settings': current_settings}).eq('id', campaign_id).execute()
        
        return jsonify({
            'success': True,
            'target': target_stats,
            'competitors': competitor_stats
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=True)
