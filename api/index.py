#!/usr/bin/env python3
"""
SEO Agency Platform - Main API
Flask application with role-based authentication and multi-tenant support.
"""
import os
import sys

# Add local libs to path (for openpyxl)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'libs')))
# Add project root to path so "api" module is found
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import logging
from datetime import datetime
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps
from api.dataforseo_client import (
    start_onpage_audit,
    get_audit_status,
    get_audit_summary,
    get_page_issues,
    get_domain_rank_overview
)
from api.utils import create_tasks_from_audit, categorize_audit_issues
from api.export import generate_audit_excel
from execution.screenshot_capture import capture_screenshot_with_fallback
from api.deep_audit_slides import create_deep_audit_slides, create_authority_shift_slides
from api.google_auth import get_google_credentials
import time
import requests
import threading
import json
import re
import gemini_client
from webflow_client import webflow_client
from nano_banana_client import nano_banana_client

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# import check
from pathlib import Path

# Initialize Flask
# Use pathlib for better handling of spaces in paths
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables
env_local_path = BASE_DIR / '.env.local'
env_path = BASE_DIR / '.env'

# 1. Try .env.local first
try:
    if env_local_path.exists():
        load_dotenv(env_local_path)
        logger.info("Loaded .env.local")
except Exception as e:
    logger.warning(f"Failed to load .env.local: {e}")

# 2. Try .env (might fail permissions)
try:
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded .env")
except Exception as e:
    # This catches PermissionError from .exists() or load_dotenv()
    logger.warning(f"Could not load .env (likely permissions): {e}")
        
    # Double check if load_dotenv actually worked
    if not os.getenv('SUPABASE_URL'):
        # Fallback: Manual parse
        logger.info("Attempting manual parse of .env...")
        try:
             with open(env_path, 'r') as f:
                 for line in f:
                     line = line.strip()
                     if not line or line.startswith('#'): continue
                     if '=' in line:
                         key, val = line.split('=', 1)
                         if not os.getenv(key): # Don't overwrite
                             os.environ[key] = val.strip().strip("'").strip('"')
             logger.info("Manual parse completed.")
        except Exception as e:
             logger.error(f"Manual parse failed: {e}")

except Exception as e:
    logger.error(f"Error loading environment: {e}")

# Verify critical vars
if not os.getenv('SUPABASE_URL'):
    logger.warning("CRITICAL: SUPABASE_URL not found in environment!")
else:
    logger.info("Supabase URL configured.")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / 'public'),
    static_folder=str(BASE_DIR / 'public'),
    static_url_path=''
)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')

# Session cookie config for Railway proxy (needed for OAuth PKCE flow)
is_production = bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PUBLIC_DOMAIN'))
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = is_production
app.config['SESSION_COOKIE_HTTPONLY'] = True

CORS(app)

# Register Google OAuth Integration Blueprint
try:
    from api.google_integration import google_integration_bp
    app.register_blueprint(google_integration_bp)
except Exception as e:
    logger.error(f"Failed to register google_integration blueprint: {e}")

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

@app.route('/audit-dashboard.html')
@login_required
def audit_dashboard():
    """Serve the advanced deep audit dashboard."""
    return render_template('audit-dashboard.html')

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

        return jsonify({'error': str(e)}), 401


@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password."""
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password required'}), 400

    try:
        # 1. Verify current password
        user_email = session['user']['email']
        
        # We try to sign in. If it fails, current password is wrong.
        # Note: This might create a new session on Supabase side, but that's fine.
        auth_res = supabase.auth.sign_in_with_password({
            'email': user_email,
            'password': current_password
        })
        
        if not auth_res.user:
            return jsonify({'error': 'Incorrect current password'}), 401

        # 2. Update password
        # users.update() updates the user.
        update_res = supabase.auth.update_user({
            'password': new_password
        })
        
        if update_res:
             return jsonify({'success': True, 'message': 'Password updated successfully'})
        else:
             return jsonify({'error': 'Failed to update password'}), 500

    except Exception as e:
        logger.error(f"Password change error: {e}")
        # HACK: Supabase/GoTrue specific error messages often come in e.message or str(e)
        msg = str(e)
        if "Invalid login credentials" in msg:
             return jsonify({'error': 'Incorrect current password'}), 401
        return jsonify({'error': f"Failed to change password: {msg}"}), 500


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
        
        # Filter by organization for EVERYONE (Admin means Org Admin, not Superuser)
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
        query = client.table('tasks').select('*, campaigns!inner(name, domain, organization_id)')
        
        # KEY FIX: Filter by Organization (via joined campaign)
        if user.get('organization_id'):
             query = query.eq('campaigns.organization_id', user['organization_id'])
        else:
             return jsonify({'tasks': []})

        # Filter based on role (Permissions within Org)
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
    
    client = supabase_admin or supabase
    
    try:
        # First check if user can update this task
        task = client.table('tasks').select('*').eq('id', task_id).single().execute()
        
        if not task.data:
            return jsonify({'error': 'Task not found'}), 404
        
        # Check permission
        user_role = user.get('role', '').lower()
        if user_role not in ['admin', 'administrator', 'campaign_manager']:
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
        
        response = client.table('tasks').update(update_data).eq('id', task_id).execute()
        
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
    audit_type = request.args.get('type') # Expected: 'technical' or 'competitor'
    
    try:
        # Use admin client to bypass RLS or ensure context
        client = supabase_admin or supabase
        user = session['user']
        
        # Join campaigns to filter by Org
        query = client.table('audits').select('*, campaigns!inner(name, domain, organization_id)')
        
        # KEY FIX: Filter by Organization
        if user.get('organization_id'):
             query = query.eq('campaigns.organization_id', user['organization_id'])
        else:
             return jsonify({'audits': []})

        if campaign_id:
            query = query.eq('campaign_id', campaign_id)
            
        if audit_type:
            query = query.eq('type', audit_type)
        
        response = query.order('created_at', desc=True).execute()
        return jsonify({'audits': response.data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/audits/<audit_id>/generate-slides', methods=['POST'])
@login_required
@permission_required('view_all_audits')
def generate_audit_slides(audit_id):
    """Generate Google Slides for an audit."""
    user = session['user']
    
    try:
        # Get audit data
        audit = supabase_admin.table('audits').select('*').eq('id', audit_id).execute()
        if not audit.data:
            return jsonify({'error': 'Audit not found'}), 404
            
        audit_data = audit.data[0]
        
        # Check permissions (basic organization check)
        if user['role'] != 'admin' and audit_data.get('organization_id') != user.get('organization_id'):
             return jsonify({'error': 'Unauthorized'}), 403

        # Check if already has slides? (Optional: allow regeneration)
        
        # Import generator here to avoid circular imports or early failures if dependencies missing
        try:
            from api.deep_audit_slides import create_deep_audit_slides
        except ImportError as e:
            return jsonify({'error': f'Slides generator module error: {str(e)}'}), 500

        # Run generation
        # Note: This can take time. Ideally should be a background task (Celery/RQ).
        # For now, running synchronously but it might timeout on Vercel/Railway if > 30s.
        # We'll assume it's fast enough or user accepts wait.
        
        full_data = audit_data.get('data', {})
        # If competitor audit, use that domain for the slides title
        results_obj = audit_data.get('results') or {}
        domain = results_obj.get('competitor_domain') or audit_data.get('settings', {}).get('domain') or 'Website'
        
        try:
            result = create_deep_audit_slides(full_data, domain)
            slides_url = result.get('presentation_url')
            
            # Update audit record
            supabase.table('audits').update({'slides_url': slides_url}).eq('id', audit_id).execute()
            
            return jsonify({'slides_url': slides_url})
            
        except FileNotFoundError as e:
            # Likely missing credentials or asset
            return jsonify({'error': f'File not found error: {str(e)}'}), 503
        except Exception as e:
            return jsonify({'error': f'Failed to generate slides: {str(e)}'}), 500

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
            
        target_domain = data.get('competitor_domain') or campaign.data['domain']
        audit_type = data.get('type', 'technical')
        
        try:
            max_pages = int(data.get('max_pages', 200))
        except (ValueError, TypeError):
            max_pages = 200
        
        # Start DataForSEO audit
        dfs_result = start_onpage_audit(target_domain, max_pages=max_pages)
        
        if not dfs_result.get('success'):
            return jsonify({'error': f"Failed to start audit: {dfs_result.get('error')}"}), 500
            
        task_id = dfs_result.get('task_id')

        # Create audit record
        # If it's a competitor, persist the competitor_domain in the results dict so UI can show it
        initial_results = {}
        if data.get('competitor_domain'):
            initial_results['competitor_domain'] = data.get('competitor_domain')

        response = client.table('audits').insert({
            'campaign_id': data.get('campaign_id'),
            'type': audit_type,
            'status': 'crawling',
            'dataforseo_task_id': task_id,
            'results': initial_results
        }).execute()
        
        audit = response.data[0]
        audit_id = audit['id']
        
        # ---- DUAL WRITE: Also create a projects record for audit-dashboard.html ----
        try:
            # Fetch keywords + backlinks in parallel with crawl (same as audit-app)
            from api.dataforseo_client import fetch_ranked_keywords, fetch_backlinks_summary, get_referring_domains, location_code_for
            
            # Resolve campaign country to DataForSEO location_code
            saved_location = (campaign.data.get('settings') or {}).get('location', 'US')
            audit_location_code = location_code_for(saved_location)
            
            keywords_data = fetch_ranked_keywords(target_domain, location_code=audit_location_code)
            keywords = keywords_data.get('keywords', []) if isinstance(keywords_data, dict) else []
            keywords_total_count = keywords_data.get('total_count', len(keywords))
            keywords_estimated_traffic = keywords_data.get('estimated_traffic', 0)
            keywords_at_limit = keywords_data.get('keywords_at_limit', len(keywords) >= 1000)
            
            backlinks_summary = fetch_backlinks_summary(target_domain)
            referring_domains = get_referring_domains(target_domain)
            
            import time as time_mod
            full_audit_data = {
                'task_id': task_id,
                'domain': target_domain,
                'status': 'pending',
                'created_at': time_mod.strftime("%Y-%m-%dT%H:%M:%SZ"),
                'organic_keywords': keywords,
                'total_keywords': keywords_total_count,
                'total_traffic': keywords_estimated_traffic,
                'keywords_at_limit': keywords_at_limit,
                'backlinks_summary': backlinks_summary,
                'referring_domains': referring_domains,
                'max_pages': max_pages
            }
            
            project_response = client.table('projects').insert({
                'domain': target_domain,
                'full_audit_data': full_audit_data,
                'source': 'agency-platform',
                'audit_id': audit_id
            }).execute()
            
            project_id = project_response.data[0]['id'] if project_response.data else None
            logger.info(f"Dual-write: created project {project_id} for audit {audit_id}")
        except Exception as dual_err:
            logger.error(f"Dual-write to projects failed (non-fatal): {dual_err}")
        # ---- END DUAL WRITE ----
        
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
                    
                    # 3. Categorize Results for UI (First, so we can use for tasks)
                    categorized = categorize_audit_issues(pages, summary.get('summary'))
                    
                    # 4. Create Tasks
                    # Use admin client for writes if available
                    client = supabase_admin or supabase
                    create_tasks_from_audit(categorized, audit['campaign_id'], client)
                    
                    # 5. Update Audit Record
                    existing_results = audit.get('results') or {}
                    new_results = existing_results.copy()
                    new_results.update({
                        'summary': summary.get('summary', {}),
                        'categorized': categorized,
                        'pages': pages
                    })
                    
                    update_data = {
                        'status': 'completed',
                        'results': new_results
                    }
                    
                    update_res = client.table('audits').update(update_data).eq('id', audit_id).execute()
                    audit = update_res.data[0] # Return updated audit
                    
                except Exception as e:
                     print(f"Error finalizing audit: {e}")
                     # Optional: fail status
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # ---------------------------------------------------------
    # LEGACY DATA SUPPORT: On-the-fly migration
    # ---------------------------------------------------------
    if audit.get('status') == 'completed' and audit.get('results'):
        results = audit['results']
        categorized = results.get('categorized')
        
        # Check if migration is needed:
        # 1. No categorized data at all (very old)
        # 2. Old categorization (Architecture contains items that should be in Usability)
        needs_migration = False
        
        if not categorized and 'pages' in results:
            needs_migration = True
        elif categorized and 'architecture' in categorized:
            # Check for a key that Moved, e.g., 'server_errors_5xx'
            if 'server_errors_5xx' in categorized['architecture']:
                needs_migration = True
        
        if needs_migration and 'pages' in results:
            try:
                # print(f"Migrating legacy audit {audit['id']} on the fly...")
                new_categorized = categorize_audit_issues(results['pages'], results.get('summary'))
                audit['results']['categorized'] = new_categorized
                
                # Persist the migration
                (supabase_admin or supabase).table('audits').update({
                    'results': audit['results']
                }).eq('id', audit['id']).execute()
                # print("Migration persisted.")
            except Exception as e:
                print(f"Failed to migrate legacy audit: {e}")

    # Build response with success flag and flattened fields for audit-dashboard.html
    results = audit.get('results', {}) or {}
    campaign_data = audit.get('campaigns', {}) or {}
    domain = results.get('competitor_domain') or campaign_data.get('domain', '')
    
    flat_audit = {
        **audit,
        'domain': domain,
        'keywords': results.get('keywords', []),
        'pages': results.get('pages', []),
        'pagespeed': results.get('pagespeed', {}),
        'backlinks': results.get('backlinks', {}),
        'backlinks_summary': results.get('backlinks_summary', results.get('backlinks', {})),
        'referring_domains': results.get('referring_domains', []),
        'total_keywords': results.get('total_keywords', 0),
        'total_traffic': results.get('total_traffic', 0),
        'keywords_at_limit': results.get('keywords_at_limit', 0)
    }
    
    return jsonify({'success': True, 'audit': flat_audit})

@app.route('/api/audits/<audit_id>/export', methods=['GET'])
@login_required
def export_audit(audit_id):
    """Export audit result as Excel."""
    try:
        client = supabase_admin or supabase
        response = client.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
        audit = response.data
        
        # Ensure we have results to export
        if not audit.get('results'):
            return jsonify({'error': 'Audit has no results to export'}), 400
            
        # Migrate if needed (reuse logic or just trust current state)
        # Ideally, we should unify the read logic, but for export we just take what's there
        # If categorized data is missing, we might want to run categorization on the fly here too
        results = audit['results']
        if 'categorized' not in results and 'pages' in results:
             results['categorized'] = categorize_audit_issues(results['pages'], results.get('summary'))
        
        # Generate Excel
        output = generate_audit_excel(audit)
        
        filename = f"audit_report_{audit.get('campaigns', {}).get('domain', 'site')}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Export failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/audits/list', methods=['GET'])
@login_required
def list_audits_for_dashboard():
    """List audits for the audit-dashboard selector dropdown."""
    try:
        client = supabase_admin or supabase
        response = client.table('audits').select('id, created_at, status, results, campaigns(domain)').order('created_at', desc=True).limit(50).execute()
        audits = []
        for a in response.data:
            campaign_data = a.get('campaigns', {}) or {}
            results = a.get('results', {}) or {}
            domain = results.get('competitor_domain') or campaign_data.get('domain', 'Unknown')
            audits.append({
                'id': a['id'],
                'domain': domain,
                'created_at': a['created_at'],
                'status': a['status']
            })
        return jsonify({'audits': audits})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/deep-audit/status/<task_id>', methods=['GET'])
@login_required
def deep_audit_status(task_id):
    """Check DataForSEO crawl status for the deep audit dashboard."""
    try:
        status = get_audit_status(task_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audits/<audit_id>/slides-url', methods=['POST'])
@login_required
def save_slides_url(audit_id):
    """Save the generated slides URL to the audit record."""
    try:
        data = request.get_json()
        slides_url = data.get('slides_url')
        if not slides_url:
            return jsonify({'error': 'slides_url required'}), 400
        
        client = supabase_admin or supabase
        client.table('audits').update({
            'slides_url': slides_url
        }).eq('id', audit_id).execute()
        
        return jsonify({'success': True})
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

@app.route('/api/client/stats', methods=['GET'])
@login_required
@permission_required('view_all_campaigns')
def get_client_stats():
    """Return traffic, keyword count, and top 10 ranked keywords.
    
    Params:
      campaign_id: required — reads the most recent technical audit for this campaign
      audit_id: optional — if provided, reads from the projects record for this specific audit
                (used for competitor rows which each have their own audit)
    """
    campaign_id = request.args.get('campaign_id')
    audit_id = request.args.get('audit_id')

    db = supabase_admin or supabase
    keywords_raw, total_kw, total_traffic = [], 0, 0

    try:
        if audit_id:
            # Competitor row: find the project linked to this specific audit
            proj_res = db.table('projects').select('full_audit_data') \
                .eq('audit_id', audit_id).limit(1).execute()
        else:
            # Client row: find the project linked to the latest technical audit for this campaign
            if not campaign_id:
                return jsonify({'error': 'campaign_id or audit_id required'}), 400
            # Get the latest technical audit id
            aud_res = db.table('audits').select('id') \
                .eq('campaign_id', campaign_id).eq('type', 'technical') \
                .order('created_at', desc=True).limit(1).execute()
            latest_audit_id = (aud_res.data or [{}])[0].get('id')
            if not latest_audit_id:
                return jsonify({'success': True, 'total_keywords': 0, 'total_traffic': 0, 'top10_keywords': []})
            proj_res = db.table('projects').select('full_audit_data') \
                .eq('audit_id', latest_audit_id).limit(1).execute()

        proj = (proj_res.data or [{}])[0].get('full_audit_data') or {}
        # Keywords are stored as organic_keywords in projects.full_audit_data
        keywords_raw = proj.get('organic_keywords') or proj.get('keywords', [])
        total_kw = proj.get('total_keywords', len(keywords_raw))
        total_traffic = proj.get('total_traffic', 0)
    except Exception as e:
        logger.error(f"get_client_stats error: {e}")

    # Sort by rank_absolute and pick top 10
    def rank_of(item):
        r = (item.get('ranked_serp_element') or {}).get('serp_item', {}).get('rank_absolute')
        return r if r else 9999

    sorted_kws = sorted(keywords_raw, key=rank_of)[:10]
    top10 = []
    for item in sorted_kws:
        kw = (item.get('keyword_data') or {}).get('keyword', '')
        rank = rank_of(item)
        vol = (item.get('keyword_data') or {}).get('keyword_info', {}).get('search_volume', 0) or 0
        if kw:
            top10.append({'keyword': kw, 'rank': rank, 'volume': vol})

    return jsonify({
        'success': True,
        'total_keywords': total_kw,
        'total_traffic': total_traffic,
        'top10_keywords': top10
    })


@app.route('/api/client/backlinks', methods=['GET'])
@login_required
@permission_required('view_all_campaigns')
def get_client_backlinks():
    """Fetch live backlink profile (summary + referring domains) for the client campaign."""
    campaign_id = request.args.get('campaign_id')
    if not campaign_id:
        return jsonify({'error': 'campaign_id is required'}), 400

    client = supabase_admin or supabase
    try:
        campaign_res = client.table('campaigns').select('domain').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404

        domain = campaign['domain']

        from api.dataforseo_client import fetch_backlinks_summary, get_referring_domains
        summary = fetch_backlinks_summary(domain)
        referring_domains = get_referring_domains(domain, limit=500)

        return jsonify({
            'success': True,
            'domain': domain,
            'summary': summary,
            'referring_domains': referring_domains
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/refresh-backlinks', methods=['POST'])
def refresh_backlinks():
    """Fetch live backlinks from DataForSEO for a project/audit and persist to projects table.
    
    Called by the 'Refresh Backlinks' button in audit-dashboard.html.
    Accepts: { project_id: <audit_id or project_id> }
    Returns: { success, referring_domains: [], backlinks_summary: {} }
    """
    data = request.get_json() or {}
    entity_id = data.get('project_id')
    if not entity_id:
        return jsonify({'error': 'project_id required'}), 400

    db = supabase_admin or supabase
    try:
        from api.dataforseo_client import fetch_backlinks_summary, get_referring_domains

        # Find the project record — could be an audit_id or a project uuid
        proj_res = db.table('projects').select('id, full_audit_data').eq('audit_id', entity_id).limit(1).execute()
        if not proj_res.data:
            proj_res = db.table('projects').select('id, full_audit_data').eq('id', entity_id).limit(1).execute()

        if not proj_res.data:
            return jsonify({'error': 'Project not found for given id'}), 404

        proj = proj_res.data[0]
        proj_id = proj['id']
        fad = proj.get('full_audit_data') or {}
        domain = fad.get('domain', '')

        if not domain:
            # Try to get domain from audits table via audit_id
            aud_res = db.table('audits').select('results, campaigns(domain)').eq('id', entity_id).limit(1).execute()
            if aud_res.data:
                aud = aud_res.data[0]
                domain = (aud.get('campaigns') or {}).get('domain') or (aud.get('results') or {}).get('competitor_domain', '')

        if not domain:
            return jsonify({'error': 'Could not determine domain for this project'}), 400

        logger.info(f"Refreshing backlinks for domain: {domain}")

        summary = fetch_backlinks_summary(domain)
        referring_domains = get_referring_domains(domain, limit=500)

        # Persist back to projects table
        fad['backlinks_summary'] = summary
        fad['referring_domains'] = referring_domains
        db.table('projects').update({'full_audit_data': fad}).eq('id', proj_id).execute()

        return jsonify({
            'success': True,
            'domain': domain,
            'backlinks_summary': summary,
            'referring_domains': referring_domains
        })

    except Exception as e:
        logger.error(f"refresh_backlinks error: {e}")
        return jsonify({'error': str(e)}), 500



@login_required
@permission_required('view_all_campaigns')
def analyze_competitor_gap():
    """Perform a Keyword Gap Analysis between the client domain and a competitor."""
    campaign_id = request.args.get('campaign_id')
    competitor_domain = request.args.get('competitor_domain')
    min_volume = request.args.get('min_volume', type=int)
    max_rank = request.args.get('max_rank', type=int)
    # Country params — ISO code e.g. "IN", "US", "GB"
    client_country = request.args.get('location', 'US')
    competitor_country = request.args.get('competitor_location', client_country)

    if not campaign_id or not competitor_domain:
        return jsonify({'error': 'Campaign ID and Competitor Domain are required'}), 400

    db_client = supabase_admin or supabase

    try:
        # Get target campaign
        campaign_res = db_client.table('campaigns').select('*').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404

        target_domain = campaign['domain']

        # Resolve location codes — prefer campaign's saved setting over the query param
        from api.dataforseo_client import get_keyword_gap, location_code_for
        saved_location = (campaign.get('settings') or {}).get('location', client_country)
        target_loc = location_code_for(saved_location)
        comp_loc = location_code_for(competitor_country)

        # Build in-memory filter list
        filters = []
        if min_volume is not None:
            filters.append(["keyword_info.search_volume", ">=", min_volume])
        if max_rank is not None:
            if filters: filters.append("and")
            filters.append(["ranked_serp_element.serp_item.rank_absolute", "<=", max_rank])

        if not filters:
            filters = None

        gap_results = get_keyword_gap(
            target_domain, competitor_domain,
            filters=filters,
            location_code=target_loc,
            competitor_location_code=comp_loc
        )

        return jsonify(gap_results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/competitors/backlinks-gap', methods=['GET'])
@login_required
@permission_required('view_all_campaigns')
def analyze_backlinks_gap():
    """Perform a Backlink Gap Analysis between the client domain and a competitor."""
    campaign_id = request.args.get('campaign_id')
    competitor_domain = request.args.get('competitor_domain')
    min_backlinks = request.args.get('min_backlinks', type=int)
    
    if not campaign_id or not competitor_domain:
        return jsonify({'error': 'Campaign ID and Competitor Domain are required'}), 400
        
    client = supabase_admin or supabase
    
    try:
        # Get target campaign
        campaign_res = client.table('campaigns').select('*').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
            
        target_domain = campaign['domain']
        
        # Build DataForSEO filters array if provided
        filters = []
        if min_backlinks is not None:
            filters.append(["backlinks", ">=", min_backlinks])
            
        if not filters:
            filters = None
        
        from api.dataforseo_client import get_backlinks_gap
        gap_results = get_backlinks_gap(target_domain, competitor_domain, filters=filters)
        
        return jsonify(gap_results)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/competitors/strategy', methods=['GET'])
@login_required
@permission_required('view_all_campaigns')
def get_competitor_strategy():
    """Generate strategic content recommendations based on the gap analysis."""
    campaign_id = request.args.get('campaign_id')
    competitor_domain = request.args.get('competitor_domain')
    
    if not campaign_id or not competitor_domain:
        return jsonify({'error': 'Campaign ID and Competitor Domain are required'}), 400
        
    client = supabase_admin or supabase
    
    try:
        # Get target campaign
        campaign_res = client.table('campaigns').select('*').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
            
        target_domain = campaign['domain']
        
        # We need get_keyword_gap which computes the set difference locally
        from api.dataforseo_client import get_keyword_gap
        gap_results = get_keyword_gap(target_domain, competitor_domain)
        
        # If there's an error in gap analysis
        if not gap_results.get('success'):
            return jsonify({'error': gap_results.get('error', 'Gap analysis failed')}), 500
            
        gap_keywords = gap_results.get('gap_keywords', [])
        
        # Format the top 5 gap keywords for the strategy recommendation
        top_gaps = []
        for kw in gap_keywords[:5]:
            word = kw.get('keyword_data', {}).get('keyword', 'Unknown')
            vol = kw.get('keyword_data', {}).get('keyword_info', {}).get('search_volume', 0)
            top_gaps.append(f"<li><strong>{word}</strong> (Volume: {vol})</li>")
            
        gaps_html = "\n".join(top_gaps) if top_gaps else "<li>No significant content gaps found!</li>"
        
        # Normally you would pass this through an LLM to generate real recommendations.
        # For now, we will return a formatted HTML strategy based on the gap data.
        strategy_html = f"""
        <h4>1. Attack High-Value Gaps</h4>
        <p>Your competitor <strong>{competitor_domain}</strong> is currently outranking you for several key terms. We recommend prioritizing new pillar content targeting these exact phrases:</p>
        <ul>
            {gaps_html}
        </ul>
        
        <h4>2. The 30% Better Rule (Skyscraper)</h4>
        <p>Review the top-ranking pages for the keywords above. Produce content that is at least 30% longer, more comprehensive, and features custom graphics or unique data that {competitor_domain} lacks.</p>
        
        <h4>3. Internal Link Architecture</h4>
        <p>Once the new content is published, route authority to it by adding 3-5 internal links from your strongest existing pages (like your homepage or main service pages) using exact-match anchor text.</p>
        """
        
        return jsonify({
            'success': True,
            'target_domain': target_domain,
            'competitor_domain': competitor_domain,
            'recommendations_html': strategy_html
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# DEBUG / RESCUE ROUTES
# =============================================================================

@app.route('/api/debug/claim-orphans', methods=['POST'])
@login_required
def claim_orphans():
    """Manual trigger to claim NULL-org campaigns for current user."""
    user = session['user']
    org_id = user.get('organization_id')
    
    if not org_id:
        return jsonify({'error': 'User has no organization to claim into'}), 400
        
    client = supabase_admin or supabase
    try:
        # Find orphaned campaigns
        orphans = client.table('campaigns').select('id, name').is_('organization_id', 'null').execute()
        count = len(orphans.data)
        
        # Update them
        if count > 0:
            client.table('campaigns').update({'organization_id': org_id}).is_('organization_id', 'null').execute()
            
        return jsonify({
            'success': True,
            'user_email': user['email'],
            'target_org_id': org_id,
            'orphans_found': count,
            'orphans_claimed': orphans.data
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# =============================================================================
# DEEP AUDIT & PRESENTATION ROUTES / audit-app integration
# =============================================================================

@app.route('/api/project-data/<audit_id>', methods=['GET'])
@login_required
def get_project_data(audit_id):
    """Return full_audit_data from projects table for audit-dashboard.html.
    If no project record exists (old audit), backfill from audits table on-the-fly."""
    try:
        client = supabase_admin or supabase
        result = client.table('projects').select('*').eq('audit_id', audit_id).execute()
        
        if not result.data:
            # ---- BACKFILL: Create project record from existing audit data ----
            logger.info(f"No project found for audit {audit_id}, backfilling from audits table...")
            
            audit_res = client.table('audits').select('*, campaigns(domain)').eq('id', audit_id).single().execute()
            if not audit_res.data:
                return jsonify({'error': 'Audit not found'}), 404
            
            audit = audit_res.data
            audit_results = audit.get('results', {}) or {}
            campaign_data = audit.get('campaigns', {}) or {}
            domain = audit_results.get('competitor_domain') or campaign_data.get('domain', '')
            if domain:
                domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
            
            # Build full_audit_data matching the audit-app format
            import time as time_mod
            full_audit_data = {
                'task_id': audit.get('dataforseo_task_id', ''),
                'domain': domain,
                'status': 'completed' if audit.get('status') == 'completed' else 'pending',
                'created_at': audit.get('created_at', time_mod.strftime("%Y-%m-%dT%H:%M:%SZ")),
                'organic_keywords': audit_results.get('keywords', []),
                'total_keywords': audit_results.get('total_keywords', 0),
                'total_traffic': audit_results.get('total_traffic', 0),
                'keywords_at_limit': audit_results.get('keywords_at_limit', False),
                'backlinks_summary': audit_results.get('backlinks_summary', audit_results.get('backlinks', {})),
                'referring_domains': audit_results.get('referring_domains', []),
                'pages': audit_results.get('pages', []),
                'pagespeed': audit_results.get('pagespeed', {}),
                'max_pages': 200
            }
            
            # If audit data is sparse, try fetching keywords + backlinks from DataForSEO now
            if not full_audit_data['organic_keywords'] and domain:
                try:
                    from api.dataforseo_client import fetch_ranked_keywords, fetch_backlinks_summary, get_referring_domains
                    kw_data = fetch_ranked_keywords(domain)
                    keywords = kw_data.get('keywords', []) if isinstance(kw_data, dict) else []
                    full_audit_data['organic_keywords'] = keywords
                    full_audit_data['total_keywords'] = kw_data.get('total_count', len(keywords))
                    full_audit_data['total_traffic'] = kw_data.get('estimated_traffic', 0)
                    full_audit_data['keywords_at_limit'] = kw_data.get('keywords_at_limit', False)
                    
                    full_audit_data['backlinks_summary'] = fetch_backlinks_summary(domain)
                    full_audit_data['referring_domains'] = get_referring_domains(domain)
                    logger.info(f"Backfill: fetched {len(keywords)} keywords for {domain}")
                except Exception as fetch_err:
                    logger.warning(f"Backfill: could not fetch DataForSEO data: {fetch_err}")
            
            # Save to projects table for future use
            try:
                new_project = client.table('projects').insert({
                    'domain': domain,
                    'full_audit_data': full_audit_data,
                    'source': 'backfill',
                    'audit_id': audit_id
                }).execute()
                project = new_project.data[0]
                logger.info(f"Backfill: created project {project['id']} for audit {audit_id}")
            except Exception as insert_err:
                logger.error(f"Backfill insert failed: {insert_err}")
                # Return data even if insert fails
                return jsonify({
                    'success': True,
                    'project_id': None,
                    'audit_id': audit_id,
                    'domain': domain,
                    'data': full_audit_data
                })
            
            return jsonify({
                'success': True,
                'project_id': project['id'],
                'audit_id': audit_id,
                'domain': domain,
                'data': full_audit_data
            })
            # ---- END BACKFILL ----
        
        project = result.data[0]
        audit_data = project.get('full_audit_data', {}) or {}
        
        # ---- MERGE: Fill missing fields from audits.results ----
        pages = audit_data.get('pages', [])
        pagespeed = audit_data.get('pagespeed')
        needs_update = False
        
        if (not pages or (isinstance(pages, list) and len(pages) == 0)) or not pagespeed:
            try:
                audit_res = client.table('audits').select('results').eq('id', audit_id).execute()
                if audit_res.data:
                    audit_results = audit_res.data[0].get('results', {}) or {}
                    
                    # Merge pages if missing
                    if not pages or (isinstance(pages, list) and len(pages) == 0):
                        src_pages = audit_results.get('pages', [])
                        if isinstance(src_pages, dict):
                            src_pages = src_pages.get('pages', [])
                        if src_pages:
                            audit_data['pages'] = src_pages
                            needs_update = True
                            logger.info(f"Merged {len(src_pages)} pages from audits.results")
                    
                    # Merge pagespeed if missing
                    if not pagespeed:
                        src_ps = audit_results.get('pagespeed')
                        if src_ps:
                            audit_data['pagespeed'] = src_ps
                            needs_update = True
                            logger.info(f"Merged pagespeed from audits.results")
            except Exception as merge_err:
                logger.warning(f"Merge from audits failed: {merge_err}")
        
        # Fetch pagespeed on-the-fly if still missing
        domain = project.get('domain') or audit_data.get('domain', '')
        if not audit_data.get('pagespeed') and domain:
            try:
                from execution.pagespeed_insights import fetch_pagespeed_scores
                ps_data = {}
                mobile = fetch_pagespeed_scores(f"https://{domain}", strategy="mobile")
                if mobile and mobile.get('success'):
                    ps_data['mobile'] = {'scores': mobile.get('scores', {}), 'metrics': mobile.get('metrics', {})}
                    ps_data['scores'] = mobile.get('scores', {})
                    ps_data['metrics'] = mobile.get('metrics', {})
                desktop = fetch_pagespeed_scores(f"https://{domain}", strategy="desktop")
                if desktop and desktop.get('success'):
                    ps_data['desktop'] = {'scores': desktop.get('scores', {}), 'metrics': desktop.get('metrics', {})}
                if ps_data:
                    audit_data['pagespeed'] = ps_data
                    needs_update = True
                    logger.info(f"Fetched pagespeed on-the-fly for {domain}")
            except Exception as ps_err:
                logger.warning(f"On-the-fly pagespeed failed: {ps_err}")
        
        # Persist merged data back to projects table for next time
        if needs_update:
            try:
                client.table('projects').update({
                    'full_audit_data': audit_data
                }).eq('id', project['id']).execute()
                logger.info(f"Persisted merged data back to project {project['id']}")
            except Exception as upd_err:
                logger.warning(f"Could not persist merged data: {upd_err}")
        # ---- END MERGE ----
        
        return jsonify({
            'success': True,
            'project_id': project['id'],
            'audit_id': audit_id,
            'domain': project.get('domain', ''),
            'data': audit_data
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save-audit-results', methods=['POST'])
@login_required
def save_audit_results():
    """Fetch and save on-page audit results when crawl completes"""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        data = request.get_json()
        audit_id = data.get('audit_id')
        task_id = data.get('task_id')
        
        if not audit_id or not task_id:
            return jsonify({"error": "audit_id and task_id required"}), 400
            
        client = supabase_admin or supabase
        
        # Fetch the on-page audit results from DataForSEO
        from api.dataforseo_client import get_page_issues, get_audit_summary
        
        summary_result = get_audit_summary(task_id)
        summary = summary_result.get('summary', {}) if summary_result.get('success') else {}
        
        pages_data = get_page_issues(task_id, limit=200)  # Get up to 200 pages
        pages = pages_data.get('pages', []) if pages_data.get('success') else []
        
        # Get existing audit/project data
        result = client.table('audits').select('*').eq('id', audit_id).execute()
        if not result.data:
            return jsonify({"error": "Audit not found"}), 404
        
        audit_record = result.data[0]
        audit_results = audit_record.get('results', {}) or {}
        
        # Get domain from audit data
        domain = audit_results.get('competitor_domain') or audit_record.get('campaign_id') # Will need to fetch campaign domain if missing
        
        if not domain or str(domain).startswith(('http', 'ww', '1', '2', '3', 'u', 'd', 'e')): # Crude fast check
           try:
              c_res = client.table('campaigns').select('domain').eq('id', audit_record.get('campaign_id')).execute()
              if c_res.data:
                 domain = c_res.data[0]['domain']
           except:
              pass
              
        if domain:
             domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
        
        # Fetch PageSpeed data using Google's PageSpeed Insights API - BOTH mobile and desktop
        pagespeed = {}
        if domain:
            try:
                from execution.pagespeed_insights import fetch_pagespeed_scores
                # Fetch MOBILE
                mobile_result = fetch_pagespeed_scores(f"https://{domain}", strategy="mobile")
                if mobile_result:
                    pagespeed['mobile'] = {
                        'scores': mobile_result.get('scores', {}),
                        'metrics': mobile_result.get('metrics', {})
                    }
                # Fetch DESKTOP
                desktop_result = fetch_pagespeed_scores(f"https://{domain}", strategy="desktop")
                if desktop_result:
                    pagespeed['desktop'] = {
                        'scores': desktop_result.get('scores', {}),
                        'metrics': desktop_result.get('metrics', {})
                    }
                # Also store combined scores for backward compatibility
                if mobile_result:
                    pagespeed['scores'] = mobile_result.get('scores', {})
                    pagespeed['metrics'] = mobile_result.get('metrics', {})
            except Exception as e:
                logger.error(f"PageSpeed error: {e}")
        
        # Update with pages, pagespeed, and mark as completed
        audit_results['summary'] = summary
        categorized = categorize_audit_issues(pages, summary)
        audit_results['categorized'] = categorized
        audit_results['pages'] = pages
        audit_results['pagespeed'] = pagespeed
        
        # Create tasks based on the new audit results
        if audit_record.get('campaign_id'):
            # Delete old pending automated tasks for this campaign to prevent duplicates
            try:
                client.table('tasks').delete().eq('campaign_id', audit_record.get('campaign_id')).eq('status', 'pending').execute()
            except Exception as e:
                logger.error(f"Failed to delete old tasks: {e}")
                
            create_tasks_from_audit(categorized, audit_record.get('campaign_id'), client)
        
        # Save back to Supabase
        client.table('audits').update({
            'results': audit_results,
            'status': 'completed'
        }).eq('id', audit_id).execute()
        
        # ---- DUAL WRITE: Also update the projects record for audit-dashboard.html ----
        try:
            project_res = client.table('projects').select('id, full_audit_data').eq('audit_id', audit_id).execute()
            if project_res.data:
                project = project_res.data[0]
                project_data = project.get('full_audit_data', {}) or {}
                project_data['pages'] = pages
                project_data['pagespeed'] = pagespeed
                project_data['status'] = 'completed'
                
                client.table('projects').update({
                    'full_audit_data': project_data
                }).eq('id', project['id']).execute()
                logger.info(f"Dual-write: updated project for audit {audit_id} with pages + pagespeed")
        except Exception as dual_err:
            logger.error(f"Dual-write update to projects failed (non-fatal): {dual_err}")
        # ---- END DUAL WRITE ----
        
        # ---- AUTO-SYNC TO CONTENT SYSTEM ----
        try:
            campaign_id = audit_record.get('campaign_id')
            if campaign_id and pages:
                existing_res = client.table('pages').select('url').eq('project_id', campaign_id).execute()
                existing_urls = {p['url'] for p in existing_res.data} if existing_res.data else set()
                
                new_inserts = []
                for p in pages:
                    url = p.get('url')
                    if url and isinstance(url, str) and url not in existing_urls:
                        tech_data = {
                            "title": p.get('title', ''),
                            "meta_description": p.get('description', ''),
                            "word_count": p.get('word_count', 0),
                            "body_content": ""
                        }
                        
                        new_inserts.append({
                            "project_id": campaign_id,
                            "url": url,
                            "page_type": "product",
                            "tech_audit_data": tech_data,
                            "content_description": "Auto-synced from audit"
                        })
                        existing_urls.add(url)
                
                if new_inserts:
                    for i in range(0, len(new_inserts), 50):
                        client.table('pages').insert(new_inserts[i:i+50]).execute()
                    logger.info(f"Auto-synced {len(new_inserts)} pages to Content engine for campaign {campaign_id}")
        except Exception as sync_err:
            logger.error(f"Auto-sync to Content engine failed (non-fatal): {sync_err}")
        # ---- END AUTO-SYNC ----
        
        return jsonify({"success": True, "message": "Results saved"})
        
    except Exception as e:
        logger.error(f"Error saving audit results: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/deep-audit/slides', methods=['POST'])
@app.route('/api/deep-audit/generate-slides', methods=['POST'])
@login_required
def generate_deep_audit_slides_endpoint():
    """Generate modern Google Slides presentation from audit data"""
    try:
        data = request.get_json(silent=True) or {}
            
        screenshots = data.get('screenshots', {})
        audit_data = data.get('audit_data')
        audit_id = data.get('audit_id') or data.get('project_id')  # autoGenerateSlides sends as project_id
        issue_counts = data.get('issue_counts', {})
        template_type = data.get('template_type', 'default')  # 'default' or 'authority_shift'
        
        client = supabase_admin or supabase
        
        domain = 'unknown'
        
        # If audit_data not provided but audit_id is, fetch from projects table first, then audits
        if not audit_data and audit_id:
            try:
                # Try projects table first (has full_audit_data in the right format)
                proj_res = client.table('projects').select('*').eq('audit_id', audit_id).execute()
                if proj_res.data:
                    project = proj_res.data[0]
                    audit_data = project.get('full_audit_data', {})
                    domain = project.get('domain', audit_data.get('domain', 'unknown'))
                    logger.info(f"Slides: loaded data from projects table for audit {audit_id}")
                    
                    # Merge missing fields from audits.results (readability, pagespeed, pages)
                    needs_merge = (
                        not audit_data.get('readability_results') or
                        not audit_data.get('pagespeed') or
                        not audit_data.get('pages')
                    )
                    if needs_merge:
                        try:
                            audit_row = client.table('audits').select('results').eq('id', audit_id).execute()
                            if audit_row.data:
                                ar = audit_row.data[0].get('results', {}) or {}
                                if not audit_data.get('readability_results') and ar.get('readability_results'):
                                    audit_data['readability_results'] = ar['readability_results']
                                    logger.info("Slides: merged readability_results from audits")
                                if not audit_data.get('pagespeed') and ar.get('pagespeed'):
                                    audit_data['pagespeed'] = ar['pagespeed']
                                    logger.info("Slides: merged pagespeed from audits")
                                if not audit_data.get('pages') and ar.get('pages'):
                                    audit_data['pages'] = ar['pages']
                                    logger.info("Slides: merged pages from audits")
                        except Exception as merge_err:
                            logger.warning(f"Slides: merge from audits failed: {merge_err}")
                else:
                    # Fallback to audits table
                    result = client.table('audits').select('*, campaigns(domain)').eq('id', audit_id).execute()
                    if result.data:
                        record = result.data[0]
                        results_dict = record.get('results', {}) or {}
                        campaign_data = record.get('campaigns', {}) or {}
                        
                        domain = results_dict.get('competitor_domain') or campaign_data.get('domain', 'unknown')
                        
                        audit_data = {
                            **results_dict,
                            'domain': domain,
                            'pages': results_dict.get('pages', []),
                            'pagespeed': results_dict.get('pagespeed', {}),
                            'organic_keywords': results_dict.get('keywords', []),
                            'backlinks_summary': results_dict.get('backlinks_summary', results_dict.get('backlinks', {})),
                            'referring_domains': results_dict.get('referring_domains', [])
                        }
                        logger.info(f"Slides: loaded data from audits table for audit {audit_id}")
            except Exception as e:
                logger.error(f"Error fetching project for slides: {e}")

        if not audit_data:
            return jsonify({"error": "No audit data provided and could not fetch from audit_id"}), 400

        # Ensure critical nested fields are dictionaries if they are strings
        for field in ['domain_rank', 'summary', 'backlinks_summary', 'organic_keywords', 'pages', 'referring_domains']:
            if isinstance(audit_data.get(field), str):
                import json
                try:
                    audit_data[field] = json.loads(audit_data[field])
                except:
                    pass
        
        if not domain or domain == 'unknown':
           domain = audit_data.get('domain', 'Website')
           
        logger.info(f"Generating slides for {domain}")
        
        # Get Google credentials
        creds = get_google_credentials()
        if not creds:
            return jsonify({"error": "Google credentials not available"}), 500
        
        # Upload screenshots to Supabase Storage if present
        processed_screenshots = {}
        try:
            if not isinstance(screenshots, dict):
                screenshots = {}

            # Fallback for Homepage
            try:
                hp = screenshots.get('homepage')
                is_homepage_missing = not hp or len(str(hp)) < 100
                if is_homepage_missing and domain and domain != 'unknown':
                    homepage_b64 = capture_screenshot_with_fallback(domain)
                    if homepage_b64:
                        screenshots['homepage'] = homepage_b64
            except Exception as e:
                logger.error(f"Homepage fallback error: {e}")

            if screenshots:
                import base64
                import uuid
                
                bucket_name = 'audit-screenshots'
                try:
                    buckets = client.storage.list_buckets()
                    existing_buckets = [b.name for b in buckets]
                    if bucket_name not in existing_buckets:
                        client.storage.create_bucket(bucket_name, options={"public": True})
                except Exception as e:
                    pass

                for key, data_uri in screenshots.items():
                    try:
                        if not data_uri or not isinstance(data_uri, str): continue
                        if data_uri.startswith('http'):
                            processed_screenshots[key] = data_uri
                            continue
                            
                        if ',' in data_uri:
                            _, encoded = data_uri.split(',', 1)
                        else:
                            encoded = data_uri
                            
                        img_data = base64.b64decode(encoded)
                        filename = f"{uuid.uuid4()}.png"
                        
                        client.storage.from_(bucket_name).upload(
                            file=img_data,
                            path=filename,
                            file_options={"content-type": "image/png", "x-upsert": "true"}
                        )
                        
                        public_url = client.storage.from_(bucket_name).get_public_url(filename)
                        processed_screenshots[key] = public_url
                        
                    except Exception as e:
                        continue
                        
        except Exception as e:
            processed_screenshots = {} 

        issue_counts = data.get('issue_counts', None)

        # Generate presentation using the selected template
        if template_type == 'authority_shift':
            from api.deep_audit_slides import create_authority_shift_slides
            result = create_authority_shift_slides(
                data=audit_data,
                domain=domain,
                creds=creds,
                screenshots=processed_screenshots,
                issue_counts=issue_counts
            )
        else:
            result = create_deep_audit_slides(
                data=audit_data,
                domain=domain,
                creds=creds,
                screenshots=processed_screenshots,
                issue_counts=issue_counts
            )
        
        if result and result.get('presentation_id'):
            # Save the slide URL back to the audit table so it appears in the agency UI
            if audit_id:
                try:
                    res = client.table('audits').select('results').eq('id', audit_id).execute()
                    if res.data:
                       ar = res.data[0].get('results', {}) or {}
                       ar['presentation_url'] = result.get('presentation_url')
                       client.table('audits').update({'results': ar}).eq('id', audit_id).execute()
                except: pass
        
            return jsonify({
                "success": True,
                "presentation_id": result.get('presentation_id'),
                "presentation_url": result.get('presentation_url')
            })
        else:
            return jsonify({"error": result.get('error', 'Failed to generate slides')}), 500
        
    except Exception as e:
        logger.error(f"Error generating deep slides: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal Error: {str(e)}"}), 500

@app.route('/api/audit/<audit_id>/readability', methods=['GET'])
@login_required
def analyze_readability(audit_id):
    """Analyze content readability for audit pages"""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        client = supabase_admin or supabase
        result = client.table('audits').select('*').eq('id', audit_id).execute()
        
        if not result.data:
            return jsonify({"success": False, "error": "Audit not found"}), 404
            
        audit_record = result.data[0]
        audit_data = audit_record.get('results', {}) or {}

        if audit_data.get('readability_results') and not request.args.get('refresh'):
            return jsonify({"success": True, "results": audit_data.get('readability_results')})
            
        pages = audit_data.get('pages', [])
        candidates = []
        
        def is_homepage(u):
            from urllib.parse import urlparse
            parsed = urlparse(u)
            path = parsed.path.strip('/')
            return path == '' or path in ['index.html', 'index.php', 'home']
            
        blacklist = ['/collections', '/products', '/cart', '/checkout', '/account', '/search', '/policies/', '/pages/']
        blog_keywords = ['/blog', '/blogs', '/article', '/post', '/news', '/insight', '/guide', '202']
            
        for page in pages:
            url = page.get('url', '')
            traffic = page.get('traffic', 0)
            if is_homepage(url): continue
            if any(item in url.lower() for item in blacklist): continue
            is_blog = any(keyword in url.lower() for keyword in blog_keywords)
            candidates.append({'url': url, 'traffic': traffic, 'is_blog': is_blog})
            
        candidates.sort(key=lambda x: (x['is_blog'], x['traffic']), reverse=True)
        top_candidates = [c['url'] for c in candidates[:5]]
        
        if not top_candidates:
            urls = [p.get('url') for p in pages if p.get('url')]
            
            # Simple heuristic sort: prefer paths with more slashes or hyphens which indicate content
            def sort_score(url):
                if is_homepage(url): return -10
                if any(k in url.lower() for k in blacklist): return -5
                return url.count('-') + url.count('/')
                
            sorted_urls = sorted(urls, key=sort_score, reverse=True)
            top_candidates = sorted_urls[:3]
            
        if not top_candidates:
            return jsonify({"success": False, "error": "No suitable pages found for readability analysis"})
            
        from execution.readability import mass_analyze_urls
        readability_results = mass_analyze_urls(top_candidates)
        
        if readability_results:
            audit_data['readability_results'] = readability_results
            client.table('audits').update({'results': audit_data}).eq('id', audit_id).execute()
            
            return jsonify({"success": True, "results": readability_results})
        else:
            return jsonify({"success": False, "error": "Analysis failed for all candidates"})
            
    except Exception as e:
        logger.error(f"Error in readability API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/pagespeed', methods=['POST'])
@login_required
def check_pagespeed():
    """Check PageSpeed Insights for a given URL"""
    try:
        data = request.json
        url = data.get('url')
        strategy = data.get('strategy', 'mobile')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
            
        # Add http if missing to prevent API errors
        if not url.startswith('http'):
            url = f'https://{url}'
            
        from execution.pagespeed_insights import fetch_pagespeed_scores
        results = fetch_pagespeed_scores(url, strategy)
        
        if results:
            return jsonify(results)
        else:
            return jsonify({'error': 'Failed to fetch PageSpeed data'}), 500
            
    except Exception as e:
        logger.error(f"Error checking pagespeed: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# PAGESPEED REFRESH
# =============================================================================

@app.route('/api/audit/<audit_id>/refresh-speed', methods=['POST'])
@login_required
def refresh_pagespeed(audit_id):
    """Fetch fresh PageSpeed data for an audit's domain and save it."""
    try:
        client = supabase_admin or supabase
        
        # Get domain from audit
        audit_res = client.table('audits').select('results, campaign_id, campaigns(domain)').eq('id', audit_id).execute()
        if not audit_res.data:
            return jsonify({'error': 'Audit not found'}), 404
        
        record = audit_res.data[0]
        results = record.get('results', {}) or {}
        campaign = record.get('campaigns', {}) or {}
        domain = results.get('competitor_domain') or campaign.get('domain', '')
        if domain:
            domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
        
        if not domain:
            return jsonify({'error': 'No domain found'}), 400
        
        from execution.pagespeed_insights import fetch_pagespeed_scores
        pagespeed = {}
        
        mobile = fetch_pagespeed_scores(f"https://{domain}", strategy="mobile")
        if mobile and mobile.get('success'):
            pagespeed['mobile'] = {'scores': mobile.get('scores', {}), 'metrics': mobile.get('metrics', {})}
            pagespeed['scores'] = mobile.get('scores', {})
            pagespeed['metrics'] = mobile.get('metrics', {})
        
        # Desktop fetch skipped during auto-generate to save ~80s
        # Users can manually trigger desktop via the dashboard's refresh button
        
        if not pagespeed:
            return jsonify({'error': 'PageSpeed fetch failed'}), 500
        
        # Save to audits.results
        results['pagespeed'] = pagespeed
        client.table('audits').update({'results': results}).eq('id', audit_id).execute()
        
        # Save to projects.full_audit_data
        try:
            proj = client.table('projects').select('id, full_audit_data').eq('audit_id', audit_id).execute()
            if proj.data:
                fad = proj.data[0].get('full_audit_data', {}) or {}
                fad['pagespeed'] = pagespeed
                client.table('projects').update({'full_audit_data': fad}).eq('id', proj.data[0]['id']).execute()
        except Exception as e:
            logger.warning(f"Could not update projects pagespeed: {e}")
        
        logger.info(f"PageSpeed refreshed for {domain}: perf={pagespeed.get('scores', {}).get('performance', 'N/A')}")
        return jsonify({'success': True, 'pagespeed': pagespeed})
    
    except Exception as e:
        logger.error(f"Error refreshing pagespeed: {e}")
        return jsonify({'error': str(e)}), 500





# =========================================================================
# CONTENT TAB — PORTED FROM SEO SYSTEM
# =========================================================================

def log_debug(message):
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"Logging failed: {e}", file=sys.stderr)


# --- get_title_from_url ---
def get_title_from_url(url):
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path
        segments = [s for s in path.split('/') if s]
        if not segments: return "Home"
        slug = segments[-1]
        return slug.replace('-', ' ').replace('_', ' ').title()
    except:
        return "Untitled Page"


# --- perform_gemini_research ---
def perform_gemini_research(topic, location="US", language="English"):
    """
    Uses Gemini 2.5 Flash with Google Search Grounding to perform free research.
    Returns structured data: {
        "competitors": [{"url": "...", "title": "...", "domain": "..."}],
        "keywords": [{"keyword": "...", "intent": "..."}],
    }
    """
    log_debug(f"Starting Gemini 2.5 Flash Grounded Research for: {topic} (Loc: {location}, Lang: {language})")
    
    try:
        prompt = f"""
        Research the SEO topic: "{topic}"
        
        **CONTEXT**:
        - Target Audience Location: {location}
        - Target Language: {language}
        
        Perform a deep analysis using Google Search to find:
        1. Top 3 Competitor URLs ranking for this topic in **{location}**.
        2. **At least 30 SEO Keywords** relevant to this topic (include Search Intent).
           - Focus on keywords trending in **{location}**.
           - Mix of short-tail and long-tail.
           - Include "People Also Ask" style questions relevant to this region.
           
        **PRIORITIZATION RULES**:
        1. **Primary Focus**: Prioritize keywords specifically trending in **{location}**.
        2. **Global Keywords**: You MAY include high-volume US/Global keywords if they are highly relevant, but they must be secondary to local terms.
        3. **Relevance**: Ensure all keywords are actionable for a user in {location}.
        
        Output strictly in JSON format:
        {{
            "competitors": [
                {{"url": "https://...", "title": "Page Title", "domain": "domain.com"}}
            ],
            "keywords": [
                {{"keyword": "keyword phrase", "intent": "Informational/Commercial/Transactional"}}
            ]
        }}
        """
        
        text = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=True
        )
        
        if not text:
            raise Exception("Empty response from Gemini REST API")
        
        # Clean markdown code blocks if present
        if text.startswith('```json'): text = text[7:]
        if text.startswith('```'): text = text[3:]
        if text.endswith('```'): text = text[:-3]
            
        return json.loads(text.strip())
        
    except Exception as e:
        log_debug(f"Gemini Research Failed: {e}")
        return None


# --- generate_image_prompt (L3229-3248) ---
def generate_image_prompt(topic, summary=""):
    """Generates an image prompt using Gemini."""
    prompt = f"""
    Create a detailed image generation prompt for a blog post titled: "{topic}"
    Summary: {summary[:500]}

    The image should be:
    - Visually matching the theme and tone of the article (e.g., if it's about nature, use natural elements; if tech, use modern tech aesthetics).
    - Strictly PHOTOREALISTIC, cinematic lighting, 8k resolution, highly detailed photography style.
    - NOT 3D render, NOT illustration, NOT cartoon.
    - No text in the image.
    - Aspect Ratio: 16:9

    Output ONLY the prompt text, no explanations.
    """
    try:
        return gemini_client.generate_content(prompt=prompt, model_name="gemini-2.5-flash")
    except Exception as e:
        print(f"Error generating image prompt: {e}")
        return f"A professional, modern header image for a blog post about {topic}, high quality, 4k, no text"


# --- research_with_perplexity (L3251-3431) ---
def research_with_perplexity(query, location="US", language="English", stage="MoFu"):
    """
    Conducts deep research using Perplexity's Sonar Pro model.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    
    if not api_key:
        log_debug("Perplexity API key missing - skipping research")
        print("Perplexity API key missing - skipping research")
        return {"research": "Perplexity API not configured", "citations": []}
    
    log_debug(f"Perplexity API key found: {api_key[:10]}...")
    
    # Define stage description
    stage_desc = "Middle-of-Funnel (MoFu)" if stage == "MoFu" else "Top-of-Funnel (ToFu)"
    
    try:
        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "sonar-pro",  # Using deep research model
            "max_tokens": 8000,  # Force longer, comprehensive responses
            "messages": [{
                "role": "user",
                "content": f"""**Role**: You are a Senior Content Strategist and Market Researcher conducting deep-dive competitive analysis.

**CRITICAL: LENGTH & DEPTH REQUIREMENTS**:
- This research brief MUST be MINIMUM 2500 words
- Each section requires DEEP ANALYSIS, not summaries
- Include SPECIFIC data points, prices, percentages, and citations
- Competitor analysis must include 3+ competitors with detailed strengths/weaknesses
- Content outline must have complete H2/H3/H4 structure with key points for EACH section

**Objective**: Create a comprehensive Research Brief for a {stage_desc} content asset. This must be the MOST authoritative resource on this topic, outranking all competitors with superior data, utility, and insight.

**CONTEXT**:
- Target Audience Location: {location}
- Target Language: {language}

**LOCALIZATION RULES (CRITICAL)**:
1. **Currency**: You MUST use the local currency for **{location}** (e.g., ₹ INR for India). Convert any research prices (like $) to the local currency using approximate current rates.
2. **Units**: Use the measurement system standard for **{location}**.
3. **Spelling**: Use the correct spelling dialect (e.g., "Colour" for UK/India).

{query}

**CRITICAL RULES**:
- GENERATE A COMPLETE BRIEF based on the provided data and your general knowledge
- Use the provided competitor URLs and scraped text as your primary source
- If specific data is missing, use INDUSTRY BENCHMARKS or GENERAL CATEGORY KNOWLEDGE relevant to **{location}**
- Do not refuse to generate sections - provide the best available estimates
- Format as markdown with ## headers

---

## 1. Strategic Overview

**Proposed Title**: [SEO-optimized H1 using "Best X for Y 2025" or "Product A vs B vs C" format]

**Search Intent**: [Analyze based on the provided keyword list: Informational/Commercial/Transactional]

**Format Strategy**: [Why this format fits the MoFu stage]

---

## 2. Key Insights & Benchmarks (The Evidence)

**Market Data & Specifications** (Extract from content or use category knowledge):
- [Key Feature/Spec 1]: [Value/Description]
- [Key Feature/Spec 2]: [Value/Description]
- [Price Range]: [Estimated category range]
- [User Ratings]: [Typical sentiment/rating]
- [Technical Specs]: [Ingredients, dimensions, etc.]

**Expert/Industry Concepts**:
- [Key Concept 1]: [Explanation]
- [Key Concept 2]: [Explanation]

---

## 3. Competitor Landscape & Content Gaps

**Competitor Analysis** (Based on provided URLs):
- **Competitor 1**: [Name/URL]
  - Strengths: [What they cover well]
  - Weaknesses: [What they miss]
- **Competitor 2**: [Name/URL]
  - Strengths: [What they cover well]
  - Weaknesses: [What they miss]

**The "Blue Ocean" Gap**: [The ONE angle or utility missing from the above competitors. E.g., "No one compares X vs Y directly" or "Missing detailed ingredient breakdown"]

---

## 4. Comprehensive Content Outline

**Type**: [Comparison Guide / Buying Guide / Ultimate Guide]

**Title**: [Final SEO-optimized H1]

**Detailed Structure**:

### H2: Introduction
- Hook: [Problem/Stat]
- Scope: [What's covered]

### H2: [Main Section 1 - Category Overview]
- H3: [Subtopic from keyword list]
  - **Key Point**: [Detail]
- H3: [Subtopic from keyword list]
  - **Key Point**: [Detail]

### H2: [Comparison Section]
- H3: Comparison Chart
  - **Columns**: [Attribute 1], [Attribute 2], [Attribute 3]
  - **Data Source**: [Competitor content or benchmarks]
- H3: [Product A] vs [Competitors]
  - **Differentiator**: [Specific advantage]

### H2: [Buying Guide / Selection Criteria]
- H3: Who is this for?
  - **User Type 1**: [Recommendation]
  - **User Type 2**: [Recommendation]

### H2: FAQ
- [Question from keyword list]: [Answer]
- [Question from keyword list]: [Answer]

### H2: Conclusion
- Final Recommendation
- CTA

---

## 5. Unique Ranking Hypothesis

[Explain why this content will outrank competitors based on the gaps identified above. Focus on: Better data, clearer structure, or more comprehensive scope.]

**GENERATE THE COMPLETE BRIEF NOW.**
"""
            }],
            "return_citations": True,
            "search_recency_filter": "month"
        }
        
        log_debug(f"Calling Perplexity API with query: {query[:50]}...")
        print(f"Researching with Perplexity: {query[:100]}...")
        # Increased timeout to 180s for deep research
        response = requests.post(url, headers=headers, json=payload, timeout=180)
        log_debug(f"Perplexity response status: {response.status_code}")
        
        data = response.json()
        
        if 'choices' in data and len(data['choices']) > 0:
            content = data['choices'][0]['message']['content']
            citations = data.get('citations', [])
            
            log_debug(f"✓ Perplexity success! {len(citations)} citations")
            print(f"✓ Research completed. Found {len(citations)} citations")
            for i, cite in enumerate(citations[:3]):
                print(f"  Citation {i+1}: {cite}")
            
            return {
                "research": content,
                "citations": citations
            }
        else:
            log_debug(f"Unexpected Perplexity response structure: {str(data)[:200]}")
            print(f"Unexpected Perplexity response: {data}")
            return {"research": "Research failed", "citations": []}
            
    except Exception as e:
        log_debug(f"Perplexity error: {type(e).__name__} - {str(e)}")
        print(f"Perplexity research error: {e}")
        import traceback
        traceback.print_exc()
        return {"research": f"Error: {str(e)}", "citations": []}


# --- scrape_page_content (L3977-4218) ---
def scrape_page_content(url):
    """
    Scrapes a URL and returns structured content including body text, title, and meta data.
    Uses Jina Reader as PRIMARY method (renders JavaScript, free, no API key).
    Falls back to BeautifulSoup + Gemini if Jina fails.
    """
    import requests
    import re
    from bs4 import BeautifulSoup

    try:
        print(f"Scraping content for: {url}")
        
        # --- PRIMARY METHOD: JINA READER ---
        # Jina renders JavaScript and returns clean markdown
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        
        raw_jina_content = None
        page_title = None
        meta_description = ""
        
        try:
            print("DEBUG: Trying Jina Reader...")
            response = requests.get(jina_url, headers=headers, timeout=45)
            
            if response.status_code == 200 and len(response.text) > 500:
                raw_jina_content = response.text
                print(f"DEBUG: Jina returned {len(raw_jina_content)} chars")
                
                # Extract title from Jina markdown (first H1)
                title_match = re.search(r'^#\s+(.+)$', raw_jina_content, re.MULTILINE)
                if title_match:
                    page_title = title_match.group(1).strip()
                
                # Try to extract title from the === underline format too
                if not page_title:
                    title_match2 = re.search(r'\n([^\n]+)\n=+\n', raw_jina_content)
                    if title_match2:
                        page_title = title_match2.group(1).strip()
        except Exception as je:
            print(f"DEBUG: Jina failed: {je}")
            raw_jina_content = None
        
        # --- CHUNKED GEMINI PROCESSING ---
        # Process content in chunks to avoid truncation while keeping Gemini's excellent formatting
        body_content = ""
        
        if raw_jina_content and len(raw_jina_content) > 500:
            # Pre-process: Remove footer sections before chunking
            content = raw_jina_content
            footer_markers = [
                # Keep only truly generic footer items. 
                # user reported 'Complete Your Routine' cuts off Ingredients/FAQ which follow it.
                r'\nJoin Our Community\n.*',
                r'\n## Footer\n.*',
            ]
            for pattern in footer_markers:
                match = re.search(pattern, content, flags=re.DOTALL)
                if match:
                    content = content[:match.start()]
            
            # Remove image markdown links
            content = re.sub(r'!\[Image \d+:[^\]]*\]\(https?://[^\)]+\)', '', content)
            content = re.sub(r'!\[[^\]]*\]\([^\)]+\)', '', content)
            
            print(f"DEBUG: Pre-cleaned content: {len(content)} chars")
            
            # Split into chunks (15K each to leave room for prompt)
            chunk_size = 15000
            chunks = []
            for i in range(0, len(content), chunk_size):
                chunks.append(content[i:i + chunk_size])
            
            print(f"DEBUG: Processing {len(chunks)} chunks with Gemini")
            
            processed_chunks = []
            for idx, chunk in enumerate(chunks):
                try:
                    if idx == 0:
                        # First chunk: Smart Start Detection + Formatting
                        prompt = f"""You are a precise content extractor. Your goal is to identify the MAIN PRODUCT CONTENT within this raw text and format it as clean Markdown.

CRITICAL INSTRUCTIONS:

1. **FIND THE START**: 
   - Skip "Cart", "Browse our Bestsellers" lists, Navigation menus, and Header links.
   - Start extracting from the **Main Product Title** (e.g., "Turmeric Shield | SPF 40 PA+++").

2. **FIND THE END**:
   - Keep ALL sections: Description, Benefits, Ingredients, How to Use, Clinical Results, Verified Reviews, FAQ.
   - Stop ONLY when you reach the generic site-wide footer (e.g. "Subscribe", "About 82°E", "Follow us").

3. **STRICT PRESERVATION**:
   - **NO CUTTING**: Do NOT remove any text within the main content boundaries.
   - **NO SUMMARIZING**: Output the content word-for-word.
   - **NO REORDERING**: Keep sections in their original sequence.

4. **FORMATTING**:
   - Use `#` for the Main Title.
   - Use `##` or `###` for section headers.
   - Use `**bold**` for labels.
   - Format lists with `-`.

CONTENT (Part {idx + 1} of {len(chunks)}):
{chunk}

Return the extracted and formatted markdown:"""
                    else:
                        # Subsequent chunks: Continuation with strict rules
                        prompt = f"""Continue processing this content.
RULES:
1. **NO HEADER/NAV REMOVAL** (This is a continuation chunk, so treat as body content).
2. **NO CUTTING / NO SUMMARIZING**.
3. **Format as clean Markdown**.
4. **Keep all Reviews, FAQs, Ingredients**.

CONTENT (Part {idx + 1} of {len(chunks)}):
{chunk}

Return the formatted markdown:"""
                    
                    result = gemini_client.generate_content(
                        prompt=prompt,
                        model_name="gemini-2.5-flash"
                    )
                    
                    if result:
                        cleaned = result.strip().replace('```markdown', '').replace('```', '').strip()
                        processed_chunks.append(cleaned)
                        print(f"DEBUG: Chunk {idx + 1}: {len(chunk)} chars -> {len(cleaned)} chars")
                    else:
                        # Fallback: use raw chunk
                        processed_chunks.append(chunk)
                        print(f"DEBUG: Chunk {idx + 1}: Gemini failed, using raw")
                        
                except Exception as e:
                    print(f"DEBUG: Chunk {idx + 1} error: {e}")
                    processed_chunks.append(chunk)
            
            # Concatenate all processed chunks
            body_content = "\n\n".join(processed_chunks)
            
            # Final cleanup
            body_content = re.sub(r'\n{4,}', '\n\n\n', body_content)
            
            print(f"DEBUG: Final content: {len(body_content)} chars")
        
        # --- FALLBACK: BeautifulSoup + Gemini ---
        if not body_content or len(body_content) < 200:
            print("DEBUG: Jina content insufficient, falling back to BeautifulSoup...")
            
            # Use Robust Scraper Helper
            content, status_code, final_url = fetch_html_robust(url)
            
            if status_code == 200 and content:
                soup = BeautifulSoup(content, 'html.parser')
                
                # Extract Title
                if not page_title:
                    if soup.title:
                        page_title = soup.title.get_text(strip=True)
                    elif soup.find('meta', attrs={'property': 'og:title'}):
                        page_title = soup.find('meta', attrs={'property': 'og:title'}).get('content')
                    elif soup.find('h1'):
                        page_title = soup.find('h1').get_text(strip=True)
                
                # Extract Meta Description
                meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
                if meta_desc:
                    meta_description = meta_desc.get('content', '')
                
                # Extract JSON-LD
                json_ld_content = ""
                try:
                    json_scripts = soup.find_all('script', type='application/ld+json')
                    for script in json_scripts:
                        if script.string:
                            try:
                                data = json.loads(script.string)
                                if isinstance(data, list):
                                    for item in data:
                                        if item.get('@type') == 'Product':
                                            json_ld_content += f"\nProduct: {item.get('name')}\nDescription: {item.get('description')}\n"
                                elif isinstance(data, dict) and data.get('@type') == 'Product':
                                    json_ld_content += f"\nProduct: {data.get('name')}\nDescription: {data.get('description')}\n"
                            except: pass
                except: pass
                
                # Clean and extract text
                for unwanted in soup(["script", "style", "svg", "noscript", "iframe", "nav", "footer", "aside"]):
                    unwanted.decompose()
                
                body_content = soup.get_text(separator='\n', strip=True)
                
                # Use Gemini for intelligent extraction if content is messy
                if len(body_content) > 1000:
                    try:
                        extraction_prompt = f"""Extract the main product/page content from this text. 
Remove navigation, headers, footers, and promotional noise.
Return clean markdown with:
- Product/Page Title
- Description
- Key features/benefits
- Ingredients (if product)
- How to use (if applicable)

Text:
{body_content[:8000]}"""
                        
                        gemini_result = gemini_client.generate_content(
                            prompt=extraction_prompt,
                            model_name="gemini-2.5-flash"
                        )
                        if gemini_result and len(gemini_result) > 200:
                            body_content = gemini_result.strip()
                            body_content = body_content.replace('```markdown', '').replace('```', '').strip()
                    except Exception as ge:
                        print(f"DEBUG: Gemini extraction failed: {ge}")
        
        # Final fallback for title
        if not page_title:
            page_title = url.split('/')[-1].replace('-', ' ').replace('_', ' ').title()
        
        if not body_content:
            body_content = "Could not extract meaningful content"
        
        return {
            "title": page_title,
            "body_content": body_content,
            "meta_description": meta_description,
            "json_ld": ""
        }

    except Exception as e:
        print(f"Scraping error: {e}")
        import traceback
        traceback.print_exc()
        return None


# --- generate_content_via_rest (L4254-4294) ---
# --- generate_dynamic_outline (Chunked Workflow) ---
def generate_dynamic_outline(topic, research_context, project_loc, gemini_client):
    """Generates a structured JSON outline for the article based on research."""
    print(f"DEBUG: Generating Dynamic Outline for '{topic}'...", flush=True)
    
    prompt = f"""
    You are an expert Content Strategist. Create a detailed Outline for a "Best-in-Class" SEO article.
    TOPIC: {topic}
    TARGET AUDIENCE: {project_loc}
    RESEARCH BRIEF:
    {research_context[:15000]} 
    TASK:
    Create a logical H2 structure for a comprehensive 2500-4500 word article.
    REQUIRED SECTIONS:
    1. "Introduction" - Hook with a relatable problem, Quick Answer box (2-3 sentence TL;DR), then thesis
    2. A "Common Problems/Mistakes" section - Frame as USER problems (e.g., "Why Most X Fail"), NOT as "How This Article Is Different"
    3. A "Self-Diagnosis/Framework" section - Actionable tool for readers to identify their situation
    4. "Detailed Breakdown" - Models/Types/Categories with specific comparisons
    5. "ROI & Hidden Costs" - Financials/Risks/Realistic timelines
    6. "Conclusion & Action Steps" - Clear next steps, NOT generic summary
    7. "FAQ" (Schema-ready) - 3-5 questions people actually search
    CRITICAL RULES:
    - Frame ALL headers as USER PROBLEMS, not self-promotion
    - BAD: "Why This Article Is Different" / "How We Provide Better Advice"
    - GOOD: "Why Most Vegan Eye Creams Don't Work for Indian Skin" / "Common Mistakes When Choosing X"
    - NO meta-commentary about the article itself
    - Headers should be search-query-aligned (what users would type in Google)
    OUTPUT FORMAT (JSON ARRAY):
    [
        {{"title": "Introduction", "instructions": "Hook with relatable problem. Include **Quick Answer:** box with 2-3 sentence summary. State thesis."}},
        {{"title": "Why Most X Fail for [Audience]", "instructions": "Problem-focused section. Use research data to explain common issues."}},
        ...
    ]
    """
    
    try:
        response = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-pro",
            use_grounding=False # Logic only
        )
        
        # Clean JSON
        if not response: return []
        cleaned = response.strip()
        if cleaned.startswith('```json'): cleaned = cleaned[7:]
        if cleaned.startswith('```'): cleaned = cleaned[3:]
        if cleaned.endswith('```'): cleaned = cleaned[:-3]
        
        import json
        return json.loads(cleaned.strip())
    except Exception as e:
        print(f"Error generating outline: {e}")
        # Fallback Outline
        return [
            {"title": "Introduction", "instructions": "Introduction to the topic."},
            {"title": "Key Concepts", "instructions": "Explain the core concepts."},
            {"title": "Detailed Analysis", "instructions": "Deep dive into the details."},
            {"title": "Comparison", "instructions": "Compare options."},
            {"title": "Conclusion", "instructions": "Wrap up."}
        ]

# --- generate_sections_chunked (Chunked Workflow) ---
def generate_sections_chunked(topic, outline, research_context, project_loc, gemini_client, links_str):
    """Generates the article section by section based on the outline."""
    full_content = []
    import re
    
    print(f"DEBUG: Starting Chunked Generation for '{topic}' ({len(outline)} sections)...", flush=True)
    
    # Context Window Management (Keep it relevant)
    previous_section_summary = "Start of article."
    
    # Smart Link Tracking
    links_inserted_count = 0
    target_links = 7  # Target 7 internal links across the article
    link_cap = 8  # Never exceed 8 links
    
    for i, section in enumerate(outline):
        section_title = section.get('title', f"Section {i+1}")
        instructions = section.get('instructions', '')
        
        print(f"  > Generating Section {i+1}/{len(outline)}: {section_title}...", flush=True)
        
        # Smart Linking Logic
        link_instruction = ""
        if links_str and links_str != "No internal links available":
            remaining_sections = len(outline) - (i + 1)
            needed_links = target_links - links_inserted_count
            
            if links_inserted_count >= link_cap:
                link_instruction = "9. **Internal Links**: Do NOT include any more internal links (cap reached)."
            elif needed_links > 0:
                if needed_links >= remaining_sections:  # Must insert now to hit target
                    link_instruction = f"9. **Internal Links (REQUIRED)**: You MUST include EXACTLY 1 internal link in this section from the links below. Use natural, descriptive anchor text (NOT 'click here'). Links: {links_str}"
                else:  # Encourage but don't force
                    link_instruction = f"9. **Internal Links (Encouraged)**: Try to naturally include 1 internal link from: {links_str}. Use descriptive anchor text."
            else:
                link_instruction = "9. **Internal Links**: Optional - only if highly relevant."
        else:
            link_instruction = "9. **Internal Links**: No internal links available."
        
        prompt = f"""
        Write ONE section of a long-form SEO guide. You're a seasoned practitioner, not a textbook.
        TOPIC: {topic}
        CURRENT SECTION: {section_title}
        INSTRUCTIONS: {instructions}
        CONTEXT:
        - Audience Location: {project_loc}
        - Previous Section Summary: {previous_section_summary}
        RESEARCH DATA (Use strictly):
        {research_context[:10000]}
        
        WRITING STYLE — THIS IS CRITICAL:
        - Write like a sharp human expert writing a blog post, NOT like an AI assistant.
        - Vary your sentence lengths dramatically. Mix 5-word punches with 25-word explanations. Some paragraphs should be 1 sentence.
        - Use contractions naturally ("don't", "it's", "you'll", "that's").
        - NEVER use these AI giveaway phrases: "It's important to note", "In today's digital landscape", "Let's dive in", "Here's the thing", "When it comes to", "It's worth noting", "In conclusion", "Furthermore", "Moreover", "Additionally", "Consequently", "In order to", "Utilize", "Leverage", "Navigate", "Landscape", "Realm", "Delve".
        - Start some sentences with "But", "And", "So", or "Look," — real writers do this.
        - Inject brief, confident opinions: "Honestly, most agencies get this wrong." or "This is where it gets interesting."
        - Skip the perfectly balanced paragraph structure. Real articles are messy — some sections are dense, others are quick hits.
        - Reference specific numbers, tools, or examples from the research. Vague generalities = AI red flag.
        - Occasional rhetorical questions are fine. Over-using them is not.
        
        FORMATTING RULES:
        1. Use Markdown (H2 for section title, H3/H4 for subsections).
        2. NO INTRO/OUTRO FLUFF. Dive straight into the content.
        3. Use bullet points, data tables, and bold text for readability.
        4. If mentioning a competitor/product from research, be specific (Pros/Cons).
        5. LENGTH: 400-600 words for this section.
        6. NEVER write self-referential statements like "This article is different" or "This guide provides".
        7. If this is the INTRODUCTION: Include a "**Quick Answer:**" box at the start with a 2-3 sentence summary.
        8. Frame problems as USER problems ("Why X fails for you") NOT self-promotion.
        {link_instruction}
        """
        
        try:
            section_content = gemini_client.generate_content(
                prompt=prompt,
                model_name="gemini-2.5-pro",
                use_grounding=True 
            )
            
            if section_content:
                # Clean up
                if section_content.startswith('```markdown'): section_content = section_content[11:]
                if section_content.startswith('```'): section_content = section_content[3:]
                if section_content.endswith('```'): section_content = section_content[:-3]
                
                full_content.append(section_content.strip())
                
                # Count links inserted in this chunk
                links_in_chunk = len(re.findall(r'\[.*?\]\(https?://.*?\)', section_content))
                links_inserted_count += links_in_chunk
                print(f"DEBUG: Section {i+1} generated {links_in_chunk} links. Total: {links_inserted_count}/{target_links}", flush=True)
                
                # Update summary for next chunk (simple context propagation)
                previous_section_summary = f"Just covered {section_title}. Key points: {section_content[:200]}..."
            else:
                full_content.append(f"## {section_title}\n\n(Content generation failed for this section.)")
                
        except Exception as e:
            print(f"Error generating section '{section_title}': {e}")
            full_content.append(f"## {section_title}\n\n(Error generating content.)")
            
        # Rate limit pause
        import time
        time.sleep(2)
        
    return "\\n\\n".join(full_content)

# --- final_polish (Chunked Workflow) ---
def final_polish(full_content, topic, primary_keyword, cta_url, project_loc, gemini_client):
    """Assembles the chunks and adds a cohesive Intro, Outro, and Meta Description."""
    print(f"DEBUG: Polishing final article for '{topic}'...", flush=True)
    
    prompt = f"""
    You are a sharp editor polishing a long-form article. Your job is to assemble, smooth transitions, and add a killer intro + conclusion. Keep the writer's voice intact — it should sound like a real expert wrote it, not an AI.
    
    TOPIC: {topic}
    PRIMARY KEYWORD: {primary_keyword}
    CTA URL: {cta_url}
    LOCATION: {project_loc}
    
    RAW CONTENT CHUNKS:
    {full_content[:25000]} 
    
    TASK:
    1. Write a **Killer Introduction** (H1 Title + Hook + Thesis).
       - H1 must contain "{primary_keyword}".
       - Hook should be a bold statement, surprising stat, or provocative question — NOT a generic "In today's world..." opener.
    2. Review the body content (passed above) and smooth out transitions if needed (keep the bulk of it).
    3. Write a **High-Conversion Conclusion**.
       - Must end with a Call-to-Action (CTA) linking to: {cta_url}
    4. Write a **Meta Description** (155 chars, SEO optimized).
    
    CRITICAL EDITING RULES:
    - PRESERVE the conversational, opinionated tone. Don't flatten it into corporate-speak.
    - BANNED PHRASES (remove any you find): "It's important to note", "In today's digital landscape", "Let's dive in", "Furthermore", "Moreover", "Additionally", "Consequently", "In order to", "Utilize", "Leverage", "Navigate", "Delve", "robust", "comprehensive", "streamline".
    - Use contractions naturally throughout ("don't", "it's", "you'll").
    - Vary sentence lengths. Short punches mixed with longer explanations.
    
    OUTPUT FORMAT (Markdown):
    **Meta Description**: [Your Description Here]
    
    # [H1 Title]
    
    [Introduction]
    
    [Body Content - Inserted/Polished]
    
    [Conclusion + CTA]
    """
    
    try:
        final_text = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-pro",
            use_grounding=False # Editing task
        )
        return final_text if final_text else full_content
    except Exception as e:
        print(f"Error in final polish: {e}")
        return full_content

def generate_content_via_rest(prompt, api_key, model="gemini-2.5-pro", use_grounding=True):
    """
    Generate content using Gemini REST API directly to avoid SDK crashes.
    Supports Google Search Grounding.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    if use_grounding:
        data["tools"] = [{
            "google_search": {}  # Enable Google Search Grounding
        }]
        
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        # Extract text
        try:
            text = result['candidates'][0]['content']['parts'][0]['text']
            print(f"DEBUG: REST API Success. Text length: {len(text)}", flush=True)
            return text
        except (KeyError, IndexError):
            print(f"DEBUG: Unexpected REST response structure: {result}", flush=True)
            return None
            
    except Exception as e:
        print(f"DEBUG: REST API call failed: {e}")
        if 'response' in locals() and response is not None:
             print(f"DEBUG: Response content: {response.text}")
        raise e


# --- perform_seo_analysis (L4300-4453) ---
def perform_seo_analysis(page_id):
    """
    Analyzes a page for SEO issues and returns structured recommendations.
    Returns JSON with critical_issues, ai_search_gaps, content_gaps, structure_issues, overall_score.
    """
    print(f"DEBUG: Starting SEO Analysis for page_id: {page_id}", flush=True)
    
    # Fetch page data
    page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
    if not page_res.data:
        return {"error": "Page not found"}
    
    page = page_res.data
    page_type = page.get('page_type', 'page')
    tech_data = page.get('tech_audit_data', {})
    body_content = tech_data.get('body_content', '')
    page_title = tech_data.get('title', page.get('url', ''))
    meta_desc = tech_data.get('meta_description', '')
    
    # Fetch project settings
    project_loc = 'US'
    project_lang = 'English'
    try:
        project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', page['project_id']).single().execute()
        if project_res.data:
            _settings = project_res.data.get('settings', {}) or {}
            project_loc = _settings.get('location', 'US')
            project_lang = _settings.get('language', 'English')
    except Exception as e:
        print(f"DEBUG: Error fetching project settings: {e}")
    
    if not body_content or len(body_content) < 100:
        return {
            "error": "Insufficient content for analysis. Scrape content first.",
            "overall_score": 0
        }
    
    # Build SEO Analysis Prompt
    prompt = f"""You are an expert SEO Analyst. Analyze this {page_type.upper()} page for SEO issues and gaps.

**PAGE DETAILS**:
- URL: {page.get('url', '')}
- Page Type: {page_type}
- Title: {page_title}
- Meta Description: {meta_desc}
- Location Target: {project_loc}
- Language: {project_lang}

**CURRENT PAGE CONTENT**:
{body_content[:8000]}

**ANALYZE FOR**:

1. **CRITICAL SEO ISSUES** (Must Fix):
   - Missing or poor H1 tag
   - Missing/weak meta description (should be 150-160 chars)
   - Keyword stuffing or no keyword focus
   - Missing alt text on images
   - Thin content (<300 words for products, <800 for articles)
   
2. **AI SEARCH OPTIMIZATION** (For Google AI Overview, Bing Copilot):
   - Missing FAQ sections (crucial for AI snippets)
   - No clear answer paragraphs (AI pulls concise answers)
   - Missing structured data opportunities
   - Lack of E-E-A-T signals (Experience, Expertise, Authority, Trust)
   
3. **CONTENT GAPS** (Based on {page_type}):
   - For Products: Missing specs, benefits, use cases, social proof
   - For Categories: Missing comparison points, buyer guides
   - For Blogs: Missing depth, citations, actionable advice
   
4. **INTERNAL LINKING**:
   - Missing opportunities to link to other pages
   
5. **STRUCTURE ISSUES**:
   - Poor heading hierarchy (H2, H3)
   - Wall of text without breaks
   - Missing bullet points or lists

**OUTPUT FORMAT** (Return ONLY valid JSON):
{{
    "critical_issues": [
        {{"issue": "...", "severity": "high|medium|low", "fix": "..."}}
    ],
    "ai_search_gaps": [
        {{"gap": "...", "recommendation": "..."}}
    ],
    "content_gaps": [
        {{"gap": "...", "suggestion": "..."}}
    ],
    "structure_issues": [
        {{"issue": "...", "fix": "..."}}
    ],
    "overall_score": 65,
    "summary": "Brief 2-sentence summary of the biggest problems"
}}
"""
    
    try:
        result = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=False  # Pure analysis, no web search
        )
        
        if not result:
            return {"error": "SEO Analysis failed - empty response", "overall_score": 0}
        
        # Clean and parse JSON
        text = result.strip()
        if text.startswith('```json'): text = text[7:]
        if text.startswith('```'): text = text[3:]
        if text.endswith('```'): text = text[:-3]
        
        analysis = json.loads(text.strip())
        print(f"DEBUG: SEO Analysis complete. Score: {analysis.get('overall_score', 'N/A')}", flush=True)
        
        return analysis
        
    except json.JSONDecodeError as e:
        print(f"DEBUG: Failed to parse SEO analysis JSON: {e}")
        return {"error": f"Failed to parse analysis: {e}", "overall_score": 0}
    except Exception as e:
        print(f"DEBUG: SEO Analysis error: {e}")
        return {"error": str(e), "overall_score": 0}


@app.route('/api/analyze-seo', methods=['POST'])
def analyze_seo_endpoint():
    """Endpoint to analyze a page for SEO issues."""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        data = request.json
        page_id = data.get('page_id')
        
        if not page_id:
            return jsonify({"error": "page_id required"}), 400
        
        # Perform analysis
        analysis = perform_seo_analysis(page_id)
        
        if "error" in analysis and analysis.get("overall_score") == 0:
            return jsonify(analysis), 400
        
        # Save analysis to database
        (supabase_admin or supabase).table('pages').update({
            'seo_analysis': analysis
        }).eq('id', page_id).execute()
        
        return jsonify(analysis)
        
    except Exception as e:
        print(f"ERROR in analyze-seo: {e}")


# --- batch_update_pages (L4456-5767) ---
@app.route('/api/batch-update-pages', methods=['POST'])
def batch_update_pages():
    print(f"====== BATCH UPDATE PAGES CALLED ======", flush=True)
    log_debug("Entered batch_update_pages route")
    log_debug(f"Entered batch_update_pages route")
    if not supabase: return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        data = request.json
        log_debug(f"Received batch update data: {data}")
        page_ids = data.get('page_ids', [])
        action = data.get('action')
        
        if not page_ids or not action:
            return jsonify({"error": "page_ids and action required"}), 400
            
        if action == 'trigger_audit':
            # In a real app, this would trigger a background job
            (supabase_admin or supabase).table('pages').update({"audit_status": "Pending"}).in_('id', page_ids).execute()
            
        elif action == 'trigger_classification':
            (supabase_admin or supabase).table('pages').update({"classification_status": "Pending"}).in_('id', page_ids).execute()
            
        elif action == 'approve_strategy':
            (supabase_admin or supabase).table('pages').update({"approval_status": True}).in_('id', page_ids).execute()
            
        elif action == 'scrape_content':
            # Scrape existing content for selected pages
            for page_id in page_ids:
                page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
                if not page_res.data: continue
                page = page_res.data
                
                try:
                    scraped_data = scrape_page_content(page['url'])
                    
                    if scraped_data:
                        # Update tech_audit_data with body_content AND title
                        current_tech_data = page.get('tech_audit_data', {})
                        current_tech_data['body_content'] = scraped_data['body_content']
                        
                        if not current_tech_data.get('title') or current_tech_data.get('title') == 'Untitled':
                             current_tech_data['title'] = scraped_data['title'] or get_title_from_url(page['url'])
                        
                        (supabase_admin or supabase).table('pages').update({
                            "tech_audit_data": current_tech_data
                        }).eq('id', page_id).execute()
                        print(f"✓ Scraped content for {page['url']}")
                    else:
                        print(f"⚠ Failed to scrape {page['url']}")
                        
                except Exception as e:
                    print(f"Error scraping page {page_id}: {e}")
            
            return jsonify({"message": "Content scraped successfully"})
        elif action == 'generate_content':
            # Product/Category pages use gemini_client for SEO verification
            # Topic pages use gemini_client (no grounding needed - they have research already)
            
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return jsonify({"error": "GEMINI_API_KEY not found"}), 500

            def process_content_generation_background(page_ids, api_key):
                print(f"====== GENERATE_CONTENT BACKGROUND THREAD STARTED ======", flush=True)
                
                for page_id in page_ids:
                    try:
                        # 1. Get Page Data
                        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
                        if not page_res.data: continue
                        page = page_res.data
                        
                        # 2. Get existing content
                        existing_content = page.get('tech_audit_data', {}).get('body_content', '')
                        if not existing_content:
                            # If no body content, try to scrape it now
                            try:
                                logging.info(f"DEBUG: No existing content for {page['url']}, attempting fresh scrape...")
                                scraped_data = scrape_page_content(page['url'])
                                if scraped_data and scraped_data.get('body_content'):
                                    existing_content = scraped_data['body_content']
                                    logging.info(f"DEBUG: Fresh scrape successful ({len(existing_content)} bytes)")
                                else:
                                    existing_content = "No content available"
                                    logging.info("DEBUG: Fresh scrape returned no content")
                            except Exception as e:
                                logging.error(f"Error scraping content for {page['url']}: {e}")
                                existing_content = "No content available"
                        
                        # 3. Generate improved content
                        page_title = page.get('tech_audit_data', {}).get('title', page.get('url', ''))
                        page_type = page.get('page_type', 'page')

                        # Fetch Project Settings for Localization
                        project_loc = 'US'
                        project_lang = 'English'
                        try:
                            log_debug(f"Fetching project settings for {page['project_id']}...")
                            project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', page['project_id']).single().execute()
                            if project_res.data:
                                _settings = project_res.data.get('settings', {}) or {}
                                project_loc = _settings.get('location', 'US')
                                project_lang = _settings.get('language', 'English')
                            log_debug(f"Project settings: Loc={project_loc}, Lang={project_lang}")
                        except Exception as proj_err:
                            log_debug(f"Error fetching project settings: {proj_err}")
                        
                        try:
                            log_debug(f"Checking page type for branching: '{page_type}'")
                            
                            # Fetch Parent Page Context (for Internal Linking)
                            parent_context = ""
                            if page.get('source_page_id'):
                                try:
                                    # 1. Fetch Parent (MoFu)
                                    parent_res = (supabase_admin or supabase).table('pages').select('id, url, tech_audit_data, source_page_id').eq('id', page['source_page_id']).single().execute()
                                    if parent_res.data:
                                        p_data = parent_res.data
                                        p_title = p_data.get('tech_audit_data', {}).get('title', 'Related Page')
                                        p_url = p_data.get('url', '#')
                                        
                                        # 2. Fetch Grandparent (Product) if exists
                                        gp_context = ""
                                        if p_data.get('source_page_id'):
                                            try:
                                                gp_res = (supabase_admin or supabase).table('pages').select('url, tech_audit_data').eq('id', p_data['source_page_id']).single().execute()
                                                if gp_res.data:
                                                    gp_data = gp_res.data
                                                    gp_title = gp_data.get('tech_audit_data', {}).get('title', 'Main Product')
                                                    gp_url = gp_data.get('url', '#')
                                                    gp_context = f"\n    - ALSO link to the Main Product: [{gp_title}]({gp_url}) (Context: The ultimate solution)."
                                            except Exception:
                                                pass # Ignore grandparent errors

                                        parent_context = f"\n    **INTERNAL LINKING REQUIREMENT**:\n    - You MUST organically mention and link to the parent page: [{p_title}]({p_url}) (Context: Next step in learning).\n{gp_context}"
                                except Exception as parent_err:
                                    log_debug(f"Error fetching parent context: {parent_err}")

                            # ==============================================
                            # AUTO SEO ANALYSIS (for Product/Category pages)
                            # ==============================================
                            seo_issues_str = ""
                            seo_analysis = None
                            if page_type and page_type.lower().strip() in ['product', 'category']:
                                print(f"DEBUG: Running auto SEO analysis for {page_type} page...", flush=True)
                                try:
                                    seo_analysis = perform_seo_analysis(page_id)
                                    
                                    if seo_analysis and not seo_analysis.get('error'):
                                        # Format issues for the prompt
                                        issues_list = []
                                        
                                        # Critical issues
                                        for item in seo_analysis.get('critical_issues', []):
                                            issues_list.append(f"- {item.get('issue')}: {item.get('fix')}")
                                        
                                        # AI search gaps
                                        for item in seo_analysis.get('ai_search_gaps', []):
                                            issues_list.append(f"- {item.get('gap')}: {item.get('recommendation')}")
                                        
                                        # Content gaps
                                        for item in seo_analysis.get('content_gaps', []):
                                            issues_list.append(f"- {item.get('gap')}: {item.get('suggestion')}")
                                        
                                        # Structure issues
                                        for item in seo_analysis.get('structure_issues', []):
                                            issues_list.append(f"- {item.get('issue')}: {item.get('fix')}")
                                        
                                        if issues_list:
                                            seo_issues_str = "\n**SEO ISSUES TO FIX** (from analysis):\n" + "\n".join(issues_list[:10])  # Limit to top 10
                                            print(f"DEBUG: Found {len(issues_list)} SEO issues to fix. Score: {seo_analysis.get('overall_score')}", flush=True)
                                        
                                        # Save analysis to DB
                                        (supabase_admin or supabase).table('pages').update({
                                            'seo_analysis': seo_analysis
                                        }).eq('id', page_id).execute()
                                        
                                except Exception as seo_err:
                                    print(f"DEBUG: SEO analysis failed (non-blocking): {seo_err}", flush=True)
                                    seo_issues_str = ""

                            # BRANCHING LOGIC: Product vs Category vs Topic
                            generated_text = ""
                            if page_type and page_type.lower().strip() == 'product':
                                log_debug("Entered Product generation block")
                                # PRODUCT PROMPT (Sales & Conversion Focused - Conservative + Grounded)
                                prompt = f"""You are an expert E-commerce Copywriter with access to live Google Search.
                                
            **TASK**: Polish and enhance the content for this **PRODUCT PAGE**. 
            **CRITICAL GOAL**: Improve clarity and persuasion WITHOUT changing the original length or structure significantly.

            **CONTEXT**:
            - Target Audience Location: {project_loc}
            - Target Language: {project_lang}

            **LOCALIZATION RULES (CRITICAL)**:
            1. **Currency**: You MUST use the local currency for **{project_loc}** (e.g., ₹ INR for India). Convert prices if needed.
            2. **Units**: Use the measurement system standard for **{project_loc}**.
            3. **Spelling**: Use the correct spelling dialect (e.g., "Colour" for UK/India).
            4. **Cultural Context**: Use examples relevant to **{project_loc}**.

            **PAGE DETAILS**:
            - URL: {page['url']}
            - Title: {page_title}
            - Product Name: {page_title}

            **EXISTING CONTENT** (Source of Truth):
            ```
            {existing_content if existing_content else "No content"}
            ```
            {seo_issues_str}

            **INSTRUCTIONS**:
            1.  **Strict Polish (NO RESTRUCTURING)**: 
                -   Keep the **exact** original section order (Intro -> Benefits -> Clinical -> Ingredients -> FAQ -> Reviews).
                -   Do NOT merge sections or move them around.
                -   Do NOT remove any reviews or list items. If there are 10 reviews, keep 10.

            2.  **Maintain Length & Detail**: 
                -   The output must be **at least** the same length as the original. 
                -   Do NOT summarize or condense text.
                -   **CRITICAL**: If there is a list of details (e.g., "Ingredient X: Definition Y"), KEEP THE ENTIRE LIST. Do not turn it into a paragraph.
                -   Keep all technical details, ingredient lists, and specs exactly as is.

            3.  **Enhance, Don't Rewrite**: 
                -   Only fix grammar, flow, and punchiness.
                -   Add SEO keywords naturally where they fit, but don't rewrite entire paragraphs just to fit them.

            4.  **STRICT ACCURACY**: 
                -   **DO NOT CHANGE** technical specs, ingredients, dimensions, or "What's Inside".
                -   **DO NOT INVENT** features.

            5.  **Competitive Intelligence** (USE GROUNDING):
                -   Search for similar products to understand competitive positioning
                -   Verify any comparative claims ("best", "top-rated") against live data
                -   Identify unique selling points vs competitors

            **OUTPUT FORMAT** (Markdown):
            -   Return the full page content in Markdown.
            -   Include a **Meta Description** at the top.
            -   Keep the original formatting (H1, H2, bullets) but polished.
            """
                                # Use REST API for Products
                                print(f"DEBUG: Generating content for Product: {page_title} using gemini-2.5-pro (REST)", flush=True)
                                generated_text = generate_content_via_rest(
                                    prompt=prompt,
                                    api_key=api_key,
                                    model="gemini-2.5-pro",
                                    use_grounding=True
                                )
                            
                            elif page_type and page_type.lower() == 'category':
                                # CATEGORY PROMPT (Research-Backed SEO Enhancement - Grounded + Respect Length)
                                prompt = f"""You are an expert E-commerce Copywriter & SEO Specialist.

            **TASK**: Enhance this **CATEGORY/COLLECTION PAGE** using real-time search data.
            **CRITICAL GOAL**: infuse the content with high-value SEO keywords and competitive insights while respecting the original length and structure.

            **CONTEXT**:
            - Target Audience Location: {project_loc}
            - Target Language: {project_lang}

            **LOCALIZATION RULES (CRITICAL)**:
            1. **Currency**: You MUST use the local currency for **{project_loc}** (e.g., ₹ INR for India). Convert prices if needed.
            2. **Units**: Use the measurement system standard for **{project_loc}**.
            3. **Spelling**: Use the correct spelling dialect (e.g., "Colour" for UK/India).
            4. **Cultural Context**: Use examples relevant to **{project_loc}**.

            **PAGE DETAILS**:
            - URL: {page['url']}
            - Title: {page_title}
            - Category Name: {page_title}

            **EXISTING CONTENT** (Source of Truth):
            ```
            {existing_content}
            ```
            {seo_issues_str}

            **INSTRUCTIONS**:
            1.  **Research First (USE GROUNDING)**:
                -   Search for top-ranking competitors for "{page_title}" in **{project_loc}**.
                -   Identify the **primary intent** (e.g., "buy cheap", "luxury", "guide") and align the copy.
                -   Find 3-5 **semantic keywords** competitors are using that are missing here.

            2.  **Enhance & Optimize (The "Better" Part)**:
                -   Rewrite the existing text to include these new keywords naturally.
                -   Improve the value proposition based on what competitors offer.
                -   Make it **better SEO-wise**: clearer headings, stronger hook, better keyword density.

            3.  **Respect Constraints**:
                -   **Length**: Keep it roughly the same length (+/- 10%). Do NOT add massive new sections (like FAQs) unless the original had them.
                -   **Structure**: Maintain the existing flow (Intro -> Products -> Outro).

            4.  **Meta Description**:
                -   Write a new, high-CTR Meta Description (150-160 chars).

            **OUTPUT FORMAT** (Markdown):
            -   Return the full page content in Markdown.
            -   Include a **Meta Description** at the top.
            """
                                # Use REST API for Categories
                                generated_text = generate_content_via_rest(
                                    prompt=prompt,
                                    api_key=api_key,
                                    model="gemini-2.5-pro",
                                    use_grounding=True
                                )
                                
                            elif page_type == 'Topic':
                                # CHUNKED GENERATION LOGIC (New "Best-in-Class" Workflow)
                                print(f"DEBUG: Starting Chunked Workflow for {page_title}...", flush=True)
                                
                                # Get research data
                                research_data = page.get('research_data', {})
                                keyword_cluster = research_data.get('keyword_cluster', [])
                                primary_keyword = research_data.get('primary_keyword', page_title)
                                perplexity_research = research_data.get('perplexity_research', '')
                                citations = research_data.get('citations', [])
                                funnel_stage = page.get('funnel_stage', '')
                                source_page_id = page.get('source_page_id')
                                
                                # Internal Links Logic
                                internal_links = []
                                cta_url = None # URL for the final CTA
                                
                                if source_page_id:
                                    try:
                                        parent_res = (supabase_admin or supabase).table('pages').select('id, url, tech_audit_data, source_page_id').eq('id', source_page_id).single().execute()
                                        if parent_res.data:
                                            parent = parent_res.data
                                            parent_title = parent.get('tech_audit_data', {}).get('title', parent.get('url'))
                                            if funnel_stage == 'MoFu':
                                                internal_links.append(f"- {parent_title} (Main Product): {parent['url']}")
                                                cta_url = parent['url']
                                            elif funnel_stage == 'ToFu':
                                                # ToFu links: MoFu parent (2x) + Product grandparent (2-3x)
                                                internal_links.append(f"- {parent_title} (In-Depth Guide - USE 2 TIMES): {parent['url']}")
                                                grandparent_id = parent.get('source_page_id')
                                                if grandparent_id:
                                                    gp_res = (supabase_admin or supabase).table('pages').select('url, tech_audit_data').eq('id', grandparent_id).single().execute()
                                                    if gp_res.data:
                                                        gp_title = gp_res.data.get('tech_audit_data', {}).get('title', gp_res.data.get('url'))
                                                        internal_links.append(f"- {gp_title} (Main Product - USE 2-3 TIMES): {gp_res.data['url']}")
                                                        cta_url = gp_res.data['url'] # Prefer Grandparent (Product) for ToFu CTA
                                                
                                                if not cta_url: cta_url = parent['url'] # Fallback to Parent if no GP
                                    except Exception as e:
                                        print(f"Error fetching internal links: {e}")
                                links_str = '\n'.join(internal_links) if internal_links else "No internal links available"
                                
                                # Format keywords & citations
                                if keyword_cluster:
                                    kw_list = '\n'.join([f"- {kw['keyword']} ({kw['volume']}/mo, Score: {kw.get('score', 0)})" for kw in keyword_cluster[:15]])
                                else:
                                    kw_list = f"- {primary_keyword}"
                                citations_str = '\n'.join([f"[{i+1}] {cite}" for i, cite in enumerate(citations[:10])]) if citations else "No citations available"
                                
                                # Research Section
                                research_section = ""
                                if perplexity_research:
                                    research_section = f"# DEEP RESEARCH BRIEF (Source: Perplexity):\n{perplexity_research}\n\n# CITATIONS:\n{citations_str}"

                                # 1. Generate Dynamic Outline
                                outline = generate_dynamic_outline(page_title, research_section, project_loc, gemini_client)
                                if not outline:
                                    raise Exception("Failed to generate outline")
                                
                                # 2. Generate Sections (Chunked)
                                full_content = generate_sections_chunked(page_title, outline, research_section, project_loc, gemini_client, links_str)
                                
                                # 3. Final Polish (Intro/Outro/Meta)
                                generated_text = final_polish(full_content, page_title, primary_keyword, cta_url, project_loc, gemini_client)

                            if not generated_text:
                                raise Exception("Content generation returned empty string")

                            # Humanizer removed — fix AI-detection at the prompt level instead

                            # Parse Meta Description if present

                            # Parse Meta Description if present
                            # PRESERVE existing scraped meta_description as default
                            existing_meta = page.get('tech_audit_data', {}).get('meta_description', '')
                            meta_desc = existing_meta if existing_meta else "No description available"
                            
                            # Parse Meta Description using Regex (More Robust)
                            try:
                                # Primary: Look for XML tags <meta-description>...</meta-description>
                                meta_match = re.search(r'<meta-description>\s*(.+?)\s*</meta-description>', generated_text, re.IGNORECASE | re.DOTALL)
                                
                                # Fallback: Look for "Meta Description:" text label
                                if not meta_match:
                                    meta_match = re.search(r'Meta Description.*:\s*(.+)', generated_text, re.IGNORECASE)

                                if meta_match:
                                    extracted_meta = meta_match.group(1).strip()
                                    extracted_meta = extracted_meta.strip('*# ') # Cleanup
                                    if extracted_meta:
                                        meta_desc = extracted_meta
                            except Exception as e:
                                log_debug(f"Meta extraction failed: {e}")
                            
                            # Update Page
                            (supabase_admin or supabase).table('pages').update({
                                "content": generated_text,
                                "status": "Generated",
                                "product_action": "Idle",
                                "tech_audit_data": {
                                    **page.get('tech_audit_data', {}),
                                    "meta_description": meta_desc
                                }
                            }).eq('id', page_id).execute()
                            
                            log_debug(f"Content generated successfully for {page_title}")

                        except Exception as gen_err:
                            log_debug(f"Generation error for {page_title}: {gen_err}")
                            import traceback
                            traceback.print_exc()
                            # Reset status
                            (supabase_admin or supabase).table('pages').update({"product_action": "Idle"}).eq('id', page_id).execute()
                            
                    except Exception as e:
                        log_debug(f"Outer error for {page_id}: {e}")
                        try:
                            (supabase_admin or supabase).table('pages').update({"product_action": "Idle"}).eq('id', page_id).execute()
                        except: pass

            # Update status to Processing IMMEDIATELY (Before thread starts)
            # This ensures frontend sees the loading state
            for pid in page_ids:
                try:
                    (supabase_admin or supabase).table('pages').update({
                        "product_action": "Processing Content..."
                    }).eq('id', pid).execute()
                except: pass

            # Start background thread
            log_debug("Starting background Content Generation thread...")
            thread = threading.Thread(target=process_content_generation_background, args=(page_ids, api_key))
            thread.start()
            
            return jsonify({"message": "Content generation started in background."}), 202


        elif action == 'conduct_research':
            # SIMPLIFIED: Perplexity Research Brief ONLY
            # (Keywords/Competitors are already done in generate_mofu)
            
            def process_research_background(page_ids, api_key):
                print(f"====== CONDUCT_RESEARCH BACKGROUND THREAD STARTED ======", flush=True)
                log_debug(f"CONDUCT_RESEARCH: Starting for {len(page_ids)} pages")
                
                for page_id in page_ids:
                    print(f"DEBUG: Processing page_id: {page_id}", flush=True)
                    try:
                        # Get the Topic page
                        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
                        if not page_res.data: continue
                        
                        page = page_res.data
                        topic_title = page.get('tech_audit_data', {}).get('title', '')
                        research_data = page.get('research_data') or {}
                        
                        if not topic_title: continue
                        
                        log_debug(f"Researching topic (Perplexity): {topic_title}")
                        
                        # Get existing keywords/competitors
                        keywords = research_data.get('ranked_keywords', [])
                        competitor_urls = research_data.get('competitor_urls', [])
                        
                        # Fetch Project Settings for Localization
                        project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', page['project_id']).single().execute()
                        _settings = (project_res.data.get('settings', {}) or {}) if project_res.data else {}
                        project_loc = _settings.get('location', 'US')
                        project_lang = _settings.get('language', 'English')
                        
                        # Get funnel stage
                        funnel_stage = page.get('funnel_stage') or 'MoFu'
                        
                        # Fallback: If no keywords (maybe old page), run Gemini now
                        if not keywords:
                            log_debug(f"No keywords found for {topic_title}. Running Gemini fallback (Loc: {project_loc})...")
                            gemini_result = perform_gemini_research(topic_title, location=project_loc, language=project_lang)
                            if gemini_result:
                                keywords = gemini_result.get('keywords', [])
                                competitor_urls = [c['url'] for c in gemini_result.get('competitors', [])]
                                # Update research data immediately
                                research_data.update({
                                    "competitor_urls": competitor_urls,
                                    "ranked_keywords": keywords,
                                    "formatted_keywords": '\n'.join([f"{kw.get('keyword', '')} | {kw.get('intent', 'informational')} |" for kw in keywords])
                                })
                        
                        # Prepare query for Perplexity
                        keyword_list = ", ".join([k.get('keyword', '') for k in keywords[:15]])
                        competitor_list = ", ".join(competitor_urls)
                        
                        research_query = f"""
                        Research Topic: {topic_title}
                        Top Competitors: {competitor_list}
                        Top Keywords: {keyword_list}
                        
                        Create a detailed Content Research Brief for this topic.
                        Analyze the competitors and keywords to find content gaps.
                        Focus on User Pain Points, Key Subtopics, and Scientific/Technical details.
                        """
                        
                        log_debug(f"Starting Perplexity Research for brief (Loc: {project_loc}, Stage: {funnel_stage})...")
                        perplexity_result = research_with_perplexity(research_query, location=project_loc, language=project_lang, stage=funnel_stage)
                        
                        # Update research data with brief
                        research_data.update({
                            "stage": "complete",
                            "mode": "hybrid",
                            "perplexity_research": perplexity_result.get('research', ''),
                            "citations": perplexity_result.get('citations', [])
                        })
                        
                        # Update page
                        (supabase_admin or supabase).table('pages').update({
                            "research_data": research_data,
                            "product_action": "Idle"
                        }).eq('id', page_id).execute()
                        
                        log_debug(f"Research complete for {topic_title}")
                        
                    except Exception as e:
                        log_debug(f"Research error: {e}")
                        import traceback
                        traceback.print_exc()
                        # Reset status on error
                        try:
                            (supabase_admin or supabase).table('pages').update({"product_action": "Idle"}).eq('id', page_id).execute()
                        except: pass

            # Update status to Processing IMMEDIATELY (Before thread starts)
            # This ensures frontend sees the loading state
            for pid in page_ids:
                try:
                    (supabase_admin or supabase).table('pages').update({
                        "product_action": "Processing Research..."
                    }).eq('id', pid).execute()
                except: pass

            # Start background thread
            log_debug("Starting background Research thread...")
            thread = threading.Thread(target=process_research_background, args=(page_ids, os.environ.get("GEMINI_API_KEY")))
            thread.start()
            
            return jsonify({"message": "Research started in background. The status will update to 'Processing...' in the table."}), 202


            return jsonify({"message": "Content generated successfully"})

        elif action == 'generate_mofu':
            print(f"====== GENERATE MOFU ACTION ======", flush=True)
            log_debug(f"GENERATE_MOFU: Starting for {len(page_ids)} pages")
            print(f"DEBUG: Received generate_mofu action for page_ids: {page_ids}")
            print(f"DEBUG: Received generate_mofu action for page_ids: {page_ids}")
            # Use gemini_client with Grounding (ENABLED!)
            # This helps verify that the topic angles are actually trending/relevant.
            # client = genai_new.Client(api_key=os.environ.get("GEMINI_API_KEY")) # REMOVED
            # tool = types.Tool(google_search=types.GoogleSearch()) # REMOVED
            
            def process_mofu_generation(page_ids, api_key):
                log_debug(f"Background MoFu thread started for pages: {page_ids}")
                try:
                    # Use gemini_client with Grounding (ENABLED!)
                    # client = genai_new.Client(api_key=api_key) # REMOVED
                    # tool = types.Tool(google_search=types.GoogleSearch()) # REMOVED
                    
                    for pid in page_ids:
                        print(f"DEBUG: Processing page_id: {pid}")
                        # Get Product Page Data
                        res = (supabase_admin or supabase).table('pages').select('*').eq('id', pid).single().execute()
                        if not res.data: 
                            print(f"DEBUG: Page {pid} not found")
                            continue
                        product = res.data
                        product_tech = product.get('tech_audit_data', {})


                        
                        print(f"Researching MoFu opportunities for {product.get('url')}...")
                        
                        # === NEW DATA-FIRST WORKFLOW ===
                        
                        # Step 0: Ensure Content Context (Fix for "Memoir vs Candles")
                        body_content = product_tech.get('body_content', '')
                        product_title = product_tech.get('title', 'Untitled')
                        
                        # FIX: If title is "Pending Scan" or generic, force scrape to get REAL title
                        is_bad_title = not product_title or 'pending' in product_title.lower() or 'untitled' in product_title.lower() or 'scan' in product_title.lower()
                        
                        if not body_content or len(body_content) < 100 or is_bad_title:
                            log_debug(f"Content/Title missing or bad ('{product_title}') for {product['url']}, scraping now...")
                            scraped = scrape_page_content(product['url'])
                            if scraped:
                                body_content = scraped['body_content']
                                # Use scraped title if current is bad
                                if is_bad_title and scraped.get('title'):
                                    product_title = scraped['title']
                                    log_debug(f"Updated title from '{product_tech.get('title')}' to '{product_title}'")
                                
                                # Update DB so we don't scrape again
                                current_tech = product.get('tech_audit_data', {})
                                current_tech['body_content'] = body_content
                                current_tech['title'] = product_title # Save real title
                                
                                (supabase_admin or supabase).table('pages').update({
                                    "tech_audit_data": current_tech
                                }).eq('id', pid).execute()
                                product_tech = current_tech # Update local var
                        
                        log_debug(f"Using Product Title: {product_title}")

                        # Fetch Source Product Page
                        product_res = (supabase_admin or supabase).table('pages').select('*').eq('id', pid).single().execute()
                        if not product_res.data:
                            print(f"DEBUG: Product page not found for ID: {pid}", flush=True)
                            continue
                        product = product_res.data
                        product_title = product.get('tech_audit_data', {}).get('title', '')
                        print(f"DEBUG: Processing Product: {product_title}", flush=True)
                        
                        # Fetch Project Settings
                        project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', product['project_id']).single().execute()
                        _settings = (project_res.data.get('settings', {}) or {}) if project_res.data else {}
                        project_loc = _settings.get('location', 'US')
                        project_lang = _settings.get('language', 'English')
                        print(f"DEBUG: Project Settings: {project_loc}, {project_lang}", flush=True)

                        # Step 1: Get Keywords
                        keywords = []
                        # (Skipping to where I can inject prints easily)
                        # I'll just add prints around the Gemini call in the next block
                        # Step 1: Generate MULTIPLE Broad Seed Keywords for DataForSEO
                        # Strategy: Don't search for specific product - search for CATEGORY + common queries
                        if not product_title:
                            product_title = get_title_from_url(product['url'])

                        print(f"DEBUG: Analyzing context for: {product_title} (Loc: {project_loc}, Lang: {project_lang})")
                        
                        try:
                            # NEW STRATEGY: Generate multiple broad seeds
                            context_prompt = f"""Analyze this product to generate 3-5 BROAD keyword seeds for DataForSEO research.

        Product Title: "{product_title}"
        Page Content: {body_content[:2000]}

        Task:
        1. Identify the product CATEGORY (e.g., "carrier oils", "lipstick", "sunscreen", "candles")
        2. Generate 3-5 BROAD search terms that people use when researching this category in **{project_loc}**.
        3. DO NOT use the specific product name - use GENERIC category terms

        Examples:
        - Product: "Apricot Kernel Oil" → Seeds: ["carrier oil benefits", "oil for skin", "facial oils", "natural oils skincare"]
        - Product: "MAC Ruby Woo Lipstick" → Seeds: ["red lipstick", "matte lipstick", "long lasting lipstick", "lipstick shades"]
        - Product: "Supergoop Sunscreen" → Seeds: ["face sunscreen", "spf for skin", "sunscreen benefits", "daily sunscreen"]

        OUTPUT: Return ONLY a comma-separated list of 3-5 broad keywords. No explanations.
        Example output: carrier oil benefits, oil for skin, facial oils, natural oils"""
                            
                            seed_res_text = gemini_client.generate_content(
                                prompt=context_prompt,
                                model_name="gemini-2.5-flash",
                                use_grounding=True
                            )
                            seeds_str = seed_res_text.strip().replace('"', '').replace("'", "") if seed_res_text else ""
                            broad_seeds = [s.strip() for s in seeds_str.split(',') if s.strip()]
                            
                            # Fallback if AI fails
                            if not broad_seeds:
                                broad_seeds = [product_title]
                            
                            log_debug(f"Generated {len(broad_seeds)} broad seeds: {broad_seeds}")
                            print(f"DEBUG: Broad seed keywords: {broad_seeds}")
                            
                        except Exception as e:
                            print(f"⚠ Seed generation failed: {e}. Using product title.")
                            broad_seeds = [product_title]

                        
                        # NEW: Use Gemini 2.0 Flash with Grounding as PRIMARY source (User Request)
                        print(f"DEBUG: Using Gemini 2.0 Flash for keyword research (Primary)...")
                        log_debug("Calling perform_gemini_research as PRIMARY source")
                        
                        gemini_result = perform_gemini_research(product_title, location=project_loc, language=project_lang)
                        keywords = []
                        
                        if gemini_result and gemini_result.get('keywords'):
                            print(f"✓ Gemini Research successful. Found {len(gemini_result['keywords'])} keywords.")
                            for k in gemini_result['keywords']:
                                keywords.append({
                                    'keyword': k.get('keyword'),
                                    'volume': 100, # Placeholder volume since Gemini doesn't provide it
                                    'score': 100,
                                    'cpc': 0,
                                    'competition': 0,
                                    'intent': k.get('intent', 'Commercial')
                                })
                        else:
                            print(f"⚠ Gemini Research failed. Using fallback.")
                            keywords = [{'keyword': product_title, 'volume': 0, 'score': 0, 'cpc': 0, 'competition': 0}]


                        
                        # Step 2: Prepare Data for Topic Generation (No Deep Research yet)
                        log_debug("Skipping deep research (will be done in 'Conduct Research' stage).")
                        
                        # Format keyword list for prompt
                        keyword_list = '\n'.join([f"- {k['keyword']} ({k['volume']} searches/month)" for k in keywords[:50]])
                        
                        # Minimal research data for now
                        research_data = {
                            "keywords": keywords,
                            "stage": "research_pending"
                        }


                        # Step 4: Generate Topics from REAL DATA
                        import datetime
                        current_year = datetime.datetime.now().year
                        next_year = current_year + 1
                        
                        topic_prompt = f"""You are an SEO Content Strategist. Generate 6 MoFu (Middle-of-Funnel) article topics based on REAL keyword data.

        **Product**: {product_title}
        **Target Audience**: {project_loc} ({project_lang})

        **VERIFIED HIGH-VOLUME KEYWORDS** (Scored by Opportunity):
        {keyword_list}

        **YOUR TASK**:
        Create 6 MoFu topics. For EACH topic, assign ALL semantically relevant keywords from the list above (could be 3-15 keywords per topic - include as many as naturally fit the angle).

        **Requirements**:
        1. Each topic must target a primary keyword (highest opportunity score for that angle)
        2. Include ALL secondary keywords that semantically match the topic angle
        3. Topics should be Middle-of-Funnel (Comparison, Best Of, Guide, vs)

        **Topic Types**:
        - "Best X for Y in {current_year}" (roundup/comparison)
        - "Product vs Competitor" (head-to-head comparison)
        - "Top Alternatives to X" (alternative guides)  
        - Use cases backed by research

        **Output Format** (JSON):
        {{
          "topics": [
            {{
              "title": "[Exact title - include year {current_year} if relevant]",
              "slug": "url-friendly-slug",
              "description": "2-sentence description of content angle",
              "keyword_cluster": [
                {{"keyword": "[keyword1]", "volume": [INTEGER_FROM_INPUT], "is_primary": true}},
                {{"keyword": "[keyword2]", "volume": [INTEGER_FROM_INPUT], "is_primary": false}},
                ...
              ],
              "research_notes": "Why this topic (reference SERP competitor or research insight)"
            }}
          ]
        }}

        CRITICAL: 
        1. Use EXACT integers for volume from the provided list. DO NOT write "Estimated".
        2. Assign keywords based on semantic relevance. Don't artificially limit - if 12 keywords fit a topic, include all 12.
        """


                        
                        try:
                            text = gemini_client.generate_content(
                                prompt=topic_prompt,
                                model_name="gemini-2.5-flash",
                                use_grounding=True
                            )
                            if not text: raise Exception("Empty response from Gemini")
                            text = text.strip()
                            if text.startswith('```json'): text = text[7:]
                            if text.startswith('```'): text = text[3:]
                            if text.endswith('```'): text = text[:-3]
                            text = text.strip()
                            
                            # Parse JSON with error handling
                            try:
                                data = json.loads(text)
                            except json.JSONDecodeError as json_err:
                                log_debug(f"JSON parse error: {json_err}. Response: {text[:300]}")
                                print(f"✗ Gemini returned invalid JSON. Skipping MoFu for {product_title}")
                                continue  # Skip to next product
                            
                            topics = data.get('topics', [])
                            if not topics:
                                log_debug("No topics in AI response")
                                continue
                            
                            new_pages = []
                            for t in topics:
                                # Handle keyword cluster (multiple keywords per topic)
                                keyword_cluster = t.get('keyword_cluster', [])
                                
                                if keyword_cluster:
                                    # NEW FORMAT: "keyword | intent | secondary intent" (no volume)
                                    # Classify intent based on keyword patterns
                                    def classify_intent(kw_text):
                                        kw_lower = kw_text.lower()
                                        # Transactional indicators
                                        if any(word in kw_lower for word in ['buy', 'price', 'shop', 'purchase', 'best', 'top', 'review', 'vs', 'alternative']):
                                            return 'transactional'
                                        # Commercial indicators
                                        elif any(word in kw_lower for word in ['benefits', 'how to', 'uses', 'guide', 'comparison', 'difference']):
                                            return 'commercial'
                                        # Default: informational
                                        else:
                                            return 'informational'
                                    
                                    keywords_str = '\n'.join([
                                        f"{kw['keyword']} | {classify_intent(kw['keyword'])} |"
                                        for kw in keyword_cluster
                                    ])
                                    # Get primary keyword for research reference
                                    primary_kw = next((kw for kw in keyword_cluster if kw.get('is_primary')), keyword_cluster[0] if keyword_cluster else {})
                                else:
                                    keywords_str = ""
                                    primary_kw = {}
                                
                                # Combine general research with topic-specific notes
                                topic_research = research_data.copy()
                                topic_research['notes'] = t.get('research_notes', '')
                                topic_research['keyword_cluster'] = keyword_cluster
                                topic_research['primary_keyword'] = primary_kw.get('keyword', '')
                                
                                new_pages.append({
                                    "project_id": product['project_id'],
                                    "source_page_id": pid,
                                    "url": f"{product['url'].rstrip('/')}/{t['slug']}",
                                    "page_type": "Topic",
                                    "funnel_stage": "MoFu",
                                    "product_action": "Idle",
                                    "tech_audit_data": {
                                        "title": t['title'],
                                        "meta_description": t['description'],
                                        "meta_title": t['title']
                                    },
                                    "content_description": t['description'],
                                    "keywords": keywords_str,  # Data-backed keywords with volume
                                    "slug": t['slug'],
                                    "research_data": topic_research  # Store all research including citations
                                })
                            
                            
                            
                            if new_pages:
                                print(f"DEBUG: Attempting to insert {len(new_pages)} MoFu topics...", file=sys.stderr)
                                try:
                                    insert_res = (supabase_admin or supabase).table('pages').insert(new_pages).execute()
                                    print("DEBUG: ✓ MoFu topics inserted successfully.", file=sys.stderr)
                                    
                                    # AUTO-KEYWORD RESEARCH (Gemini)
                                    if insert_res.data:
                                        print(f"DEBUG: Starting Auto-Keyword Research for {len(insert_res.data)} topics...", file=sys.stderr)
                                        for inserted_page in insert_res.data:
                                            try:
                                                p_id = inserted_page['id']
                                                # Handle tech_audit_data being a string or dict
                                                t_data = inserted_page.get('tech_audit_data', {})
                                                if isinstance(t_data, str):
                                                    try: t_data = json.loads(t_data)
                                                    except: t_data = {}
                                                    
                                                p_title = t_data.get('title', '')
                                                if not p_title: continue
                                                
                                                log_debug(f"Auto-Researching keywords for: {p_title} (Loc: {project_loc})")
                                                gemini_result = perform_gemini_research(p_title, location=project_loc, language=project_lang)
                                                
                                                if gemini_result:
                                                    keywords = gemini_result.get('keywords', [])
                                                    formatted_keywords = '\n'.join([
                                                        f"{kw.get('keyword', '')} | {kw.get('intent', 'informational')} |"
                                                        for kw in keywords if kw.get('keyword')
                                                    ])
                                                    
                                                    # Create research data (partial)
                                                    research_data = {
                                                        "stage": "keywords_only", 
                                                        "mode": "hybrid",
                                                        "competitor_urls": [c['url'] for c in gemini_result.get('competitors', [])],
                                                        "ranked_keywords": keywords,
                                                        "formatted_keywords": formatted_keywords
                                                    }
                                                    
                                                    (supabase_admin or supabase).table('pages').update({
                                                        "keywords": formatted_keywords,
                                                        "research_data": research_data
                                                    }).eq('id', p_id).execute()
                                                    log_debug(f"✓ Keywords saved for {p_title}")
                                            except Exception as research_err:
                                                log_debug(f"Auto-Research failed for {p_title}: {research_err}")
                                except Exception as insert_error:
                                    print(f"DEBUG: Error inserting with research_data: {insert_error}", file=sys.stderr)
                                    # Fallback: Try inserting without research_data (if column missing)
                                    if 'research_data' in str(insert_error) or 'column' in str(insert_error):
                                        print("DEBUG: Retrying insert without research_data column...", file=sys.stderr)
                                        for p in new_pages:
                                            p.pop('research_data', None)
                                        (supabase_admin or supabase).table('pages').insert(new_pages).execute()
                                        print("DEBUG: ✓ MoFu topics inserted (without research data).", file=sys.stderr)
                                    else:
                                        raise insert_error
                            else:
                                print("DEBUG: No new pages to insert (topics list empty).", file=sys.stderr)
                            
                            # Update Source Page Status
                            (supabase_admin or supabase).table('pages').update({"product_action": "MoFu Generated"}).eq('id', pid).execute()
                        
                        except Exception as e:
                            print(f"DEBUG: Error generating MoFu topics: {e}", file=sys.stderr)
                            import traceback
                            traceback.print_exc()
                            # Reset status on error so frontend doesn't hang
                            (supabase_admin or supabase).table('pages').update({"product_action": "Failed"}).eq('id', pid).execute()
                            
                except Exception as e:
                    log_debug(f"MoFu Thread Error: {e}")
                    # Ensure we try to reset status for all pages if the whole thread crashes
                    try:
                        (supabase_admin or supabase).table('pages').update({"product_action": "Failed"}).in_('id', page_ids).execute()
                    except: pass
                            
                except Exception as e:
                    log_debug(f"MoFu Thread Error: {e}")

            # Set status to Processing immediately
            try:
                log_debug(f"Updating status to Processing for {page_ids}")
                (supabase_admin or supabase).table('pages').update({"product_action": "Processing..."}).in_('id', page_ids).execute()
            except Exception as e:
                log_debug(f"Failed to update status to Processing: {e}")

            # Start background thread
            log_debug("Starting background MoFu thread...")
            thread = threading.Thread(target=process_mofu_generation, args=(page_ids, os.environ.get("GEMINI_API_KEY")))
            thread.start()
            
            return jsonify({"message": "MoFu generation started in background. The status will update to 'Processing...' in the table."})


        elif action == 'conduct_research':
            # SIMPLIFIED: Perplexity Research Brief ONLY
            # (Keywords/Competitors are already done in generate_mofu)
            
            def process_research_background(page_ids, api_key):
                print(f"====== CONDUCT_RESEARCH BACKGROUND THREAD STARTED ======", flush=True)
                log_debug(f"CONDUCT_RESEARCH: Starting for {len(page_ids)} pages")
                
                for page_id in page_ids:
                    print(f"DEBUG: Processing page_id: {page_id}", flush=True)
                    try:
                        # Update status to Processing
                        (supabase_admin or supabase).table('pages').update({
                            "product_action": "Processing Research..."
                        }).eq('id', page_id).execute()

                        # Get the Topic page
                        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
                        if not page_res.data: continue
                        
                        page = page_res.data
                        topic_title = page.get('tech_audit_data', {}).get('title', '')
                        research_data = page.get('research_data') or {}
                        
                        if not topic_title: continue
                        
                        log_debug(f"Researching topic (Perplexity): {topic_title}")
                        
                        # Get existing keywords/competitors
                        keywords = research_data.get('ranked_keywords', [])
                        competitor_urls = research_data.get('competitor_urls', [])
                        
                        # Fetch Project Settings for Localization
                        project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', page['project_id']).single().execute()
                        _settings = (project_res.data.get('settings', {}) or {}) if project_res.data else {}
                        project_loc = _settings.get('location', 'US')
                        project_lang = _settings.get('language', 'English')
                        
                        # Fallback: If no keywords (maybe old page), run Gemini now
                        if not keywords:
                            log_debug(f"No keywords found for {topic_title}. Running Gemini fallback (Loc: {project_loc})...")
                            gemini_result = perform_gemini_research(topic_title, location=project_loc, language=project_lang)
                            if gemini_result:
                                keywords = gemini_result.get('keywords', [])
                                competitor_urls = [c['url'] for c in gemini_result.get('competitors', [])]
                                # Update research data immediately
                                research_data.update({
                                    "competitor_urls": competitor_urls,
                                    "ranked_keywords": keywords,
                                    "formatted_keywords": '\n'.join([f"{kw.get('keyword', '')} | {kw.get('intent', 'informational')} |" for kw in keywords])
                                })
                        
                        # Prepare query for Perplexity
                        keyword_list = ", ".join([k.get('keyword', '') for k in keywords[:15]])
                        competitor_list = ", ".join(competitor_urls)
                        
                        research_query = f"""
                        Research Topic: {topic_title}
                        Top Competitors: {competitor_list}
                        Top Keywords: {keyword_list}
                        
                        Create a detailed Content Research Brief for this topic.
                        Analyze the competitors and keywords to find content gaps.
                        Focus on User Pain Points, Key Subtopics, and Scientific/Technical details.
                        """
                        
                        log_debug(f"Starting Perplexity Research for brief (Loc: {project_loc})...")
                        perplexity_result = research_with_perplexity(research_query, location=project_loc, language=project_lang)
                        
                        # Update research data with brief
                        research_data.update({
                            "stage": "complete",
                            "mode": "hybrid",
                            "perplexity_research": perplexity_result.get('research', ''),
                            "citations": perplexity_result.get('citations', [])
                        })
                        
                        # Update page
                        (supabase_admin or supabase).table('pages').update({
                            "research_data": research_data,
                            "product_action": "Idle"
                        }).eq('id', page_id).execute()
                        
                        log_debug(f"Research complete for {topic_title}")
                        
                    except Exception as e:
                        log_debug(f"Research error: {e}")
                        import traceback
                        traceback.print_exc()
                        # Reset status on error
                        try:
                            (supabase_admin or supabase).table('pages').update({"product_action": "Idle"}).eq('id', page_id).execute()
                        except: pass

            # Start background thread
            log_debug("Starting background Research thread...")
            thread = threading.Thread(target=process_research_background, args=(page_ids, os.environ.get("GEMINI_API_KEY")))
            thread.start()
            
            return jsonify({"message": "Research started in background. The status will update to 'Processing...' in the table."}), 202


        elif action == 'generate_tofu':
            # AI ToFu Topic Generation
            
            def process_tofu_generation(page_ids, api_key):
                log_debug(f"Background ToFu thread started for pages: {page_ids}")
                try:

                    
                    for pid in page_ids:
                        # Fetch Source MoFu Page
                        mofu_res = (supabase_admin or supabase).table('pages').select('*').eq('id', pid).single().execute()
                        if not mofu_res.data: continue
                        mofu = mofu_res.data
                        mofu_tech = mofu.get('tech_audit_data') or {}
                        
                        print(f"Researching ToFu opportunities for MoFu topic: {mofu_tech.get('title')}...")
                        
                        # === NEW DATA-FIRST WORKFLOW FOR TOFU ===
                        
                        # Fetch Project Settings for Localization (Moved UP)
                        project_res = (supabase_admin or supabase).table('campaigns').select('settings').eq('id', mofu['project_id']).single().execute()
                        _settings = (project_res.data.get('settings', {}) or {}) if project_res.data else {}
                        project_loc = _settings.get('location', 'US')
                        project_lang = _settings.get('language', 'English')

                        # Step 1: Get broad keyword ideas based on MoFu topic
                        mofu_title = mofu_tech.get('title', '')
                        print(f"Researching ToFu opportunities for: {mofu_title} (Loc: {project_loc})")
                        
                        # Get keyword opportunities from DataForSEO
                        # For ToFu, we want broader terms, so we might strip "Best" or "Review" from the seed
                        seed_keyword = mofu_title.replace('Best ', '').replace('Review', '').replace(' vs ', ' ').strip()
                        # NEW: Use Gemini 2.0 Flash with Grounding as PRIMARY source (User Request)
                        print(f"DEBUG: Using Gemini 2.0 Flash for ToFu keyword research (Primary)...")
                        
                        gemini_result = perform_gemini_research(seed_keyword, location=project_loc, language=project_lang)
                        keywords = []
                        
                        if gemini_result and gemini_result.get('keywords'):
                            print(f"✓ Gemini Research successful. Found {len(gemini_result['keywords'])} keywords.")
                            for k in gemini_result['keywords']:
                                keywords.append({
                                    'keyword': k.get('keyword'),
                                    'volume': 100, # Placeholder
                                    'score': 100,
                                    'cpc': 0,
                                    'competition': 0,
                                    'intent': k.get('intent', 'Informational')
                                })
                        else:
                            print(f"⚠ Gemini Research failed. Using fallback.")
                            keywords = [{'keyword': seed_keyword, 'volume': 0, 'score': 0, 'cpc': 0, 'competition': 0}]
                        
                        print(f"DEBUG: Proceeding to Topic Generation with {len(keywords)} keywords...", flush=True)
                        
                        # Step 2: Analyze SERP for top 5 keywords (Optional - keeping for context if fast enough, or remove for speed)
                        # For now, we'll keep it lightweight or rely on Gemini Grounding in the prompt.
                        # Let's SKIP DataForSEO SERP to save time/cost, and rely on Gemini Grounding.
                        serp_summary = "Relied on Gemini Grounding for current SERP context."
                        
                        # Step 3: Generate Topics (Lightweight - No Perplexity)
                        import datetime
                        current_year = datetime.datetime.now().year
                        
                        # Format keyword list for prompt
                        keyword_list = '\n'.join([f"- {k['keyword']} ({k['volume']}/mo, Score: {k.get('score', 0)})" for k in keywords[:100]])

                        topic_prompt = f"""
                        You are an SEO Strategist. Generate 5 High-Value Top-of-Funnel (ToFu) topic ideas that lead to: {mofu_tech.get('title')}
                        
                        **CONTEXT**:
                        - Target Audience: People at the beginning of their journey (Problem Aware).
                        - Location: {project_loc}
                        - Language: {project_lang}
                        - Goal: Educate them and naturally lead them to the solution (the MoFu topic).
                        
                        **HIGH-OPPORTUNITY KEYWORDS**:
                        {keyword_list}
                        
                        **INSTRUCTIONS**:
                        1.  **Use Grounding**: Search Google to ensure these topics are currently relevant and not already saturated in **{project_loc}**.
                        2.  **Focus**: "What is", "How to", "Guide to", "Benefits of", "Mistakes to Avoid".
                        3.  **Variety**: specific angles, not just generic guides.
                        
                        **LOCALIZATION RULES (CRITICAL)**:
                        1. **Currency**: You MUST use the local currency for **{project_loc}** (e.g., ₹ INR for India). Convert prices if needed.
                        2. **Units**: Use the measurement system standard for **{project_loc}**.
                        3. **Spelling**: Use the correct spelling dialect (e.g., "Colour" for UK/India).
                        4. **Cultural Context**: Use examples relevant to **{project_loc}**.
                        
                        Current Date: {datetime.datetime.now().strftime("%B %Y")}
                        
                        Return a JSON object with a key "topics" containing a list of objects:
                        - "title": Topic Title (Must include a primary keyword)
                        - "slug": URL friendly slug
                        - "description": Brief content description (intent)
                        - "keyword_cluster": List of ALL semantically relevant keywords from the list (aim for 30+ per topic if relevant)
                        - "primary_keyword": The main keyword targeted
                        """
                        
                        try:
                            text = gemini_client.generate_content(
                                prompt=topic_prompt,
                                model_name="gemini-2.5-flash",
                                use_grounding=True
                            )
                            if not text: raise Exception("Empty response from Gemini")
                            text = text.strip()
                            if text.startswith('```json'): text = text[7:]
                            if text.startswith('```'): text = text[3:]
                            if text.endswith('```'): text = text[:-3]
                            
                            data = json.loads(text)
                            topics = data.get('topics', [])
                            
                            new_pages = []
                            for t in topics:
                                # Map selected keywords back to their data
                                cluster_data = []
                                for k_str in t.get('keyword_cluster', []):
                                    match = next((k for k in keywords if k['keyword'].lower() == k_str.lower()), None)
                                    if match: cluster_data.append(match)
                                    else: cluster_data.append({'keyword': k_str, 'volume': 0, 'score': 0, 'intent': 'Informational'})
                                
                                # Standardized Format: "keyword | intent |" (Matches MoFu style)
                                keywords_str = '\n'.join([
                                    f"{k['keyword']} | {k.get('intent', 'Informational')} |"
                                    for k in cluster_data
                                ])
                                
                                # Minimal research data (No Perplexity yet)
                                topic_research = {
                                    "stage": "topic_generated",
                                    "keyword_cluster": cluster_data,
                                    "primary_keyword": t.get('primary_keyword')
                                }

                                new_pages.append({
                                    "project_id": mofu['project_id'],
                                    "source_page_id": pid,
                                    "url": f"{mofu['url'].rsplit('/', 1)[0]}/{t['slug']}", 
                                    "page_type": "Topic",
                                    "funnel_stage": "ToFu",
                                    "product_action": "Idle", # Ready for manual "Conduct Research"
                                    "tech_audit_data": {
                                        "title": t['title'],
                                        "meta_description": t['description'],
                                        "meta_title": t['title']
                                    },
                                    "content_description": t['description'],
                                    "keywords": keywords_str,
                                    "slug": t['slug'],
                                    "research_data": topic_research
                                })
                            
                            if new_pages:
                                print(f"Attempting to insert {len(new_pages)} ToFu topics...")
                                insert_res = (supabase_admin or supabase).table('pages').insert(new_pages).execute()
                                print("✓ ToFu topics inserted successfully.")
                                
                                # AUTO-KEYWORD RESEARCH (Gemini) - Architecture Parity with MoFu
                                if insert_res.data:
                                    print(f"DEBUG: Starting Auto-Keyword Research for {len(insert_res.data)} ToFu topics...")
                                    for inserted_page in insert_res.data:
                                        try:
                                            p_id = inserted_page['id']
                                            t_data = inserted_page.get('tech_audit_data', {})
                                            if isinstance(t_data, str):
                                                try: t_data = json.loads(t_data)
                                                except: t_data = {}
                                                
                                            p_title = t_data.get('title', '')
                                            if not p_title: continue
                                            
                                            log_debug(f"Auto-Researching keywords for ToFu: {p_title}")
                                            # Use project location/language for research
                                            gemini_result = perform_gemini_research(p_title, location=project_loc, language=project_lang)
                                            
                                            if gemini_result:
                                                keywords = gemini_result.get('keywords', [])
                                                formatted_keywords = '\n'.join([
                                                    f"{kw.get('keyword', '')} | {kw.get('intent', 'informational')} |"
                                                    for kw in keywords if kw.get('keyword')
                                                ])
                                                
                                                # Create research data (partial)
                                                research_data = {
                                                    "stage": "keywords_only", 
                                                    "mode": "hybrid",
                                                    "competitor_urls": [c['url'] for c in gemini_result.get('competitors', [])],
                                                    "ranked_keywords": keywords,
                                                    "formatted_keywords": formatted_keywords
                                                }
                                                
                                                (supabase_admin or supabase).table('pages').update({
                                                    "keywords": formatted_keywords,
                                                    "research_data": research_data
                                                }).eq('id', p_id).execute()
                                            log_debug(f"✓ Keywords saved for {p_title}")
                                        except Exception as research_err:
                                            log_debug(f"Auto-Research failed for {p_title}: {research_err}")
                            
                            log_debug(f"ToFu generation complete for {pid}. Updating status...")
                            # Update Source Page Status
                            (supabase_admin or supabase).table('pages').update({"product_action": "ToFu Generated"}).eq('id', pid).execute()
                            log_debug(f"Status updated to 'ToFu Generated' for {pid}")
                            
                        except Exception as e:
                            print(f"Error generating ToFu topics: {e}")
                            import traceback
                            traceback.print_exc()
                            # Reset status on error so frontend doesn't hang
                            (supabase_admin or supabase).table('pages').update({"product_action": "Failed"}).eq('id', pid).execute()
                
                except Exception as e:
                    log_debug(f"ToFu Thread Error: {e}")
                    # Ensure we try to reset status for all pages if the whole thread crashes
                    try:
                        (supabase_admin or supabase).table('pages').update({"product_action": "Failed"}).in_('id', page_ids).execute()
                    except: pass

            # Set status to Processing immediately
            try:
                log_debug(f"Updating status to Processing for {page_ids}")
                (supabase_admin or supabase).table('pages').update({"product_action": "Processing..."}).in_('id', page_ids).execute()
            except Exception as e:
                log_debug(f"Failed to update status to Processing: {e}")

            # Start background thread
            log_debug("Starting background ToFu thread...")
            thread = threading.Thread(target=process_tofu_generation, args=(page_ids, os.environ.get("GEMINI_API_KEY")))
            thread.start()
            
            return jsonify({"message": "ToFu generation started in background. The status will update to 'Processing...' in the table."})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_page_details():
    if not supabase: return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        page_id = request.args.get('page_id')
        if not page_id: return jsonify({"error": "page_id required"}), 400
        
        res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).execute()
        if not res.data: return jsonify({"error": "Page not found"}), 404
        
        return jsonify(res.data[0])
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    except Exception as e:
        print(f"Error in crawl_project: {e}")
        return jsonify({"error": str(e)}), 500







# --- get_pages (L1611-1637) ---
@app.route('/api/get-pages', methods=['GET'])
def get_pages():
    client = supabase_admin or supabase
    if not client:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        project_id = request.args.get('project_id')
        if not project_id:
            return jsonify({"error": "project_id is required"}), 400
        
        # Optimize: Select only necessary columns for the list view
        # We need tech_audit_data for the status/title, but we don't need the full body_content if it's huge.
        # However, Supabase select doesn't support "exclude".
        # Let's select explicit columns.
        response = client.table('pages').select('id, project_id, url, page_type, created_at, tech_audit_data, funnel_stage, source_page_id, content_description, keywords, product_action, research_data, content, seo_analysis').eq('project_id', project_id).order('id').execute()
        
        import sys
        print(f"DEBUG: get_pages for {project_id} found {len(response.data) if response.data else 0} pages.", file=sys.stderr)
        
        # DEBUG: Check data structure
        if response.data:
            print(f"DEBUG: get_pages first row keys: {response.data[0].keys()}", file=sys.stderr)
            print(f"DEBUG: get_pages first row tech_audit_data: {response.data[0].get('tech_audit_data')}", file=sys.stderr)
            
        return jsonify({"pages": response.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- delete_page (L1639-1665) ---
@app.route('/api/delete-page', methods=['DELETE'])
def delete_page():
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    page_id = request.args.get('page_id')
    if not page_id:
        return jsonify({"error": "page_id is required"}), 400
        
    try:
        # Recursive delete function to handle children manually
        def delete_children(pid):
            # Find all children
            children = (supabase_admin or supabase).table('pages').select('id').eq('source_page_id', pid).execute()
            if children.data:
                for child in children.data:
                    delete_children(child['id'])
            
            # Delete the page itself
            (supabase_admin or supabase).table('pages').delete().eq('id', pid).execute()

        delete_children(page_id)
        
        return jsonify({"message": "Page and all children deleted successfully"})
    except Exception as e:
        print(f"Error deleting page: {e}")
        return jsonify({"error": str(e)}), 500


# --- get_page_status (L1667-1687) ---
@app.route('/api/get-page-status', methods=['GET'])
def get_page_status():
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        page_id = request.args.get('page_id')
        if not page_id:
            return jsonify({"error": "page_id is required"}), 400
            
        response = (supabase_admin or supabase).table('pages').select('id, product_action, audit_status').eq('id', page_id).single().execute()
        
        if not response.data:
            return jsonify({"error": "Page not found"}), 404
            
        # Log the status being returned (to debug premature closing)
        print(f"DEBUG: get_page_status for {page_id}: {response.data.get('product_action')}", file=sys.stderr)
        
        return jsonify(response.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- webflow_sites (L6220-6229) ---
@app.route('/api/webflow/sites', methods=['POST'])
def webflow_list_sites():
    try:
        data = request.json
        api_key = data.get('api_key')
        if not api_key: return jsonify({"error": "Missing API Key"}), 400
        sites = webflow_client.list_sites(api_key)
        return jsonify({"sites": sites})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- webflow_collections (L6231-6241) ---
@app.route('/api/webflow/collections', methods=['POST'])
def webflow_list_collections():
    try:
        data = request.json
        api_key = data.get('api_key')
        site_id = data.get('site_id')
        if not api_key or not site_id: return jsonify({"error": "Missing API Key or Site ID"}), 400
        collections = webflow_client.list_collections(api_key, site_id)
        return jsonify({"collections": collections})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- generate_blog_image (L6243-6285) ---
@app.route('/api/generate-blog-image', methods=['POST'])
def generate_blog_image_endpoint():
    data = request.json
    page_id = data.get('page_id')
    custom_prompt = data.get('prompt')
    
    if not page_id: return jsonify({"error": "page_id required"}), 400
    
    try:
        # Fetch page
        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
        if not page_res.data: return jsonify({"error": "Page not found"}), 404
        page = page_res.data
        
        tech_data = page.get('tech_audit_data') or {}
        topic = tech_data.get('title') or page.get('url') or 'Untitled'
        content = page.get('content') or ''
        summary = content[:500] if content else ''
        
        # Generate Prompt if not provided
        if not custom_prompt:
            prompt = generate_image_prompt(topic, summary)
        else:
            prompt = custom_prompt
            
        # Generate Image
        image_url = nano_banana_client.generate_image(prompt)
        
        # Update Page
        (supabase_admin or supabase).table('pages').update({
            'main_image_url': image_url,
            'image_prompt': prompt
        }).eq('id', page_id).execute()
        
        return jsonify({
            "message": "Image generated successfully",
            "image_url": image_url,
            "prompt": prompt
        })
        
    except Exception as e:
        print(f"Error generating blog image: {e}")
        return jsonify({"error": str(e)}), 500


# --- get_html_content (L6287-6397) ---
@app.route('/api/get-html-content', methods=['POST'])
def get_html_content():
    """Get HTML-formatted content for copy-paste (same logic as Webflow publish)"""
    data = request.json
    page_id = data.get('page_id')
    
    if not page_id:
        return jsonify({"error": "Missing page_id"}), 400
        
    try:
        # Fetch page
        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
        if not page_res.data: 
            return jsonify({"error": "Page not found"}), 404
        page = page_res.data
        
        # Get raw markdown content
        content_md = page.get('content', '')
        if not content_md:
            return jsonify({"error": "No content to convert"}), 400
        
        # ==== SAME MARKDOWN PRE-PROCESSING AS WEBFLOW PUBLISH ====
        import re
        
        # 0. Fix space between ] and ( in markdown links
        content_md = re.sub(r'\]\s+\(', '](', content_md)
        content_md = re.sub(r'\*\*\s*\]', '**]', content_md)
        
        # 1. Fix malformed links with asterisks
        def clean_link_text(match):
            link_text = match.group(1)
            url = match.group(2)
            clean_text = link_text.replace('*', '')
            return f'[{clean_text}]({url})'
        content_md = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', clean_link_text, content_md)
        
        # 2. Fix raw URL display after links
        content_md = re.sub(r'\]\(([^)]+)\)\s*\(\1\)', r'](\1)', content_md)
        
        # 3. Fix raw URLs displayed in parentheses after links
        content_md = re.sub(r'\]\(([^)]+)\)\s*\(https?://[^)]+\)', r'](\1)', content_md)
        
        # 4. Ensure headings have proper spacing
        content_md = re.sub(r'([^\n])\n(#{1,6}\s)', r'\1\n\n\2', content_md)
        
        # 5. Fix excessive heading levels
        content_md = re.sub(r'^#{5,}\s', '### ', content_md, flags=re.MULTILINE)
        content_md = re.sub(r'^#{4}\s', '### ', content_md, flags=re.MULTILINE)
        
        # 6. Table formatting: ensure blank lines before/after tables
        lines = content_md.split('\n')
        processed_lines = []
        in_table = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_table_line = stripped.startswith('|') and stripped.endswith('|') and '|' in stripped[1:-1]
            
            if is_table_line and not in_table:
                if processed_lines and processed_lines[-1].strip():
                    processed_lines.append('')
                in_table = True
            elif not is_table_line and in_table:
                if stripped:
                    processed_lines.append('')
                in_table = False
            
            processed_lines.append(line)
        
        content_md = '\n'.join(processed_lines)
        
        # 7. Fix bullet points
        content_md = re.sub(r'^(\s*)\*\s+', r'\1- ', content_md, flags=re.MULTILINE)
        
        # Convert to HTML
        content_html = markdown.markdown(
            content_md, 
            extensions=['tables', 'nl2br', 'fenced_code', 'sane_lists']
        )
        
        # POST-PROCESSING: Fix links appearing on own line
        content_html = re.sub(r'<br\s*/?>\s*(<a\s)', r'\1', content_html)
        content_html = re.sub(r'<br\s*/?>(s*<a\s)', r'\1', content_html)
        content_html = re.sub(r'(</a>)\s*<br\s*/?>', r'\1', content_html)
        content_html = re.sub(r'(</a>)<br\s*/?>\s*', r'\1 ', content_html)
        content_html = re.sub(r'\n\s*(<a\s)', r' \1', content_html)
        content_html = re.sub(r'(</a>)\s*\n', r'\1 ', content_html)
        
        # Force display:inline on all anchor tags
        content_html = re.sub(r'<a href=', r'<a style="display:inline;" href=', content_html)
        
        # Add inline styles for tables
        content_html = content_html.replace(
            '<table>', 
            '<table style="width:100%;border-collapse:collapse;margin:20px 0;">'
        )
        content_html = content_html.replace(
            '<th>', 
            '<th style="border:1px solid #ddd;padding:12px;text-align:left;background-color:#f5f5f5;font-weight:bold;">'
        )
        content_html = content_html.replace(
            '<td>', 
            '<td style="border:1px solid #ddd;padding:12px;text-align:left;">'
        )
        
        return jsonify({"html": content_html, "title": page.get('tech_audit_data', {}).get('title', '')})
        
    except Exception as e:
        print(f"Error getting HTML content: {e}")
        return jsonify({"error": str(e)}), 500



# --- publish_webflow (L6616-6855) ---
@app.route('/api/publish-webflow', methods=['POST'])
def webflow_publish():
    data = request.json
    page_id = data.get('page_id')
    api_key = data.get('api_key')
    collection_id = data.get('collection_id')
    field_mapping = data.get('field_mapping', {}) # { 'wf_field_slug': 'data_key' }
    
    if not all([page_id, api_key, collection_id]):
        return jsonify({"error": "Missing required fields"}), 400
        
    try:
        # Fetch page
        page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
        if not page_res.data: return jsonify({"error": "Page not found"}), 404
        page = page_res.data
        
        # Prepare content
        content_md = page.get('content', '')
        
        # ==== COMPREHENSIVE MARKDOWN PRE-PROCESSING (Ported from seo-saas-brain) ====
        # Fix common Gemini output issues before converting to HTML
        import re
        
        # 0. Fix space between ] and ( in markdown links: [text] (url) -> [text](url)
        content_md = re.sub(r'\]\s+\(', '](', content_md)
        content_md = re.sub(r'\*\*\s*\]', '**]', content_md) # Bold inside link fix sometimes
        
        # 1. Fix malformed links with asterisks: [*text*](url) or [text*](url) -> [text](url)
        # Pattern: Find markdown links and clean asterisks from the link text
        def clean_link_text(match):
            link_text = match.group(1)
            url = match.group(2)
            # Remove asterisks from link text
            clean_text = link_text.replace('*', '')
            return f'[{clean_text}]({url})'
        content_md = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', clean_link_text, content_md)
        
        # 2. Fix raw URL display after links: [text](url) (url) -> [text](url)
        content_md = re.sub(r'\]\(([^)]+)\)\s*\(\1\)', r'](\1)', content_md)
        
        # 3. Fix raw URLs displayed in parentheses after links
        content_md = re.sub(r'\]\(([^)]+)\)\s*\(https?://[^)]+\)', r'](\1)', content_md)
        
        # 4. Ensure headings have proper spacing (add newline before if missing)
        content_md = re.sub(r'([^\n])\n(#{1,6}\s)', r'\1\n\n\2', content_md)
        
        # 5. Fix #### raw heading chars appearing as text
        # Replace multiple # followed by space at start of line with proper H2/H3
        content_md = re.sub(r'^#{5,}\s', '### ', content_md, flags=re.MULTILINE)
        content_md = re.sub(r'^#{4}\s', '### ', content_md, flags=re.MULTILINE)
        
        # 6. Table formatting: ensure blank lines before/after tables
        lines = content_md.split('\n')
        processed_lines = []
        in_table = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_table_line = stripped.startswith('|') and stripped.endswith('|') and '|' in stripped[1:-1]
            
            if is_table_line and not in_table:
                # Starting a table - add blank line before if previous line isn't blank
                if processed_lines and processed_lines[-1].strip():
                    processed_lines.append('')
                in_table = True
            elif not is_table_line and in_table:
                # Ending a table - add blank line after
                if stripped:  # Only add blank if next line isn't already blank
                    processed_lines.append('')
                in_table = False
            
            processed_lines.append(line)
        
        content_md = '\n'.join(processed_lines)
        
        # 7. Ensure proper list formatting (bullet points need consistent spacing)
        # Fix asterisk-as-text becoming bullet: line starting with * followed by space
        content_md = re.sub(r'^(\s*)\*\s+', r'\1- ', content_md, flags=re.MULTILINE)
        
        # Use extensions: tables, nl2br (for line breaks in lists), fenced_code, sane_lists
        content_html = markdown.markdown(
            content_md, 
            extensions=['tables', 'nl2br', 'fenced_code', 'sane_lists']
        )
        
        # POST-PROCESSING: Aggressive fix for links appearing on their own line
        # 1. Remove <br> or <br/> right BEFORE anchor tags
        content_html = re.sub(r'<br\s*/?>\s*(<a\s)', r'\1', content_html)
        content_html = re.sub(r'<br\s*/?>(\s*<a\s)', r'\1', content_html)
        
        # 2. Remove <br> or <br/> right AFTER closing anchor tags  
        content_html = re.sub(r'(</a>)\s*<br\s*/?>', r'\1', content_html)
        content_html = re.sub(r'(</a>)<br\s*/?>\s*', r'\1 ', content_html)
        
        # 3. Remove literal newlines around anchor tags in the HTML itself
        content_html = re.sub(r'\n\s*(<a\s)', r' \1', content_html)
        content_html = re.sub(r'(</a>)\s*\n', r'\1 ', content_html)
        
        # 4. Force display:inline on all anchor tags - Webflow Rich Text may render them as block
        content_html = re.sub(r'<a href=', r'<a style="display:inline;" href=', content_html)
        
        # Add inline styles for tables (Webflow rich text needs inline styles)
        content_html = content_html.replace(
            '<table>', 
            '<table style="width:100%;border-collapse:collapse;margin:20px 0;">'
        )
        content_html = content_html.replace(
            '<th>', 
            '<th style="border:1px solid #ddd;padding:12px;text-align:left;background-color:#f5f5f5;font-weight:bold;">'
        )
        content_html = content_html.replace(
            '<td>', 
            '<td style="border:1px solid #ddd;padding:12px;text-align:left;">'
        )
        
        # Prepare fields
        site_id = data.get('site_id')  # Frontend needs to pass this
        image_wf_field = None
        image_url = None
        
        fields = {}
        for wf_field, data_key in field_mapping.items():
            value = None
            if data_key == 'title':
                value = page.get('tech_audit_data', {}).get('title') or page.get('url')
            elif data_key == 'slug':
                value = page.get('slug')
            elif data_key == 'content':
                value = content_html
            elif data_key == 'meta_description':
                value = page.get('tech_audit_data', {}).get('meta_description')
            elif data_key == 'main_image':
                # Store for later processing - we need to upload the image first
                image_wf_field = wf_field
                image_url = page.get('main_image_url')
                continue  # Don't add to fields yet
            
            if value:
                fields[wf_field] = value
        
        # Handle image upload if present
        if image_url and site_id and image_wf_field:
            try:
                import tempfile
                import requests as req
                
                # Download image from Supabase URL
                print(f"DEBUG: Downloading image from {image_url}", flush=True)
                img_response = req.get(image_url, timeout=30)
                img_response.raise_for_status()
                
                # Save to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                    tmp.write(img_response.content)
                    tmp_path = tmp.name
                
                print(f"DEBUG: Image downloaded to {tmp_path}", flush=True)
                
                # Upload to Webflow
                asset = webflow_client.upload_asset(api_key, site_id, tmp_path)
                
                # Use asset ID (or URL) in the field
                # Webflow v2 API might use 'fileId' or 'url' - check the asset response
                if 'id' in asset:
                    fields[image_wf_field] = asset['id']
                    print(f"DEBUG: Using asset ID: {asset['id']}", flush=True)
                elif 'url' in asset:
                    fields[image_wf_field] = asset['url']
                    print(f"DEBUG: Using asset URL: {asset['url']}", flush=True)
                
                # Clean up temp file
                import os
                os.unlink(tmp_path)
                
            except Exception as img_error:
                print(f"WARNING: Failed to upload image to Webflow: {img_error}", flush=True)
                # Continue without image rather than failing entire publish
                
        # Publish
        with open('debug_payload.json', 'w') as f:
            json.dump(fields, f, indent=2)
        print(f"DEBUG: Webflow Payload: {json.dumps(fields, indent=2)}", flush=True)
        try:
            res = webflow_client.create_item(api_key, collection_id, fields, is_draft=True)
        except Exception as e:
            # Check for 409 Conflict (Slug already exists)
            error_msg = str(e)
            if "409" in error_msg or "Conflict" in error_msg:
                print(f"DEBUG: Slug conflict detected. Searching for existing item to update...", flush=True)
                
                target_slug = fields.get('slug')
                existing_item = None
                
                # Paginate through items to find the conflicting one
                limit = 100
                offset = 0
                max_pages = 20 # Search up to 2000 items (safety limit)
                
                for _ in range(max_pages):
                    print(f"DEBUG: Searching items offset={offset}...", flush=True)
                    items = webflow_client.list_items(api_key, collection_id, limit=limit, offset=offset)
                    
                    if not items:
                        break # End of list
                        
                    found = False
                    for item in items:
                        # Webflow V2: check fieldData.slug
                        if item.get('fieldData', {}).get('slug') == target_slug:
                            existing_item = item
                            found = True
                            break
                    
                    if found:
                        break
                        
                    if len(items) < limit:
                        break # Last page
                        
                    offset += limit

                if existing_item:
                    print(f"DEBUG: Found existing item {existing_item['id']}. Updating to Draft...", flush=True)
                    res = webflow_client.update_item(api_key, collection_id, existing_item['id'], fields, is_draft=True)
                else:
                    raise Exception(f"Conflict detected for slug '{target_slug}' but could not find existing item (checked {offset + limit} items). Please manually delete the item in Webflow or change the slug.")
            else:
                raise e

        
        # Update status
        (supabase_admin or supabase).table('pages').update({'status': 'Published'}).eq('id', page_id).execute()
        
        return jsonify({"message": "Published successfully", "webflow_response": res})
        
    except Exception as e:
        print(f"Error publishing to Webflow: {e}")
        return jsonify({"error": str(e)}), 500





@app.route('/api/debug/sync-supergoop')
def debug_sync_supergoop():
    import traceback
    try:
        from api.dataforseo_client import get_page_issues
        res = supabase.table('projects').select('*').execute()
        pid = None
        for p in res.data:
            if p.get('target_domain') and 'supergoop' in p.get('target_domain').lower():
                pid = p['id']
                break
                
        if not pid: return jsonify({"error": "No Supergoop"}), 404
        
        audits = supabase.table('audits').select('*').eq('project_id', pid).execute()
        audit = audits.data[-1] if audits.data else None
        if not audit: return jsonify({"error": "No audit"}), 404
        
        issues = get_page_issues(audit.get('dataforseo_task_id'), limit=100)
        items = issues.get('items', [])
        
        count = 0
        for page in items:
            url = page.get('url')
            if not url: continue
            tech_audit_data = {"title": page.get('meta', {}).get('title', url)}
            existing = (supabase_admin or supabase).table('pages').select('id').eq('url', url).eq('project_id', pid).execute()
            if not existing.data:
                (supabase_admin or supabase).table('pages').insert({"project_id": pid, "url": url, "page_type": "product", "tech_audit_data": tech_audit_data, "content_description": "Auto-synced from audit"}).execute()
                count += 1
                
        return jsonify({"success": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()})

if __name__ == '__main__':
    print("Starting server...")
    port = int(os.getenv('PORT', 3000))
    print(f"Running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)
