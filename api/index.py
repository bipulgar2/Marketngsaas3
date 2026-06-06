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
    get_domain_rank_overview,
    fetch_ranked_keywords,
    fetch_backlinks_summary,
    get_referring_domains
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
        'name': 'Client',
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

@app.route('/client-portal')
@login_required
def client_portal():
    """Serve the read-only client-facing dashboard."""
    return render_template('client-portal.html')


@app.route('/api/client-portal/summary', methods=['GET'])
@login_required
def get_client_portal_summary():
    """Aggregated read-only summary for client portal.
    
    Returns: campaign info, recent wins, keyword ranking snapshot,
    traffic trend, completed tasks, and upcoming tasks.
    Accessible by viewers (clients) and admins.
    """
    campaign_id = request.args.get('campaign_id')
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400
    
    user = session.get('user', {})
    user_role = user.get('role', 'viewer')
    
    # Scope check for viewers
    if user_role == 'viewer':
        assigned = user.get('assigned_campaigns', [])
        if assigned and campaign_id not in assigned:
            return jsonify({'error': 'Not authorized'}), 403
    
    db = supabase_admin or supabase
    
    try:
        # 1. Campaign info
        camp_res = db.table('campaigns').select('name, domain, settings, created_at').eq('id', campaign_id).single().execute()
        camp = camp_res.data
        if not camp:
            return jsonify({'error': 'Campaign not found'}), 404
        
        # 2. Tracked keywords + ranking snapshot
        tracked_kws = (camp.get('settings') or {}).get('tracked_keywords', [])
        kw_snapshot = []
        for kw in tracked_kws[:20]:
            kw_text = kw if isinstance(kw, str) else kw.get('keyword', '')
            rank = kw.get('rank', None) if isinstance(kw, dict) else None
            prev_rank = kw.get('prev_rank', None) if isinstance(kw, dict) else None
            kw_snapshot.append({
                'keyword': kw_text,
                'rank': rank,
                'prev_rank': prev_rank,
                'change': (prev_rank - rank) if (rank and prev_rank) else None
            })
        
        # 3. Tasks summary
        tasks_res = db.table('tasks').select('id, title, status, priority, due_date').eq('campaign_id', campaign_id).order('created_at', desc=True).limit(50).execute()
        all_tasks = tasks_res.data or []
        completed_tasks = [t for t in all_tasks if t.get('status') == 'done']
        upcoming_tasks = [t for t in all_tasks if t.get('status') in ('todo', 'in_progress')]
        
        # 4. Recent wins (link placements)
        placements_res = db.table('link_placements').select('id, target_url, anchor_text, dr, status, created_at').eq('campaign_id', campaign_id).eq('status', 'live').order('created_at', desc=True).limit(10).execute()
        recent_placements = placements_res.data or []
        
        # 5. Domain metrics from latest audit
        client_data = _collect_domain_data(db, camp['domain'], campaign_id=campaign_id)
        
        # 6. Scorecard
        scorecard = {}
        try:
            both = [{**client_data, 'domain': camp['domain'], 'is_client': True}]
            # Add a fake baseline for normalization
            both.append({**{k: max(v, 1) if isinstance(v, (int, float)) else v for k, v in client_data.items()}, 'domain': 'baseline', 'is_client': False})
            cards = _compute_scorecards(both)
            scorecard = next((s for s in cards if s.get('is_client')), {})
        except:
            pass
        
        return jsonify({
            'success': True,
            'campaign': {
                'name': camp.get('name', ''),
                'domain': camp.get('domain', ''),
                'created_at': camp.get('created_at', '')
            },
            'metrics': {
                'total_traffic': client_data.get('total_traffic', 0),
                'total_keywords': client_data.get('total_keywords', 0),
                'domain_rank': client_data.get('domain_rank', 0),
                'referring_domains': client_data.get('referring_domains', 0),
                'backlinks_total': client_data.get('backlinks_total', 0)
            },
            'scorecard': scorecard,
            'keywords': kw_snapshot,
            'tasks': {
                'completed': len(completed_tasks),
                'upcoming': len(upcoming_tasks),
                'total': len(all_tasks),
                'recent_completed': [{'title': t['title'], 'priority': t.get('priority')} for t in completed_tasks[:5]],
                'upcoming_list': [{'title': t['title'], 'priority': t.get('priority'), 'due_date': t.get('due_date')} for t in upcoming_tasks[:5]]
            },
            'placements': [{
                'url': p.get('target_url', ''),
                'anchor': p.get('anchor_text', ''),
                'dr': p.get('dr', 0),
                'date': p.get('created_at', '')
            } for p in recent_placements]
        })
        
    except Exception as e:
        logger.error(f"Client portal summary error: {e}")
        return jsonify({'error': str(e)}), 500

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
        auth_retries = 3
        for attempt in range(auth_retries):
            try:
                response = supabase.auth.sign_in_with_password({
                    'email': email,
                    'password': password
                })
                break
            except Exception as auth_e:
                if attempt == auth_retries - 1:
                    raise auth_e
                logger.warning(f"Supabase login attempt {attempt+1} failed: {auth_e}. Retrying in 1s...")
                time.sleep(1)
        
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
            'full_name': profile.data.get('full_name') if profile.data else None,
            'assigned_campaigns': profile.data.get('assigned_campaigns', []) if profile.data else []
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
    """Register new user. Optionally consumes an invite token."""
    data = request.json
    email = data.get('email')
    password = data.get('password')
    full_name = data.get('full_name', '')
    invite_token = data.get('invite_token')  # Optional: from invite link
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    admin = supabase_admin or supabase
    invite = None
    
    # If invite token provided, validate it first before creating user
    if invite_token:
        try:
            inv_res = admin.table('invitations').select('*').eq('token', invite_token).eq('used', False).single().execute()
            invite = inv_res.data
            if not invite:
                return jsonify({'error': 'Invalid or expired invitation.'}), 400
        except Exception:
            return jsonify({'error': 'Invalid or expired invitation.'}), 400
    
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
        if invite:
            # ---- INVITED USER: Join existing org with assigned role ----
            try:
                admin.table('profiles').update({
                    'organization_id': invite['organization_id'],
                    'role': invite['role'],
                    'assigned_campaigns': invite.get('assigned_campaigns', []),
                    'full_name': full_name
                }).eq('id', user.id).execute()
                
                # Mark invite as used
                admin.table('invitations').update({'used': True}).eq('id', invite['id']).execute()
                
                logger.info(f"Invited user {email} joined org {invite['organization_id']} as {invite['role']}")
            except Exception as e:
                logger.error(f"Failed to process invite for {email}: {e}")
        else:
            # ---- SELF-SIGNUP: Create new org (existing behavior) ----
            try:
                org_name = f"{full_name}'s Org" if full_name else "My Organization"
                slug = org_name.lower().replace(' ', '-').replace("'", "") + f"-{int(datetime.now().timestamp())}"
                
                org_res = admin.table('organizations').insert({
                    'name': org_name,
                    'slug': slug,
                    'owner_id': user.id
                }).execute()
                
                if org_res.data:
                    org_id = org_res.data[0]['id']
                    admin.table('profiles').update({
                        'organization_id': org_id,
                        'role': 'admin'
                    }).eq('id', user.id).execute()
                    logger.info(f"Created organization {org_id} for new user {user.id}")
                    
            except Exception as e:
                logger.error(f"Failed to auto-create org for {email}: {e}")

        return jsonify({
            'success': True,
            'message': 'Account created! You can now sign in.'
        })
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Signup error: {error_msg}")
        
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
    """Get current user info including role permissions and assigned campaigns."""
    user = session.get('user', {})
    role = user.get('role', 'viewer')
    
    # Define which top-level tabs each role can see
    ROLE_TABS = {
        'admin': ['dashboard', 'audit-2', 'tech-audit', 'content', 'strategy', 'competitors', 'links', 'tasks', 'reports', 'client-settings'],
        'campaign_manager': ['dashboard', 'audit-2', 'tech-audit', 'content', 'strategy', 'competitors', 'links', 'tasks', 'reports', 'client-settings'],
        'content_strategist': ['dashboard', 'content', 'strategy', 'competitors', 'tasks'],
        'content_creator': ['dashboard', 'content', 'tasks'],
        'optimization_specialist': ['dashboard', 'audit-2', 'tech-audit', 'tasks'],
        'link_builder': ['dashboard', 'links', 'tasks'],
        'reporting_manager': ['dashboard', 'reports'],
        'viewer': ['reports']
    }
    
    return jsonify({
        'user': user,
        'role_info': ROLES.get(role, {}),
        'allowed_tabs': ROLE_TABS.get(role, ['dashboard'])
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
# TEAM MANAGEMENT ROUTES
# =============================================================================

@app.route('/api/team', methods=['GET'])
@login_required
@role_required('admin')
def list_team_members():
    """List all team members in the current organization."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'members': []})
    
    client = supabase_admin or supabase
    try:
        res = client.table('profiles').select('id, email, full_name, role, assigned_campaigns, created_at').eq('organization_id', org_id).execute()
        return jsonify({'members': res.data or []})
    except Exception as e:
        logger.error(f"List team error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/team/invite', methods=['POST'])
@login_required
@role_required('admin')
def invite_team_member():
    """Create an invitation for a new team member."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'error': 'No organization found'}), 400
    
    data = request.json
    email = data.get('email', '').strip()
    role = data.get('role', 'viewer')
    assigned_campaigns = data.get('assigned_campaigns', [])
    
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    
    if role not in ROLES:
        return jsonify({'error': f'Invalid role: {role}'}), 400
    
    client = supabase_admin or supabase
    try:
        # Check if an unused invite already exists for this email
        existing = client.table('invitations').select('id, token').eq('email', email).eq('organization_id', org_id).eq('used', False).execute()
        if existing.data:
            # Return the existing invite link
            token = existing.data[0]['token']
            return jsonify({
                'success': True,
                'invite_token': token,
                'message': f'Existing invite found for {email}'
            })
        
        # Create new invitation
        res = client.table('invitations').insert({
            'email': email,
            'organization_id': org_id,
            'role': role,
            'assigned_campaigns': assigned_campaigns
        }).execute()
        
        if res.data:
            token = res.data[0]['token']
            logger.info(f"Created invite for {email} as {role} in org {org_id}")
            return jsonify({
                'success': True,
                'invite_token': token,
                'message': f'Invitation created for {email}'
            })
        else:
            return jsonify({'error': 'Failed to create invitation'}), 500
    except Exception as e:
        logger.error(f"Invite error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/team/invitations', methods=['GET'])
@login_required
@role_required('admin')
def list_invitations():
    """List all invitations for the current organization."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'invitations': []})
    
    client = supabase_admin or supabase
    try:
        res = client.table('invitations').select('*').eq('organization_id', org_id).order('created_at', desc=True).execute()
        return jsonify({'invitations': res.data or []})
    except Exception as e:
        logger.error(f"List invitations error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/team/invitations/<token>', methods=['DELETE'])
@login_required
@role_required('admin')
def delete_invitation(token):
    """Delete a pending invitation."""
    user = session['user']
    org_id = user.get('organization_id')
    client = supabase_admin or supabase
    
    try:
        # Verify the invitation belongs to the user's organization
        existing = client.table('invitations').select('*').eq('token', token).eq('organization_id', org_id).execute()
        if not existing.data:
            return jsonify({'error': 'Invitation not found or unauthorized'}), 404
            
        res = client.table('invitations').delete().eq('token', token).execute()
        return jsonify({'success': True, 'message': 'Invitation deleted'})
    except Exception as e:
        logger.error(f"Delete invitation error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/team/<member_id>', methods=['PUT'])
@login_required
@role_required('admin')
def update_team_member(member_id):
    """Update a team member's role or assigned campaigns."""
    user = session['user']
    org_id = user.get('organization_id')
    
    # Cannot change your own role/org (prevents self-lockout)
    if str(user.get('id')) == str(member_id):
        return jsonify({'error': 'Cannot modify your own account via this endpoint'}), 400
        
    data = request.json
    role = data.get('role')
    assigned_campaigns = data.get('assigned_campaigns')
    
    update_data = {}
    if role:
        if role not in ROLES:
            return jsonify({'error': f'Invalid role: {role}'}), 400
        update_data['role'] = role
    if assigned_campaigns is not None:
        update_data['assigned_campaigns'] = assigned_campaigns
        
    if not update_data:
        return jsonify({'error': 'No fields to update'}), 400
        
    client = supabase_admin or supabase
    try:
        # Verify the member belongs to the same org
        member = client.table('profiles').select('*').eq('id', member_id).eq('organization_id', org_id).execute()
        if not member.data:
            return jsonify({'error': 'Member not found or unauthorized'}), 404
            
        res = client.table('profiles').update(update_data).eq('id', member_id).execute()
        return jsonify({'success': True, 'member': res.data[0] if res.data else None})
    except Exception as e:
        logger.error(f"Update team member error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/team/<member_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def remove_team_member(member_id):
    """Remove a team member from the organization."""
    user = session['user']
    org_id = user.get('organization_id')
    
    # Cannot delete yourself
    if str(user.get('id')) == str(member_id):
        return jsonify({'error': 'Cannot remove your own account'}), 400
        
    client = supabase_admin or supabase
    try:
        # Verify the member belongs to the same org
        member = client.table('profiles').select('*').eq('id', member_id).eq('organization_id', org_id).execute()
        if not member.data:
            return jsonify({'error': 'Member not found or unauthorized'}), 404
            
        # To "remove" a member, we disconnect them from the org
        # Depending on auth setup, might just clear their org_id and role
        res = client.table('profiles').update({
            'organization_id': None,
            'role': 'viewer',
            'assigned_campaigns': []
        }).eq('id', member_id).execute()
        
        return jsonify({'success': True, 'message': 'Member removed successfully'})
    except Exception as e:
        logger.error(f"Remove team member error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# CAMPAIGN ROUTES
# =============================================================================

@app.route('/api/campaigns', methods=['GET'])
@login_required
def list_campaigns():
    """List campaigns visible to user. Non-admin roles only see assigned campaigns."""
    user = session['user']
    
    # Use admin client to bypass RLS (backend handles authorization)
    client = supabase_admin or supabase
    
    try:
        query = client.table('campaigns').select('*')
        
        # Filter by organization for EVERYONE
        if user.get('organization_id'):
            query = query.eq('organization_id', user['organization_id'])
        else:
            return jsonify({'campaigns': []})
        
        response = query.order('created_at', desc=True).execute()
        campaigns = response.data or []
        
        # For non-admin roles, filter to only assigned campaigns
        user_role = user.get('role', 'viewer')
        
        # Fresh fetch of assigned campaigns to prevent stale session issues
        profile_res = client.table('profiles').select('assigned_campaigns').eq('id', user['id']).execute()
        assigned = profile_res.data[0].get('assigned_campaigns', []) if profile_res.data else []
        
        if user_role == 'admin':
            pass  # Full visibility — see all org campaigns
        elif assigned:
            campaigns = [c for c in campaigns if str(c['id']) in assigned]
        else:
            # Non-admin role with no assignments sees nothing
            campaigns = []
        
        return jsonify({'campaigns': campaigns})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/campaigns', methods=['POST'])
@login_required
@role_required('admin')
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

@app.route('/api/campaigns/<campaign_id>', methods=['DELETE'])
@login_required
@permission_required('view_all_campaigns')
def delete_campaign(campaign_id):
    """Delete a campaign."""
    client = supabase_admin or supabase
    try:
        # Note: Depending on Supabase cascade rules, deleting the campaign might automatically
        # delete related audits, tasks, and data. If not set, might need explicit cleanup.
        response = client.table('campaigns').delete().eq('id', campaign_id).execute()
        return jsonify({'success': True, 'message': 'Campaign deleted successfully'})
    except Exception as e:
        logger.error(f"Delete campaign error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# TRACKED KEYWORDS ROUTES
# =============================================================================

@app.route('/api/campaigns/<campaign_id>/keywords', methods=['GET', 'POST', 'DELETE'])
@login_required
def manage_tracked_keywords(campaign_id):
    """Get or update tracked keywords for a campaign."""
    client = supabase_admin or supabase
    try:
        if request.method == 'GET':
            response = client.table('campaigns').select('tracked_keywords').eq('id', campaign_id).single().execute()
            if not response.data:
                return jsonify({'keywords': []})
            return jsonify({'keywords': response.data.get('tracked_keywords') or []})
            
        elif request.method == 'POST':
            data = request.json
            new_keywords = data.get('keywords', [])
            
            # Make sure it's a list of strings
            new_keywords = [str(k) for k in new_keywords]
            
            # MERGE: Fetch existing tracked keywords first, then add new ones
            existing_res = client.table('campaigns').select('tracked_keywords').eq('id', campaign_id).single().execute()
            existing_keywords = (existing_res.data.get('tracked_keywords') or []) if existing_res.data else []
            
            # Union: existing + new, deduplicated, preserving order
            merged = list(existing_keywords)
            for kw in new_keywords:
                if kw not in merged:
                    merged.append(kw)
            
            response = client.table('campaigns').update({'tracked_keywords': merged}).eq('id', campaign_id).execute()
            out_keywords = response.data[0].get('tracked_keywords', merged) if hasattr(response, 'data') and response.data else merged
            return jsonify({'success': True, 'keywords': out_keywords})
            
        elif request.method == 'DELETE':
            data = request.json
            keywords_to_remove = data.get('keywords', [])
            
            # Fetch existing tracked keywords
            existing_res = client.table('campaigns').select('tracked_keywords').eq('id', campaign_id).single().execute()
            existing_keywords = (existing_res.data.get('tracked_keywords') or []) if existing_res.data else []
            
            # Remove specified keywords
            updated = [kw for kw in existing_keywords if kw not in keywords_to_remove]
            
            response = client.table('campaigns').update({'tracked_keywords': updated}).eq('id', campaign_id).execute()
            out_keywords = response.data[0].get('tracked_keywords', updated) if hasattr(response, 'data') and response.data else updated
            return jsonify({'success': True, 'keywords': out_keywords})
            
    except Exception as e:
        logger.error(f"Tracked Keywords Error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# BRAND CONFIG ROUTES
# =============================================================================

@app.route('/api/campaigns/<campaign_id>/brand-config', methods=['GET', 'POST'])
@login_required
def manage_brand_config(campaign_id):
    """Get or update brand configuration for a campaign."""
    client = supabase_admin or supabase
    try:
        if request.method == 'GET':
            response = client.table('campaigns').select('brand_config').eq('id', campaign_id).single().execute()
            if not response.data:
                return jsonify({'brand_config': {}})
            return jsonify({'brand_config': response.data.get('brand_config') or {}})
            
        elif request.method == 'POST':
            data = request.json
            brand_config = data.get('brand_config', {})
            
            response = client.table('campaigns').update({'brand_config': brand_config}).eq('id', campaign_id).execute()
            out = response.data[0].get('brand_config', brand_config) if hasattr(response, 'data') and response.data else brand_config
            return jsonify({'success': True, 'brand_config': out})
            
    except Exception as e:
        logger.error(f"Brand Config Error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# KEYWORD RESEARCH ROUTES
# =============================================================================

@app.route('/api/keyword-research', methods=['POST'])
@login_required
def keyword_research():
    """Run keyword research using DataForSEO Related Keywords API."""
    try:
        from api.dataforseo_client import keyword_suggestions, location_code_for
        data = request.json or {}
        seed_keyword = data.get('seed_keyword', '').strip()
        country = data.get('country', 'US')
        language = data.get('language', 'en')
        limit = min(int(data.get('limit', 500)), 700)
        
        if not seed_keyword:
            return jsonify({'error': 'seed_keyword is required'}), 400
        
        loc_code = location_code_for(country)
        result = keyword_suggestions(seed_keyword, location_code=loc_code, language_code=language, limit=limit)
        
        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify({'error': result.get('error', 'Unknown error')}), 500
            
    except Exception as e:
        logger.error(f"Keyword Research Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# TOPIC CLUSTERS ROUTES
# =============================================================================

@app.route('/api/generate-topic-clusters', methods=['POST'])
@login_required
def generate_topic_clusters():
    """Use Gemini to semantically cluster keywords into topic silos."""
    try:
        data = request.json or {}
        keywords = data.get('keywords', [])
        brand_config = data.get('brand_config', {})
        
        if not keywords or len(keywords) < 3:
            return jsonify({'error': 'At least 3 keywords required'}), 400
        
        # Build context from brand config
        brand_context = ""
        if brand_config:
            if brand_config.get('business_name'):
                brand_context += f"Business: {brand_config['business_name']}\n"
            if brand_config.get('industry'):
                brand_context += f"Industry: {brand_config['industry']}\n"
            if brand_config.get('usp'):
                brand_context += f"USP: {brand_config['usp']}\n"
            if brand_config.get('primary_audience'):
                brand_context += f"Target Audience: {brand_config['primary_audience']}\n"
            if brand_config.get('linking_strategy'):
                brand_context += f"Linking Strategy: {brand_config['linking_strategy']}\n"
        
        # Cap at 200 keywords for prompt length
        kw_list = keywords[:200]
        kw_str = "\n".join([f"- {kw}" for kw in kw_list])
        
        prompt = f"""You are an expert SEO content strategist. Analyze these keywords and organize them into topic clusters (content silos).

{f"BRAND CONTEXT:{chr(10)}{brand_context}" if brand_context else ""}

KEYWORDS:
{kw_str}

Create topic clusters following these rules:
1. Each cluster has ONE pillar keyword (broadest/highest volume) and supporting keywords
2. Group semantically related keywords together
3. Assign each cluster a funnel stage: "tofu" (awareness), "mofu" (consideration), or "bofu" (decision)
4. Suggest a pillar page title and URL slug for each cluster
5. Create 3-8 clusters maximum (merge small groups)

Respond ONLY with valid JSON in this exact format, no markdown:
{{
  "clusters": [
    {{
      "cluster_name": "Descriptive Cluster Name",
      "pillar_keyword": "main keyword phrase",
      "pillar_title": "Suggested Pillar Page Title",
      "pillar_slug": "url-slug-for-pillar",
      "funnel_stage": "tofu|mofu|bofu",
      "supporting_keywords": ["keyword 1", "keyword 2", "keyword 3"],
      "content_angle": "Brief description of what content in this cluster should cover"
    }}
  ]
}}"""

        result = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=True
        )
        
        if not result:
            return jsonify({'error': 'Empty response from AI'}), 500
        
        # Clean markdown
        text = result.strip()
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)
            
        import json
        parsed = json.loads(text)
        clusters_data = parsed.get('clusters', [])
        
        # Persist to campaign settings
        campaign_id = data.get('campaign_id')
        if campaign_id:
            try:
                db = supabase_admin or supabase
                camp_res = db.table('campaigns').select('settings').eq('id', campaign_id).single().execute()
                settings = camp_res.data.get('settings', {}) if camp_res.data else {}
                settings['topic_clusters'] = clusters_data
                db.table('campaigns').update({'settings': settings}).eq('id', campaign_id).execute()
            except Exception as e:
                logger.error(f"Error saving topic clusters: {e}")
        
        return jsonify({'success': True, 'clusters': clusters_data})
        
    except json.JSONDecodeError as e:
        logger.error(f"Topic Clusters JSON Parse Error: {e}\nRaw: {text[:500]}")
        return jsonify({'error': f'AI returned invalid JSON: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Topic Clusters Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# AI BRIEFS ROUTES
# =============================================================================

@app.route('/api/generate-content-brief', methods=['POST'])
@login_required
def generate_content_brief():
    """Generate an AI content brief using Gemini."""
    try:
        data = request.json or {}
        keyword = data.get('keyword', '').strip()
        funnel_stage = data.get('funnel_stage', 'mofu')
        brand_config = data.get('brand_config', {})
        cluster_context = data.get('cluster_context', '')
        
        if not keyword:
            return jsonify({'error': 'keyword is required'}), 400
        
        # Build brand context
        brand_ctx = ""
        if brand_config:
            parts = []
            if brand_config.get('business_name'): parts.append(f"Business: {brand_config['business_name']}")
            if brand_config.get('industry'): parts.append(f"Industry: {brand_config['industry']}")
            if brand_config.get('usp'): parts.append(f"USP: {brand_config['usp']}")
            if brand_config.get('primary_audience'): parts.append(f"Audience: {brand_config['primary_audience']}")
            if brand_config.get('voice_style'): parts.append(f"Voice: {brand_config['voice_style']}")
            if brand_config.get('perspective'): parts.append(f"Perspective: {brand_config['perspective']}")
            if brand_config.get('tone_notes'): parts.append(f"Tone: {brand_config['tone_notes']}")
            if brand_config.get('content_length'): parts.append(f"Length preference: {brand_config['content_length']}")
            if brand_config.get('ai_notes'): parts.append(f"Notes: {brand_config['ai_notes']}")
            brand_ctx = "\n".join(parts)
        
        funnel_desc = {
            'tofu': 'Top of Funnel (Awareness) — educational, informational, attracts new visitors',
            'mofu': 'Middle of Funnel (Consideration) — comparative, builds trust, shows expertise',
            'bofu': 'Bottom of Funnel (Decision) — conversion-focused, product/service specific'
        }.get(funnel_stage, 'Middle of Funnel')
        
        prompt = f"""You are a senior SEO content strategist. Generate a comprehensive content brief for the following keyword.

TARGET KEYWORD: {keyword}
FUNNEL STAGE: {funnel_desc}
{f"CLUSTER CONTEXT: {cluster_context}" if cluster_context else ""}
{f"BRAND CONTEXT:{chr(10)}{brand_ctx}" if brand_ctx else ""}

Create a detailed content brief. Respond ONLY with valid JSON, no markdown:
{{
  "title": "Recommended article/page title (SEO optimized, under 60 chars)",
  "meta_description": "Meta description (under 155 chars, includes keyword)",
  "target_keyword": "{keyword}",
  "secondary_keywords": ["4-6 secondary/LSI keywords to include"],
  "search_intent": "informational|commercial|transactional|navigational",
  "funnel_stage": "{funnel_stage}",
  "recommended_word_count": 1500,
  "recommended_format": "Guide|Listicle|How-To|Comparison|Review|Landing Page",
  "outline": [
    {{
      "heading": "H2 heading text",
      "subheadings": ["H3 subheading 1", "H3 subheading 2"],
      "key_points": ["Main point to cover", "Another point"],
      "word_count": 300
    }}
  ],
  "competitor_angles": ["What top-ranking pages typically cover — 3 bullet points"],
  "content_gap": "What competitors miss that this content should include",
  "internal_linking": ["Suggested pages to link to/from based on topic"],
  "cta_recommendation": "What CTA to use and where to place it",
  "visual_suggestions": ["Types of images/charts/infographics to include"],
  "seo_notes": "Any specific SEO recommendations for this piece"
}}"""

        result = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=True
        )
        
        if not result:
            return jsonify({'error': 'Empty response from AI'}), 500
        
        text = result.strip()
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)
            
        import json
        parsed = json.loads(text)
        
        # Persist brief to content_pieces
        db = supabase_admin or supabase
        campaign_id = data.get('campaign_id')
        user = session.get('user', {})
        
        if campaign_id:
            try:
                piece = {
                    'campaign_id': campaign_id,
                    'title': parsed.get('title', keyword),
                    'target_keyword': keyword,
                    'funnel_stage': funnel_stage,
                    'content_type': 'blog_post',
                    'brief': parsed,
                    'outline': parsed.get('outline', []),
                    'status': 'brief',
                    'assigned_by': user.get('id')
                }
                db.table('content_pieces').insert(piece).execute()
            except Exception as e:
                logger.error(f"Error saving brief to DB: {e}")
                
        return jsonify({'success': True, 'brief': parsed})
        
    except json.JSONDecodeError as e:
        logger.error(f"AI Brief JSON Parse Error: {e}")
        return jsonify({'error': f'AI returned invalid JSON: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"AI Brief Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# GUEST POST TOPICS ROUTES
# =============================================================================

@app.route('/api/generate-guest-post-topics', methods=['POST'])
@login_required
def generate_guest_post_topics():
    """Use Gemini to identify guest post topics and target sites."""
    try:
        data = request.json or {}
        brand_config = data.get('brand_config', {})
        tracked_keywords = data.get('tracked_keywords', [])
        clusters = data.get('clusters', [])
        
        # Build context
        brand_ctx = ""
        if brand_config:
            parts = []
            if brand_config.get('business_name'): parts.append(f"Business: {brand_config['business_name']}")
            if brand_config.get('industry'): parts.append(f"Industry: {brand_config['industry']}")
            if brand_config.get('usp'): parts.append(f"USP: {brand_config['usp']}")
            if brand_config.get('primary_audience'): parts.append(f"Audience: {brand_config['primary_audience']}")
            if brand_config.get('domain'): parts.append(f"Website: {brand_config['domain']}")
            if brand_config.get('linking_strategy'): parts.append(f"Link Strategy: {brand_config['linking_strategy']}")
            brand_ctx = "\n".join(parts)
        
        kw_str = ""
        if tracked_keywords:
            kw_str = "\n".join([f"- {kw}" for kw in tracked_keywords[:30]])
        
        cluster_str = ""
        if clusters:
            cluster_str = "\n".join([f"- {c.get('cluster_name', '')} (Pillar: {c.get('pillar_keyword', '')})" for c in clusters[:8]])
        
        prompt = f"""You are an expert link building strategist. Generate guest post topic ideas for this business.

{f"BRAND:{chr(10)}{brand_ctx}" if brand_ctx else ""}
{f"TARGET KEYWORDS:{chr(10)}{kw_str}" if kw_str else ""}
{f"TOPIC CLUSTERS:{chr(10)}{cluster_str}" if cluster_str else ""}

Generate 8-12 guest post topic ideas. For each, suggest:
1. A compelling article title that would be accepted by editors
2. The type of website to pitch it to
3. A brief pitch angle (why they'd publish it)
4. The anchor text to use for a backlink
5. Which internal page it should link to
6. Difficulty level (easy/medium/hard)

Respond ONLY with valid JSON, no markdown:
{{
  "topics": [
    {{
      "title": "Guest post article title",
      "target_site_type": "Type of publication/blog to pitch (e.g. Marketing blog, Industry news site, Local business directory)",
      "example_sites": ["example-site1.com", "example-site2.com"],
      "pitch_angle": "Why this site would want to publish this (1-2 sentences)",
      "anchor_text": "Exact anchor text for backlink",
      "target_page": "Which page on their site this should link to",
      "funnel_stage": "tofu|mofu|bofu",
      "difficulty": "easy|medium|hard",
      "content_type": "How-To|Opinion|Data Study|Case Study|Expert Roundup|Listicle"
    }}
  ]
}}"""

        result = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=True
        )
        
        if not result:
            return jsonify({'error': 'Empty response from AI'}), 500
        
        text = result.strip()
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)
            
        import json
        parsed = json.loads(text)
        topics_data = parsed.get('topics', [])
        
        # Persist to campaign settings
        campaign_id = data.get('campaign_id')
        if campaign_id:
            try:
                db = supabase_admin or supabase
                camp_res = db.table('campaigns').select('settings').eq('id', campaign_id).single().execute()
                settings = camp_res.data.get('settings', {}) if camp_res.data else {}
                settings['guest_posts'] = topics_data
                db.table('campaigns').update({'settings': settings}).eq('id', campaign_id).execute()
            except Exception as e:
                logger.error(f"Error saving guest posts: {e}")
                
        return jsonify({'success': True, 'topics': topics_data})
        
    except json.JSONDecodeError as e:
        logger.error(f"Guest Post JSON Parse Error: {e}")
        return jsonify({'error': f'AI returned invalid JSON: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Guest Post Topics Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# SITE ARCHITECTURE PLANNER
# =============================================================================

@app.route('/api/site-architecture', methods=['GET'])
@login_required
def get_site_architecture():
    """Load saved site architecture for a campaign."""
    campaign_id = request.args.get('campaign_id')
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400
    client = supabase_admin or supabase
    try:
        res = client.table('campaigns').select('site_architecture').eq('id', campaign_id).single().execute()
        return jsonify({'success': True, 'architecture': res.data.get('site_architecture')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/site-architecture', methods=['POST'])
@login_required
def save_site_architecture():
    """Save site architecture for a campaign."""
    data = request.get_json()
    campaign_id = data.get('campaign_id')
    architecture = data.get('architecture')
    if not campaign_id or architecture is None:
        return jsonify({'error': 'campaign_id and architecture required'}), 400
    client = supabase_admin or supabase
    try:
        client.table('campaigns').update({'site_architecture': architecture}).eq('id', campaign_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate-site-architecture', methods=['POST'])
@login_required
def generate_site_architecture():
    """Use Gemini to generate a recommended site architecture based on real audit data + business type."""
    data = request.get_json()
    business_type = data.get('business_type', 'saas')
    brand_config = data.get('brand_config', {})
    domain = data.get('domain', '')
    campaign_id = data.get('campaign_id', '')
    brand_name = brand_config.get('brand_name', domain.replace('www.', '').split('.')[0].title() if domain else 'Brand')

    # === STEP 1: Pull existing pages from the latest audit ===
    existing_pages = []
    client = supabase_admin or supabase
    if campaign_id:
        try:
            audit_res = client.table('technical_audits').select('results').eq('campaign_id', campaign_id).order('created_at', desc=True).limit(1).execute()
            if audit_res.data and audit_res.data[0].get('results'):
                results = audit_res.data[0]['results']
                pages = results.get('pages', [])
                
                # Extract all URLs/pages from the audit results
                for page in pages:
                    url = page.get('url')
                    if url and isinstance(url, str):
                        # Normalize: strip domain prefix to get relative paths
                        url = url.strip()
                        if url.startswith('http'):
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            path = parsed.path or '/'
                        else:
                            path = url
                        if path and path not in existing_pages:
                            existing_pages.append(path)
        except Exception as e:
            print(f"DEBUG: Could not fetch audit pages: {e}", flush=True)

    existing_nodes = _paths_to_existing_nodes(existing_pages)
    import json
    existing_nodes_json = json.dumps(existing_nodes, indent=2)

    prompt = f"""You are an expert SEO site architect. 
Brand: {brand_name}
Domain: {domain}
Industry: {brand_config.get('industry', business_type)}
Target audience: {brand_config.get('target_audience', 'general')}
Business type: {business_type}

=== CURRENT SITE ARCHITECTURE (Auto-Mapped) ===
{existing_nodes_json}

=== YOUR MISSION ===
Analyze the CURRENT SITE ARCHITECTURE above and identify 15-30 missing, high-value SEO pages (or folders) that this business urgently needs.
You must return a JSON array containing ONLY the NEW recommended nodes to snap into the existing tree.

Rules for your NEW nodes:
1. "id": generate a unique string (e.g., "rec_node_1", "rec_node_2")
2. "name": page/section name
3. "type": "folder" or "page"
4. "slug": URL path (e.g. "/new-topic/sub-page")
5. "keyword": primary target keyword
6. "pr": PageRank priority (folders=15-25, pages=2-10)
7. "parent_id": VERY IMPORTANT! If a new page belongs under an existing folder, look at the CURRENT SITE ARCHITECTURE JSON and use its exact "id" as the parent_id. If a new page is top-level, set parent_id to "node_root".
8. "order": sort order (0, 1, 2)
9. "status": MUST be "recommended".

DO NOT RETURN ANY EXISTING NODES IN YOUR OUTPUT. Return ONLY the JSON array of your new recommendations. No markdown formatting, just raw JSON array."""

    try:
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return jsonify({'error': 'GEMINI_API_KEY not configured'}), 500

        text = generate_content_via_rest(prompt, api_key, model="gemini-2.5-flash", use_grounding=True)
        if not text:
            return jsonify({'error': 'Gemini returned empty response'}), 500

        text = text.strip()

        import re

        # Robustly extract JSON array if markdown or conversational text is present
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            text = match.group(0)
        else:
            # Fallback markdown un-fencing
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            if text.startswith('json'):
                text = text[4:]
                
        text = text.strip()
        
        try:
            if text and text != '[]':
                new_nodes = json.loads(text)
            else:
                new_nodes = []
            
            # STITCH NATIVE PAGES + NEW RECOMMENDATIONS
            nodes = existing_nodes + new_nodes
            
        except json.JSONDecodeError as e:
            print(f"DEBUG: JSON Parse Error. Raw: {text[:200]}")
            return jsonify({'error': f'AI returned invalid JSON: {str(e)}'}), 500

        # Build the architecture object
        architecture = {
            'nodes': nodes,
            'business_type': business_type,
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'existing_pages_count': len(existing_pages),
            'meta': {
                'total_nodes': len(nodes),
                'max_depth': _calc_max_depth(nodes)
            }
        }

        return jsonify({'success': True, 'architecture': architecture})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def _paths_to_existing_nodes(paths):
    """Natively convert an array of url paths into JSON architecture nodes."""
    nodes = []
    # Base root node
    nodes.append({
        "id": "node_root",
        "name": "Homepage",
        "type": "folder",
        "slug": "/",
        "keyword": "home",
        "pr": 100,
        "parent_id": None,
        "order": 0,
        "status": "existing"
    })
    
    # Filter valid unique paths
    clean_paths = sorted(list(set([p for p in paths if p and p != '/' and not p.startswith('#')])))
    
    slug_to_id = {"/": "node_root"}
    node_counter = 1
    
    for path in clean_paths:
        parts = [p for p in path.strip('/').split('/') if p]
        if not parts:
            continue
            
        current_slug = ""
        parent_id = "node_root"
        
        for i, part in enumerate(parts):
            current_slug += f"/{part}"
            if current_slug not in slug_to_id:
                node_id = f"node_ex_{node_counter}"
                node_counter += 1
                slug_to_id[current_slug] = node_id
                
                is_leaf = (i == len(parts) - 1)
                name = part.replace("-", " ").replace("_", " ").title()
                
                nodes.append({
                    "id": node_id,
                    "name": name,
                    "type": "page" if is_leaf else "folder",
                    "slug": current_slug,
                    "keyword": name.lower(),
                    "pr": 5 if is_leaf else 15,
                    "parent_id": parent_id,
                    "order": 0,
                    "status": "existing"
                })
            parent_id = slug_to_id[current_slug]
            
    return nodes

def _calc_max_depth(nodes):
    """Calculate max depth of the node tree."""
    node_map = {n['id']: n for n in nodes}
    def depth(node_id, d=0):
        node = node_map.get(node_id)
        if not node:
            return d
        parent = node.get('parent_id')
        if parent and parent in node_map:
            return depth(parent, d + 1)
        return d
    return max((depth(n['id']) for n in nodes), default=0) + 1

@app.route('/api/site-architecture/push-to-content', methods=['POST'])
@login_required
def push_architecture_to_content():
    """Push selected architecture nodes to the content strategy as topics."""
    data = request.get_json()
    campaign_id = data.get('campaign_id')
    nodes = data.get('nodes', [])

    if not campaign_id or not nodes:
        return jsonify({'error': 'campaign_id and nodes required'}), 400

    client = supabase_admin or supabase
    created = 0
    try:
        for node in nodes:
            # Create a content_calendar item for each node
            item = {
                'campaign_id': campaign_id,
                'title': node.get('name', ''),
                'target_keyword': node.get('keyword', ''),
                'status': 'planned',
                'brief': {
                    'slug': node.get('slug', ''),
                    'content_type': 'page' if node.get('type') == 'page' else 'pillar',
                    'notes': f"From site architecture. PR weight: {node.get('pr', 0)}"
                },
                'created_at': datetime.utcnow().isoformat()
            }
            client.table('content_calendar').insert(item).execute()
            created += 1

        return jsonify({'success': True, 'created': created})
    except Exception as e:
        return jsonify({'error': str(e), 'created': created}), 500

# =============================================================================
# LINK MARKETPLACE (Linkmanagement.net Portal Integration)
# =============================================================================

@app.route('/api/link-marketplace/inventory', methods=['GET'])
@login_required
def get_link_inventory():
    """Fetch available inventory with filtering"""
    niche = request.args.get('niche')
    min_da = request.args.get('min_da', type=int)
    
    client = supabase_admin or supabase
    query = client.table('link_inventory').select('*').eq('is_active', True)
    
    if niche and niche != 'All':
        query = query.eq('niche', niche)
    if min_da:
        query = query.gte('da', min_da)
        
    try:
        res = query.order('da', desc=True).execute()
        # Obfuscate domains slightly for unpurchased links as per standard practice (e.g. tech***.com)
        items = res.data
        for item in items:
            domain_parts = item['domain'].split('.')
            if len(domain_parts) >= 2:
                name, tld = domain_parts[0], domain_parts[-1]
                if len(name) > 4:
                    item['display_domain'] = f"{name[:4]}****.{tld}"
                else:
                    item['display_domain'] = f"{name[0]}***.{tld}"
            else:
                item['display_domain'] = "Private Domain"
                
        return jsonify({'success': True, 'inventory': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/link-marketplace/checkout', methods=['POST'])
@login_required
def process_link_checkout():
    """Process a shopping cart of links"""
    data = request.get_json()
    campaign_id = data.get('campaign_id')
    cart_items = data.get('items', [])
    
    if not campaign_id or not cart_items:
        return jsonify({'error': 'Missing campaign or cart items'}), 400
        
    client = supabase_admin or supabase
    try:
        total = sum(float(item.get('price', 0)) for item in cart_items)
        
        # Create order
        order_res = client.table('link_orders').insert({
            'campaign_id': campaign_id,
            'total_amount': total,
            'status': 'processing'
        }).execute()
        
        order_id = order_res.data[0]['id']
        
        # Create order items
        for item in cart_items:
            client.table('link_order_items').insert({
                'order_id': order_id,
                'link_id': item.get('id'),
                'target_url': item.get('target_url', ''),
                'anchor_text': item.get('anchor_text', ''),
                'price': float(item.get('price', 0))
            }).execute()
            
        return jsonify({'success': True, 'order_id': order_id, 'message': 'Order successfully placed. Check placements tab.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# LINK PLACEMENTS
# =============================================================================

@app.route('/api/link-placements', methods=['GET'])
@login_required
def get_link_placements():
    """Fetch all link orders and their items for a campaign."""
    campaign_id = request.args.get('campaign_id')
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400

    client = supabase_admin or supabase
    try:
        # Fetch orders for this campaign
        orders_res = client.table('link_orders').select('*').eq('campaign_id', campaign_id).order('created_at', desc=True).execute()
        orders = orders_res.data or []

        # Fetch all order items with link info
        result = []
        for order in orders:
            items_res = client.table('link_order_items').select('*, link_inventory(domain, da, niche)').eq('order_id', order['id']).execute()
            items = items_res.data or []
            result.append({
                'order_id': order['id'],
                'total_amount': float(order.get('total_amount', 0)),
                'status': order.get('status', 'pending'),
                'created_at': order.get('created_at', ''),
                'items': [{
                    'id': it['id'],
                    'domain': (it.get('link_inventory') or {}).get('domain', 'Unknown'),
                    'da': (it.get('link_inventory') or {}).get('da', 0),
                    'niche': (it.get('link_inventory') or {}).get('niche', ''),
                    'target_url': it.get('target_url', ''),
                    'anchor_text': it.get('anchor_text', ''),
                    'price': float(it.get('price', 0))
                } for it in items]
            })

        # Summary stats
        total_links = sum(len(o['items']) for o in result)
        total_spent = sum(o['total_amount'] for o in result)
        active_orders = sum(1 for o in result if o['status'] in ('processing', 'in_progress'))
        completed = sum(1 for o in result if o['status'] == 'completed')

        return jsonify({
            'success': True,
            'orders': result,
            'stats': {
                'total_links': total_links,
                'total_spent': total_spent,
                'active_orders': active_orders,
                'completed_orders': completed,
                'total_orders': len(result)
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# SCHEMA MARKUP SYSTEM — Auto-Assign, Questionnaire, Bulk Generation
# =============================================================================

def _detect_page_type(url):
    """Detect page type from URL patterns. Returns (page_type, schema_types)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.lower().strip('/')
    
    # Skip pages (no schema needed)
    skip_patterns = ['privacy', 'terms', 'legal', 'policy', 'cookie', 'disclaimer',
                     'cart', 'checkout', 'account', 'login', 'signup', 'register',
                     'search', 'sitemap', 'wp-admin', 'feed', 'wp-json', 'xmlrpc',
                     'cdn-cgi', '.xml', '.json', '.txt', '.css', '.js', '.pdf']
    if any(pat in path for pat in skip_patterns):
        return ('skip', [])
    
    # Homepage
    if path == '' or path in ['index.html', 'index.php', 'home']:
        return ('homepage', ['Organization'])
    
    # Blog / Article
    blog_patterns = ['blog', 'post', 'news', 'article', 'insight', 'guide',
                     'journal', 'story', 'update', 'announcement', 'resource']
    if any(pat in path for pat in blog_patterns):
        return ('blog', ['Article'])
    
    # FAQ
    if 'faq' in path or 'frequently-asked' in path or 'questions' in path:
        return ('faq', ['FAQPage'])
    
    # Reviews / Testimonials
    if 'review' in path or 'testimonial' in path or 'feedback' in path:
        return ('review', ['AggregateRating'])
    
    # Product / Shop / E-commerce
    product_patterns = ['product', 'shop', 'store', 'collection', 'item', 'buy']
    if any(pat in path for pat in product_patterns):
        return ('product', ['Product'])
    
    # Pricing
    if 'pricing' in path or 'plans' in path or 'packages' in path:
        return ('pricing', ['Product'])
    
    # Service pages
    service_patterns = ['service', 'solution', 'offering', 'what-we-do', 'expertise']
    if any(pat in path for pat in service_patterns):
        return ('service', ['Service'])
    
    # About / Contact
    if 'about' in path or 'team' in path or 'our-story' in path:
        return ('about', ['Organization'])
    if 'contact' in path or 'location' in path or 'find-us' in path:
        return ('contact', ['LocalBusiness'])
    
    # Portfolio / Case study / Work
    if 'portfolio' in path or 'case-stud' in path or 'project' in path or 'work' in path:
        return ('portfolio', ['Article'])
    
    # Default: treat as a content/service page
    # Pages with multiple path segments are likely service or content pages
    segments = [s for s in path.split('/') if s]
    if len(segments) >= 2:
        return ('content', ['Article'])
    
    # Single-segment pages are likely service pages
    return ('service', ['Service'])


@app.route('/api/schema/auto-assign', methods=['POST'])
@login_required
def schema_auto_assign():
    """Auto-detect page types and assign best schema types from audit data."""
    try:
        data = request.json or {}
        campaign_id = data.get('campaign_id')
        audit_id = data.get('audit_id')
        
        if not campaign_id:
            return jsonify({'error': 'campaign_id is required'}), 400
        
        db = supabase_admin or supabase
        pages = []
        domain = ''
        
        # Strategy 1: Get pages from site_audits table (global audit)
        if audit_id:
            try:
                sa_res = db.table('site_audits').select('audit_data, domain').eq('id', audit_id).execute()
                if sa_res.data:
                    audit_data = sa_res.data[0].get('audit_data', {}) or {}
                    domain = sa_res.data[0].get('domain', '')
                    raw_pages = []
                    task_id = audit_data.get('dataforseo_task_id') or audit_data.get('task_id')
                    if task_id:
                        try:
                            from api.dataforseo_client import get_page_issues
                            issues_data = get_page_issues(task_id, limit=10000)
                            raw_pages = issues_data.get('pages', [])
                        except Exception as e:
                            logger.warning(f"Schema auto-assign: Strategy 1 dataforseo fetch failed: {e}")
                            
                    if not raw_pages:
                        raw_pages = audit_data.get('pages', [])
                        
                    for p in raw_pages:
                        url = p.get('url', '')
                        title = p.get('title', '') or (p.get('meta', {}) or {}).get('title', '')
                        if url:
                            pages.append({'url': url, 'title': title})
            except Exception as e:
                logger.warning(f"Schema auto-assign: site_audits lookup failed: {e}")
        
        # Strategy 2: Get pages from campaign's latest successful technical audit
        if not pages:
            try:
                # Check audits table for recent technical audits. Only fetch the pages array to avoid OOM on massive results JSON.
                audit_res = db.table('audits').select('id, dataforseo_task_id, pages:results->pages, domain:results->>domain, comp_domain:results->>competitor_domain').eq('campaign_id', campaign_id).eq('type', 'technical').order('created_at', desc=True).limit(5).execute()
                if audit_res.data:
                    for audit_record in audit_res.data:
                        domain = audit_record.get('comp_domain') or audit_record.get('domain', '')
                        task_id = audit_record.get('dataforseo_task_id')
                        
                        raw_pages = []
                        if task_id:
                            try:
                                from api.dataforseo_client import get_page_issues
                                issues_data = get_page_issues(task_id, limit=10000)
                                raw_pages = issues_data.get('pages', [])
                            except Exception as e:
                                logger.warning(f"Schema auto-assign: Strategy 2 dataforseo fetch failed: {e}")
                        
                        if not raw_pages:
                            raw_pages = audit_record.get('pages', [])
                            
                        if raw_pages:
                            for p in raw_pages:
                                if not isinstance(p, dict):
                                    continue
                                url = p.get('url', '')
                                title = p.get('title', '') or (p.get('meta', {}) or {}).get('title', '')
                                if url:
                                    pages.append({'url': url, 'title': title})
                            break  # Found an audit with pages, stop searching older ones
            except Exception as e:
                logger.warning(f"Schema auto-assign: audits lookup failed: {e}")
        
        # Strategy 3: Get from site_audits by domain
        if not pages:
            try:
                camp_res = db.table('campaigns').select('domain').eq('id', campaign_id).single().execute()
                if camp_res.data:
                    domain = camp_res.data.get('domain', '')
                    if domain:
                        sa_res = db.table('site_audits').select('audit_data, pages:audit_data->pages').ilike('domain', f"%{domain}%").order('created_at', desc=True).limit(1).execute()
                        if sa_res.data:
                            audit_data = sa_res.data[0].get('audit_data', {}) or {}
                            raw_pages = []
                            task_id = audit_data.get('dataforseo_task_id') or audit_data.get('task_id')
                            if task_id:
                                try:
                                    from api.dataforseo_client import get_page_issues
                                    issues_data = get_page_issues(task_id, limit=10000)
                                    raw_pages = issues_data.get('pages', [])
                                except Exception as e:
                                    logger.warning(f"Schema auto-assign: Strategy 3 dataforseo fetch failed: {e}")
                                    
                            if not raw_pages:
                                raw_pages = sa_res.data[0].get('pages', []) or []
                                
                            for p in raw_pages:
                                if not isinstance(p, dict):
                                    continue
                                url = p.get('url', '')
                                title = p.get('title', '') or (p.get('meta', {}) or {}).get('title', '')
                                if url:
                                    pages.append({'url': url, 'title': title})
            except Exception as e:
                logger.warning(f"Schema auto-assign: domain lookup failed: {e}")
        
        if not pages:
            return jsonify({'error': 'No audit data found. Run a site audit first to populate pages.'}), 404
        
        # Deduplicate pages by URL
        seen_urls = set()
        unique_pages = []
        for p in pages:
            url = p.get('url', '').rstrip('/')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_pages.append(p)
        
        # Auto-detect page types and assign schemas
        assigned_pages = []
        for p in unique_pages:
            url = p.get('url', '')
            title = p.get('title', '')
            page_type, schema_types = _detect_page_type(url)
            
            assigned_pages.append({
                'url': url,
                'title': title,
                'page_type': page_type,
                'schema_types': schema_types,
                'selected': page_type != 'skip',  # Pre-select non-skip pages
                'status': 'pending'
            })
        
        # Sort: homepage first, then services, then blogs, then others, skip at end
        type_order = {'homepage': 0, 'service': 1, 'product': 2, 'faq': 3, 'review': 4,
                      'blog': 5, 'content': 6, 'about': 7, 'contact': 8, 'portfolio': 9, 'pricing': 10, 'skip': 99}
        assigned_pages.sort(key=lambda x: type_order.get(x['page_type'], 50))
        
        # Save to database immediately for cross-session persistence
        try:
            camp_res = db.table('campaigns').select('settings').eq('id', campaign_id).single().execute()
            if camp_res.data:
                settings = camp_res.data.get('settings', {}) or {}
                settings['schema_pages'] = assigned_pages
                db.table('campaigns').update({'settings': settings}).eq('id', campaign_id).execute()
        except Exception as e:
            logger.error(f"Error saving auto-assigned pages to DB: {e}")
            
        return jsonify({
            'success': True,
            'domain': domain,
            'total_pages': len(assigned_pages),
            'selected_count': sum(1 for p in assigned_pages if p['selected']),
            'pages': assigned_pages
        })
        
    except Exception as e:
        logger.error(f"Schema auto-assign error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/schema/save-config', methods=['POST'])
@login_required
def schema_save_config():
    """Save business questionnaire data and/or schema pages for persistence."""
    try:
        data = request.json or {}
        campaign_id = data.get('campaign_id')
        config = data.get('schema_config')
        pages = data.get('schema_pages')
        
        if not campaign_id:
            return jsonify({'error': 'campaign_id is required'}), 400
        
        db = supabase_admin or supabase
        camp_res = db.table('campaigns').select('settings').eq('id', campaign_id).single().execute()
        settings = camp_res.data.get('settings', {}) if camp_res.data else {}
        
        if config is not None:
            settings['schema_config'] = config
        if pages is not None:
            settings['schema_pages'] = pages
            
        db.table('campaigns').update({'settings': settings}).eq('id', campaign_id).execute()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Schema save config error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate-schema-markup', methods=['POST'])
@login_required
def generate_schema_markup():
    """Generate schema markup for pages using AI with real business data from questionnaire."""
    try:
        data = request.json or {}
        pages = data.get('pages', [])
        schema_config = data.get('schema_config', {})
        domain = data.get('domain', '')
        campaign_id = data.get('campaign_id')
        
        if not pages:
            return jsonify({'error': 'No pages provided'}), 400
        
        # Build business context from questionnaire
        biz_ctx_parts = []
        if schema_config.get('business_name'): biz_ctx_parts.append(f"Business Name: {schema_config['business_name']}")
        if schema_config.get('business_type'): biz_ctx_parts.append(f"Business Type: {schema_config['business_type']}")
        if schema_config.get('phone'): biz_ctx_parts.append(f"Phone: {schema_config['phone']}")
        if schema_config.get('email'): biz_ctx_parts.append(f"Email: {schema_config['email']}")
        if schema_config.get('address'): biz_ctx_parts.append(f"Address: {schema_config['address']}")
        city_state = []
        if schema_config.get('city'): city_state.append(schema_config['city'])
        if schema_config.get('state'): city_state.append(schema_config['state'])
        if schema_config.get('zip'): city_state.append(schema_config['zip'])
        if city_state: biz_ctx_parts.append(f"Location: {', '.join(city_state)}")
        if schema_config.get('country'): biz_ctx_parts.append(f"Country: {schema_config['country']}")
        if schema_config.get('founded'): biz_ctx_parts.append(f"Founded: {schema_config['founded']}")
        if schema_config.get('price_range'): biz_ctx_parts.append(f"Price Range: {schema_config['price_range']}")
        if schema_config.get('logo_url'): biz_ctx_parts.append(f"Logo URL: {schema_config['logo_url']}")
        if schema_config.get('social_links'): biz_ctx_parts.append(f"Social Profiles: {', '.join(schema_config['social_links']) if isinstance(schema_config['social_links'], list) else schema_config['social_links']}")
        if schema_config.get('opening_hours'): biz_ctx_parts.append(f"Opening Hours: {schema_config['opening_hours']}")
        if schema_config.get('description'): biz_ctx_parts.append(f"Business Description: {schema_config['description']}")
        biz_context = "\n".join(biz_ctx_parts) if biz_ctx_parts else "No business details provided."
        
        # Format pages with their assigned schema types
        page_entries = []
        for p in pages:
            url = p.get('url', '')
            title = p.get('title', '')
            schema_types = p.get('schema_types', ['Article'])
            types_str = ', '.join(schema_types) if isinstance(schema_types, list) else str(schema_types)
            page_entries.append(f"- URL: {url} | Title: {title} | Schema Types: {types_str}")
        pages_str = "\n".join(page_entries)
        
        # Determine if we need an Organization/LocalBusiness site-wide schema
        has_homepage = any(p.get('page_type') == 'homepage' for p in pages)
        biz_type = schema_config.get('business_type', 'service')
        site_wide_type = 'LocalBusiness' if biz_type == 'local' else 'Organization'
        
        prompt = f"""You are an expert SEO schema markup generator. Generate production-ready JSON-LD structured data for the following pages.

DOMAIN: {domain}

REAL BUSINESS DATA (use these EXACT values, do NOT use placeholders):
{biz_context}

PAGES TO GENERATE SCHEMA FOR:
{pages_str}

CRITICAL RULES:
1. Use the EXACT business data provided above (name, phone, address, etc.) — NEVER use placeholder text like "INSERT HERE" or "YOUR PHONE".
2. For each page, generate the schema type(s) specified. If multiple types are listed for a page, generate a combined array.
3. For Article/BlogPosting: use the page title as headline, domain as publisher.
4. For Service: describe the service based on the URL slug and title.
5. For FAQPage: generate 3-5 realistic FAQ questions relevant to the page topic.
6. For Product: include price range if provided.
7. For AggregateRating: use realistic rating values (4.2-4.9 range).
8. Always include @context, @type, and all REQUIRED properties for each schema type.
9. Make the JSON-LD immediately usable — user should just copy-paste into their page <head>.
{"10. Generate ONE site-wide " + site_wide_type + " schema as well." if has_homepage else ""}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  {'"site_wide": {{ "type": "' + site_wide_type + '", "description": "Site-wide schema for homepage", "json_ld": {{ ... complete JSON-LD object ... }} }},' if has_homepage else ''}
  "pages": [
    {{
      "url": "the page URL",
      "schema_types": ["Type1", "Type2"],
      "page_type": "detected page type",
      "json_ld": [{{ ... complete JSON-LD object(s) ... }}],
      "serp_benefit": "What rich result this enables"
    }}
  ]
}}

IMPORTANT: json_ld must be actual JSON objects, NOT strings. Each item in the json_ld array is a complete schema object."""

        result = gemini_client.generate_content(
            prompt=prompt,
            model_name="gemini-2.5-flash",
            use_grounding=False
        )
        
        if not result:
            return jsonify({'error': 'Empty response from AI'}), 500
        
        text = result.strip()
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)
            
        import json
        parsed = json.loads(text)
        
        # Normalize: ensure json_ld is always an array
        for page in parsed.get('pages', []):
            jld = page.get('json_ld')
            if isinstance(jld, dict):
                page['json_ld'] = [jld]
            elif isinstance(jld, str):
                try:
                    obj = json.loads(jld)
                    page['json_ld'] = [obj] if isinstance(obj, dict) else obj
                except:
                    page['json_ld'] = []
        
        # Normalize site_wide json_ld too
        if parsed.get('site_wide'):
            sw_jld = parsed['site_wide'].get('json_ld')
            if isinstance(sw_jld, str):
                try:
                    parsed['site_wide']['json_ld'] = json.loads(sw_jld)
                except:
                    pass
        
        # Persist to campaign settings
        if campaign_id:
            try:
                db = supabase_admin or supabase
                camp_res = db.table('campaigns').select('settings').eq('id', campaign_id).single().execute()
                settings = camp_res.data.get('settings', {}) if camp_res.data else {}
                
                # Merge with existing schema_pages
                existing_pages = settings.get('schema_pages', [])
                new_pages = parsed.get('pages', [])
                
                # Build a URL→entry map from existing
                url_map = {p.get('url', '').rstrip('/'): p for p in existing_pages}
                
                # Update/add new pages
                for np in new_pages:
                    url_key = np.get('url', '').rstrip('/')
                    np['status'] = 'generated'
                    np['generated_at'] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    url_map[url_key] = np
                
                settings['schema_pages'] = list(url_map.values())
                
                # Save site_wide if present
                if parsed.get('site_wide'):
                    settings['schema_site_wide'] = parsed['site_wide']
                    settings['schema_site_wide']['generated_at'] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                db.table('campaigns').update({'settings': settings}).eq('id', campaign_id).execute()
            except Exception as e:
                logger.error(f"Error saving schema markup: {e}")
                
        return jsonify({'success': True, 'schema': parsed})
        
    except json.JSONDecodeError as e:
        logger.error(f"Schema JSON Parse Error: {e}")
        return jsonify({'error': f'AI returned invalid JSON: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Schema Markup Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# ANCHOR TEXT PLANNER

@app.route('/api/anchor-text-analysis', methods=['POST'])
@login_required
def anchor_text_analysis():
    """Analyze existing anchor text distribution and recommend ideal ratios."""
    try:
        data = request.json or {}
        campaign_id = data.get('campaign_id')
        brand_config = data.get('brand_config', {})
        target_keywords = data.get('target_keywords', [])
        
        if not campaign_id:
            return jsonify({'error': 'campaign_id required'}), 400
        
        # Fetch backlink data
        from api.dataforseo_client import get_backlinks_summary
        db = supabase_admin or supabase
        campaign = db.table('campaigns').select('domain').eq('id', campaign_id).limit(1).execute()
        if not campaign.data:
            return jsonify({'error': 'Campaign not found'}), 404
        
        domain = campaign.data[0].get('domain', '')
        if not domain:
            return jsonify({'error': 'No domain set'}), 400
        
        # Get backlink anchors from DataForSEO
        from api.dataforseo_client import dataforseo_request
        anchors_result = dataforseo_request(
            '/v3/backlinks/anchors/live',
            [{"target": domain, "limit": 100, "order_by": ["backlinks,desc"]}]
        )
        
        anchors = []
        if anchors_result and anchors_result.get('tasks'):
            for task in anchors_result['tasks']:
                for r in (task.get('result') or []):
                    for item in (r.get('items') or []):
                        anchors.append({
                            'anchor': item.get('anchor', ''),
                            'backlinks': item.get('backlinks', 0),
                            'referring_domains': item.get('referring_domains', 0),
                            'first_seen': item.get('first_seen', ''),
                        })
        
        # Classify anchors
        brand_name = (brand_config.get('business_name') or domain.split('.')[0]).lower()
        
        categories = {
            'branded': {'count': 0, 'backlinks': 0, 'items': []},
            'exact_match': {'count': 0, 'backlinks': 0, 'items': []},
            'partial_match': {'count': 0, 'backlinks': 0, 'items': []},
            'naked_url': {'count': 0, 'backlinks': 0, 'items': []},
            'generic': {'count': 0, 'backlinks': 0, 'items': []},
            'other': {'count': 0, 'backlinks': 0, 'items': []}
        }
        
        generic_terms = ['click here', 'read more', 'learn more', 'visit', 'here', 'this', 'website', 'link', 'source', 'more info']
        target_kws_lower = [kw.lower() for kw in target_keywords]
        
        total_backlinks = 0
        for anchor_data in anchors:
            anchor = (anchor_data.get('anchor') or '').lower().strip()
            bl = anchor_data.get('backlinks', 0)
            total_backlinks += bl
            
            if not anchor:
                categories['other']['count'] += 1
                categories['other']['backlinks'] += bl
                continue
            
            if brand_name in anchor or domain.replace('www.', '').split('.')[0] in anchor:
                categories['branded']['count'] += 1
                categories['branded']['backlinks'] += bl
                categories['branded']['items'].append(anchor_data)
            elif anchor in target_kws_lower:
                categories['exact_match']['count'] += 1
                categories['exact_match']['backlinks'] += bl
                categories['exact_match']['items'].append(anchor_data)
            elif any(kw in anchor for kw in target_kws_lower):
                categories['partial_match']['count'] += 1
                categories['partial_match']['backlinks'] += bl
                categories['partial_match']['items'].append(anchor_data)
            elif 'http' in anchor or domain in anchor or '.' in anchor.split(' ')[0]:
                categories['naked_url']['count'] += 1
                categories['naked_url']['backlinks'] += bl
                categories['naked_url']['items'].append(anchor_data)
            elif anchor in generic_terms:
                categories['generic']['count'] += 1
                categories['generic']['backlinks'] += bl
                categories['generic']['items'].append(anchor_data)
            else:
                categories['other']['count'] += 1
                categories['other']['backlinks'] += bl
                categories['other']['items'].append(anchor_data)
        
        # Calculate percentages
        distribution = {}
        for cat, info in categories.items():
            pct = round((info['backlinks'] / total_backlinks * 100), 1) if total_backlinks > 0 else 0
            distribution[cat] = {
                'count': info['count'],
                'backlinks': info['backlinks'],
                'percentage': pct,
                'top_anchors': [{'anchor': i['anchor'], 'backlinks': i['backlinks']} for i in sorted(info['items'], key=lambda x: x['backlinks'], reverse=True)[:5]]
            }
        
        # Ideal ratios
        ideal = {
            'branded': {'min': 30, 'max': 50, 'label': 'Brand Name + Variations'},
            'exact_match': {'min': 1, 'max': 5, 'label': 'Exact Target Keywords'},
            'partial_match': {'min': 15, 'max': 25, 'label': 'Keyword Variations'},
            'naked_url': {'min': 10, 'max': 20, 'label': 'Raw URLs'},
            'generic': {'min': 5, 'max': 15, 'label': 'Click Here, Learn More, etc.'},
            'other': {'min': 5, 'max': 20, 'label': 'Miscellaneous / Natural'}
        }
        
        # Flag issues
        warnings = []
        for cat, rec in ideal.items():
            actual = distribution.get(cat, {}).get('percentage', 0)
            if actual > rec['max']:
                warnings.append(f"{rec['label']} is too high ({actual}% vs ideal {rec['max']}% max)")
            elif actual < rec['min'] and cat in ['branded']:
                warnings.append(f"{rec['label']} is too low ({actual}% vs ideal {rec['min']}% min)")
        
        if distribution.get('exact_match', {}).get('percentage', 0) > 10:
            warnings.append("⚠️ Exact match anchors over 10% — over-optimization risk!")
        
        return jsonify({
            'success': True,
            'domain': domain,
            'total_anchors': len(anchors),
            'total_backlinks': total_backlinks,
            'distribution': distribution,
            'ideal_ratios': ideal,
            'warnings': warnings,
            'raw_anchors': anchors[:50]
        })
        
    except Exception as e:
        logger.error(f"Anchor Text Analysis Error: {e}")
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

        # Fresh fetch of assigned campaigns to prevent stale session issues
        profile_res = client.table('profiles').select('assigned_campaigns').eq('id', user['id']).execute()
        assigned = profile_res.data[0].get('assigned_campaigns', []) if profile_res.data else []

        # Filter based on role (Permissions within Org)
        user_role = user.get('role', 'viewer')
        if user_role == 'admin':
            pass # Admin sees all
        elif assigned:
            # DB query fetching all is fine for now, we'll filter in memory below
            pass
        else:
            # Regular users with no assigned campaigns see only their assigned tasks
            query = query.eq('assigned_to', user['id'])
        
        if campaign_id:
            query = query.eq('campaign_id', campaign_id)
        
        if status:
            query = query.eq('status', status)
        
        response = query.order('created_at', desc=True).execute()
        tasks = response.data or []

        # For non-admin roles, filter by assigned campaigns
        if user_role == 'admin':
            pass  # Full visibility — see all org tasks
        elif assigned:
            # Keep tasks that are either in an assigned campaign OR directly assigned to user
            tasks = [t for t in tasks if str(t.get('campaign_id')) in assigned or t.get('assigned_to') == user['id']]
        # else: no assigned_campaigns — the DB query already filtered by assigned_to, so no extra filter needed

        return jsonify({'tasks': tasks})
        
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
        # Viewers are strictly read-only — block all mutations
        if user_role == 'viewer':
            return jsonify({'error': 'Viewers have read-only access'}), 403
        if user_role not in ['admin', 'administrator', 'campaign_manager']:
            if task.data.get('assigned_to') != user['id']:
                return jsonify({'error': 'Not authorized'}), 403
        
        # Update
        update_data = {}
        if 'status' in data:
            update_data['status'] = data['status']
        if 'checklist' in data:
            update_data['checklist'] = data['checklist']
        if 'assigned_to' in data and user.get('role', '').lower() in ['admin', 'administrator', 'campaign_manager']:
            update_data['assigned_to'] = data['assigned_to']
        if 'assigned_role' in data and user.get('role', '').lower() in ['admin', 'administrator', 'campaign_manager']:
            update_data['assigned_role'] = data['assigned_role']
        
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
        # Get campaign domain + settings (need settings for location_code resolution)
        campaign = client.table('campaigns').select('domain, settings').eq('id', data.get('campaign_id')).single().execute()
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
            from api.dataforseo_client import fetch_ranked_keywords, fetch_backlinks_summary, get_referring_domains, location_code_for, get_domain_rank_overview
            
            # Resolve country to DataForSEO location_code
            # For competitor audits: use competitor_country if provided, else fall back to campaign location
            saved_location = (campaign.data.get('settings') or {}).get('location', 'US')
            if audit_type == 'competitor' and data.get('competitor_country'):
                saved_location = data.get('competitor_country')
            audit_location_code = location_code_for(saved_location)
            
            keywords_data = fetch_ranked_keywords(target_domain, location_code=audit_location_code)
            keywords = keywords_data.get('keywords', []) if isinstance(keywords_data, dict) else []
            keywords_total_count = keywords_data.get('total_count', len(keywords))
            keywords_estimated_traffic = keywords_data.get('estimated_traffic', 0)
            keywords_at_limit = keywords_data.get('keywords_at_limit', len(keywords) >= 1000)
            
            backlinks_summary = fetch_backlinks_summary(target_domain)
            referring_domains = get_referring_domains(target_domain)
            
            # Fetch domain rank overview (position distribution for slides)
            domain_rank = get_domain_rank_overview(target_domain)
            
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
                'domain_rank': domain_rank,
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
            
            # Use get_audit_summary directly because tasks_ready expires tasks!
            summary_check = get_audit_summary(task_id)
            is_ready = summary_check.get('success') and summary_check.get('summary', {}).get('crawl_progress') == 'finished'
            
            if is_ready:
                # Audit finished! Fetch results and update
                try:
                    summary = summary_check
                    
                    # 2. Get Page Issues
                    pages_result = get_page_issues(task_id, limit=1000)
                    pages = pages_result.get('pages', [])
                    
                    # 3. Categorize Results for UI (First, so we can use for tasks)
                    categorized = categorize_audit_issues(pages, summary.get('summary'))
                    
                    # 4. Create Tasks
                    if audit.get('type') != 'competitor':
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
                    
                    # Also merge keyword/traffic data from projects table into results
                    # so that competitor stats are always findable from audits.results
                    if 'total_keywords' not in new_results or not new_results.get('total_keywords'):
                        try:
                            proj_merge = client.table('projects').select('full_audit_data').eq('audit_id', audit_id).limit(1).execute()
                            if proj_merge.data:
                                fad = proj_merge.data[0].get('full_audit_data') or {}
                                new_results.setdefault('total_keywords', fad.get('total_keywords', 0))
                                new_results.setdefault('total_traffic', fad.get('total_traffic', 0))
                                new_results.setdefault('keywords', fad.get('organic_keywords', []))
                                new_results.setdefault('backlinks_summary', fad.get('backlinks_summary', {}))
                                new_results.setdefault('domain_rank', fad.get('domain_rank', 0))
                        except Exception:
                            pass
                    
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


@app.route('/api/audits/<audit_id>/export-sheets', methods=['POST'])
@login_required
def export_audit_sheets(audit_id):
    """Export audit results to a 12-tab Google Sheet."""
    try:
        client = supabase_admin or supabase
        response = client.table('audits').select('*, campaigns(name, domain)').eq('id', audit_id).single().execute()
        audit = response.data

        if not audit or not audit.get('results'):
            return jsonify({'error': 'Audit has no results to export'}), 400

        results = audit['results']
        # Ensure categorized data exists
        if 'categorized' not in results and 'pages' in results:
            results['categorized'] = categorize_audit_issues(results['pages'], results.get('summary'))

        domain = (audit.get('campaigns') or {}).get('domain', 'site')

        from execution.export_sheets import export_audit_to_sheets
        result = export_audit_to_sheets(audit, domain)

        if result.get('success'):
            # Save sheets URL to audit record
            client.table('audits').update({
                'sheets_url': result['spreadsheet_url']
            }).eq('id', audit_id).execute()

        return jsonify(result)

    except Exception as e:
        logger.error(f"Export to Sheets failed: {e}")
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
    proj = {}
    resolved_domain = None  # Track the domain for live fallback
    resolved_location = 'US'  # Track location for correct API calls

    try:
        if audit_id:
            # Competitor row: find the competitor domain from audits
            aud_res = db.table('audits').select('results, type').eq('id', audit_id).limit(1).execute()
            if aud_res.data and aud_res.data[0].get('results'):
                comp_domain = aud_res.data[0]['results'].get('competitor_domain')
                if comp_domain:
                    resolved_domain = comp_domain.replace('https://', '').replace('http://', '').split('/')[0].strip('www.')
                    # Try site_audits table (column is 'audit_data', not 'full_audit_data')
                    sa_res = db.table('site_audits').select('audit_data').ilike('domain', f"%{resolved_domain}%").limit(1).execute()
                    if sa_res.data:
                        proj = sa_res.data[0].get('audit_data') or {}
            
            # Fallback to projects
            if not proj:
                proj_res = db.table('projects').select('full_audit_data').eq('audit_id', audit_id).limit(1).execute()
                if proj_res.data:
                    proj = proj_res.data[0].get('full_audit_data') or {}
                    if not resolved_domain:
                        resolved_domain = proj.get('domain', '')
        else:
            # Client row
            if not campaign_id:
                return jsonify({'error': 'campaign_id or audit_id required'}), 400
            
            camp_res = db.table('campaigns').select('domain, settings').eq('id', campaign_id).limit(1).execute()
            camp_data = (camp_res.data or [{}])[0]
            domain = camp_data.get('domain')
            resolved_location = (camp_data.get('settings') or {}).get('location', 'US')
            if domain:
                resolved_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].strip('www.')
                sa_res = db.table('site_audits').select('audit_data').ilike('domain', f"%{resolved_domain}%").limit(1).execute()
                if sa_res.data:
                    proj = sa_res.data[0].get('audit_data') or {}
            
            if not proj:
                aud_res = db.table('audits').select('id').eq('campaign_id', campaign_id).eq('type', 'technical').order('created_at', desc=True).limit(1).execute()
                latest_audit_id = (aud_res.data or [{}])[0].get('id')
                if latest_audit_id:
                    proj_res = db.table('projects').select('full_audit_data').eq('audit_id', latest_audit_id).limit(1).execute()
                    if proj_res.data:
                        proj = proj_res.data[0].get('full_audit_data') or {}

        # Keywords are stored as organic_keywords in full_audit_data
        keywords_raw = proj.get('organic_keywords') or proj.get('keywords', [])
        total_kw = proj.get('total_keywords', len(keywords_raw))
        total_traffic = proj.get('total_traffic', 0)
        
        # Fallback: if proj was empty and we have an audit_id, try audits.results directly
        # (competitor audits store keywords/traffic in audits.results at creation time)
        if not keywords_raw and not total_kw and audit_id:
            try:
                aud_direct = db.table('audits').select('results').eq('id', audit_id).limit(1).execute()
                if aud_direct.data:
                    aud_results = aud_direct.data[0].get('results') or {}
                    keywords_raw = aud_results.get('keywords', [])
                    total_kw = aud_results.get('total_keywords', 0) or 0
                    total_traffic = aud_results.get('total_traffic', 0) or 0
                    if not resolved_domain:
                        resolved_domain = aud_results.get('competitor_domain', '')
            except Exception:
                pass
        
        # LIVE FALLBACK: If no stored data exists but we have a domain, fetch live from DataForSEO
        if not keywords_raw and resolved_domain:
            logger.info(f"client/stats: No stored data for {resolved_domain}, fetching live from DataForSEO")
            try:
                from api.dataforseo_client import fetch_domain_metrics, fetch_ranked_keywords, location_code_for
                fallback_loc = location_code_for(resolved_location)
                
                # Quick domain totals (fast, ~$0.005)
                dm = fetch_domain_metrics(resolved_domain, location_code=fallback_loc)
                if dm.get('success'):
                    total_kw = dm.get('total_keywords', 0)
                    total_traffic = dm.get('total_traffic', 0)
                
                # Top 10 keywords (slightly more expensive but needed for display)
                kw_result = fetch_ranked_keywords(resolved_domain, limit=10, location_code=fallback_loc)
                if isinstance(kw_result, dict) and kw_result.get('success'):
                    keywords_raw = kw_result.get('keywords', [])
                    if not total_kw:
                        total_kw = kw_result.get('total_count', len(keywords_raw))
                    if not total_traffic:
                        total_traffic = kw_result.get('estimated_traffic', 0)
            except Exception as live_err:
                logger.warning(f"client/stats live fallback failed (non-fatal): {live_err}")
                
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
@login_required
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



@app.route('/api/competitors/gap-analysis', methods=['GET'])
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
    """Generate AI-powered strategic recommendations based on scoring + gap analysis.
    
    Uses the scorecard data + gap keywords to produce contextual, actionable recommendations
    via Gemini. Falls back to an analytical template if Gemini is unavailable.
    """
    campaign_id = request.args.get('campaign_id')
    competitor_domain = request.args.get('competitor_domain')
    
    if not campaign_id or not competitor_domain:
        return jsonify({'error': 'Campaign ID and Competitor Domain are required'}), 400
        
    db = supabase_admin or supabase
    
    try:
        # Get target campaign
        campaign_res = db.table('campaigns').select('domain, settings, name').eq('id', campaign_id).single().execute()
        campaign = campaign_res.data
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
            
        target_domain = campaign['domain']
        brand_config = (campaign.get('settings') or {}).get('brand_config', {})
        
        # 1. Collect scoring data for both domains
        client_data = _collect_domain_data(db, target_domain, campaign_id=campaign_id)
        comp_data = _collect_domain_data(db, competitor_domain)
        
        # 2. Get gap keywords (top 10)
        gap_keywords = []
        try:
            from api.dataforseo_client import get_keyword_gap, location_code_for
            saved_location = (campaign.get('settings') or {}).get('location', 'US')
            loc_code = location_code_for(saved_location)
            gap_results = get_keyword_gap(target_domain, competitor_domain, location_code=loc_code)
            if gap_results.get('success'):
                for kw in gap_results.get('gap_keywords', [])[:10]:
                    word = kw.get('keyword_data', {}).get('keyword', '')
                    vol = kw.get('keyword_data', {}).get('keyword_info', {}).get('search_volume', 0)
                    rank = (kw.get('ranked_serp_element') or {}).get('serp_item', {}).get('rank_absolute', '—')
                    if word:
                        gap_keywords.append({'keyword': word, 'volume': vol, 'competitor_rank': rank})
        except Exception as gap_err:
            logger.warning(f"Strategy gap fetch failed (non-fatal): {gap_err}")
        
        # 3. Compute quick scores for the prompt context
        both = [
            {**client_data, 'domain': target_domain, 'is_client': True},
            {**comp_data, 'domain': competitor_domain, 'is_client': False}
        ]
        scorecards = _compute_scorecards(both)
        client_card = next((s for s in scorecards if s.get('is_client')), {})
        comp_card = next((s for s in scorecards if not s.get('is_client')), {})
        
        # 4. Build context for Gemini
        gap_text = "\n".join([f"- {g['keyword']} (vol: {g['volume']}, competitor rank: #{g['competitor_rank']})" for g in gap_keywords]) or "No gap data available"
        
        brand_context = ""
        if brand_config:
            brand_context = f"""
Brand Context:
- USP: {brand_config.get('usp', 'Not set')}
- Voice: {brand_config.get('voice', 'Not set')}
- Target Audience: {brand_config.get('target_audience', 'Not set')}
"""
        
        prompt = f"""You are a senior SEO strategist creating an actionable competitor strategy report.

CLIENT: {target_domain}
  - Brand Authority Score: {client_card.get('brand_authority', 0)}/100
  - Competitive Score: {client_card.get('competitive_score', 0)}/100
  - Traffic: {client_data.get('total_traffic', 0):,}
  - Keywords: {client_data.get('total_keywords', 0):,}
  - Domain Rank: {client_data.get('domain_rank', 0)}
  - Referring Domains: {client_data.get('referring_domains', 0):,}
  - Funnel: ToFu {client_card.get('funnel_score', {}).get('tofu', 0)}% | MoFu {client_card.get('funnel_score', {}).get('mofu', 0)}% | BoFu {client_card.get('funnel_score', {}).get('bofu', 0)}%

COMPETITOR: {competitor_domain}
  - Brand Authority Score: {comp_card.get('brand_authority', 0)}/100
  - Competitive Score: {comp_card.get('competitive_score', 0)}/100
  - Traffic: {comp_data.get('total_traffic', 0):,}
  - Keywords: {comp_data.get('total_keywords', 0):,}
  - Domain Rank: {comp_data.get('domain_rank', 0)}
  - Referring Domains: {comp_data.get('referring_domains', 0):,}
  - Funnel: ToFu {comp_card.get('funnel_score', {}).get('tofu', 0)}% | MoFu {comp_card.get('funnel_score', {}).get('mofu', 0)}% | BoFu {comp_card.get('funnel_score', {}).get('bofu', 0)}%

KEYWORD GAPS (competitor ranks, client doesn't):
{gap_text}
{brand_context}
Generate exactly 5 strategic recommendations in HTML format. Each recommendation should:
1. Have a numbered heading (h4 tag)
2. Include specific, data-backed reasoning
3. Reference the actual metrics above
4. Provide a clear action item
5. Estimate expected impact (low/medium/high)

Focus areas should cover: content gaps, link building, funnel optimization, competitive positioning, and quick wins.

Output ONLY the HTML content (h4 + p tags), no wrapping divs or extra markup."""

        # 5. Try Gemini, fall back to analytical template
        strategy_html = None
        try:
            result = gemini_client.generate_content(
                prompt=prompt,
                model_name="gemini-2.5-flash",
            )
            if result and len(result.strip()) > 100:
                strategy_html = result.strip()
        except Exception as llm_err:
            logger.warning(f"Gemini strategy generation failed: {llm_err}")
        
        # Fallback: structured analytical template using real data
        if not strategy_html:
            gap_list = "\n".join([f"<li><strong>{g['keyword']}</strong> (Volume: {g['volume']:,}, Competitor Rank: #{g['competitor_rank']})</li>" for g in gap_keywords[:5]]) or "<li>No significant content gaps found</li>"
            
            auth_diff = comp_card.get('brand_authority', 0) - client_card.get('brand_authority', 0)
            auth_direction = "ahead of" if auth_diff < 0 else "behind"
            
            traffic_ratio = comp_data.get('total_traffic', 1) / max(client_data.get('total_traffic', 1), 1)
            
            bofu_gap = comp_card.get('funnel_score', {}).get('bofu', 0) - client_card.get('funnel_score', {}).get('bofu', 0)
            
            strategy_html = f"""
            <h4>1. Attack High-Value Content Gaps</h4>
            <p>Your competitor <strong>{competitor_domain}</strong> ranks for keywords you don't yet target. Prioritize creating pillar content for these high-value terms:</p>
            <ul>{gap_list}</ul>
            <p><em>Expected Impact: High — these represent untapped search demand your competitor is already capturing.</em></p>
            
            <h4>2. Bridge the Authority Gap</h4>
            <p>You are currently {abs(auth_diff)} points {auth_direction} {competitor_domain} on Brand Authority ({client_card.get('brand_authority', 0)} vs {comp_card.get('brand_authority', 0)}). 
            {'Focus on earning high-DR referring domains through guest posts, digital PR, and HARO to close this gap.' if auth_diff > 0 else 'Maintain your authority advantage by continuing your backlink acquisition strategy.'}</p>
            <p>Target: {max(comp_data.get('referring_domains', 0) - client_data.get('referring_domains', 0), 10)} new referring domains over the next 90 days.</p>
            <p><em>Expected Impact: {'High' if auth_diff > 10 else 'Medium'}</em></p>
            
            <h4>3. Optimize Funnel Distribution</h4>
            <p>Your content funnel is {client_card.get('funnel_score', {}).get('tofu', 0)}% ToFu / {client_card.get('funnel_score', {}).get('mofu', 0)}% MoFu / {client_card.get('funnel_score', {}).get('bofu', 0)}% BoFu.
            {'You need more bottom-of-funnel content to improve conversion readiness.' if bofu_gap > 5 else 'Your funnel balance is competitive.'} 
            {competitor_domain} has {comp_card.get('funnel_score', {}).get('bofu', 0)}% BoFu content.</p>
            <p><em>Expected Impact: {'High' if bofu_gap > 10 else 'Medium'}</em></p>
            
            <h4>4. Apply the 30% Better Rule (Skyscraper)</h4>
            <p>{competitor_domain} gets ~{comp_data.get('total_traffic', 0):,} organic visits vs your {client_data.get('total_traffic', 0):,}. 
            For the gap keywords above, create content that is 30% more comprehensive with unique data, better visuals, and expert quotes that {competitor_domain} lacks.</p>
            <p><em>Expected Impact: Medium-High</em></p>
            
            <h4>5. Internal Link Architecture</h4>
            <p>Route authority to new gap content by adding 3-5 internal links from your strongest pages (homepage, main service pages) using descriptive anchor text. 
            This compounds the organic lift from steps 1-4.</p>
            <p><em>Expected Impact: Medium — amplifies all other strategies.</em></p>
            """
        
        return jsonify({
            'success': True,
            'target_domain': target_domain,
            'competitor_domain': competitor_domain,
            'recommendations_html': strategy_html,
            'client_score': client_card.get('competitive_score', 0),
            'competitor_score': comp_card.get('competitive_score', 0),
            'gap_keywords_count': len(gap_keywords),
            'ai_generated': strategy_html is not None and 'Attack High-Value Content' not in (strategy_html[:50] if strategy_html else '')
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# COMPETITOR SCORING SYSTEM (Phase 2 Completion)
# =============================================================================

@app.route('/api/competitors/scorecard', methods=['GET'])
@login_required
@permission_required('view_all_campaigns')
def get_competitor_scorecard():
    """Compute Brand Authority, Funnel Orientation, and Multi-Parameter scores.
    
    All scores are derived from data we already fetch (traffic, keywords, backlinks, DR).
    No additional DataForSEO API calls — purely computational.
    
    Returns a scorecard for the client and each competitor with:
      - brand_authority (0-100): Weighted composite of DR, referring domains, traffic
      - funnel_score: ToFu/MoFu/BoFu distribution based on keyword intent classification
      - competitive_score (0-100): Multi-parameter composite score
      - parameter_breakdown: Individual dimension scores
    """
    campaign_id = request.args.get('campaign_id')
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400
    
    db = supabase_admin or supabase
    
    try:
        # 1. Get client campaign data
        camp_res = db.table('campaigns').select('domain, settings').eq('id', campaign_id).single().execute()
        camp = camp_res.data
        if not camp:
            return jsonify({'error': 'Campaign not found'}), 404
        
        client_domain = camp['domain'].replace('https://', '').replace('http://', '').split('/')[0]
        
        # 2. Get competitor audits
        aud_res = db.table('audits').select('id, results, status').eq('campaign_id', campaign_id).eq('type', 'competitor').order('created_at', desc=True).execute()
        competitor_audits = aud_res.data or []
        
        # 3. Collect data for all domains (client + competitors)
        domains_data = []
        
        # -- Client data from site_audits or projects --
        client_data = _collect_domain_data(db, client_domain, campaign_id=campaign_id)
        client_data['is_client'] = True
        client_data['domain'] = client_domain
        domains_data.append(client_data)
        
        # -- Competitor data --
        for audit in competitor_audits:
            if audit.get('status') != 'completed':
                continue
            comp_domain = (audit.get('results') or {}).get('competitor_domain', '')
            if not comp_domain:
                continue
            comp_domain = comp_domain.replace('https://', '').replace('http://', '').split('/')[0]
            comp_data = _collect_domain_data(db, comp_domain, audit_id=audit['id'])
            comp_data['is_client'] = False
            comp_data['domain'] = comp_domain
            domains_data.append(comp_data)
        
        if len(domains_data) < 2:
            return jsonify({
                'success': True,
                'message': 'Need at least 1 completed competitor audit for scoring',
                'scorecards': []
            })
        
        # 4. Compute scores
        scorecards = _compute_scorecards(domains_data)
        
        return jsonify({
            'success': True,
            'scorecards': scorecards,
            'total_domains': len(scorecards)
        })
        
    except Exception as e:
        logger.error(f"Scorecard error: {e}")
        return jsonify({'error': str(e)}), 500


def _collect_domain_data(db, domain, campaign_id=None, audit_id=None):
    """Gather all available metrics for a domain from stored data."""
    data = {
        'total_keywords': 0,
        'total_traffic': 0,
        'backlinks_total': 0,
        'referring_domains': 0,
        'domain_rank': 0,
        'top_keywords': [],
    }
    
    try:
        # Try site_audits first (column is 'audit_data', not 'full_audit_data')
        sa_res = db.table('site_audits').select('audit_data').ilike('domain', f"%{domain}%").limit(1).execute()
        if sa_res.data:
            fad = sa_res.data[0].get('audit_data') or {}
            data['total_keywords'] = fad.get('total_keywords', 0) or 0
            data['total_traffic'] = fad.get('total_traffic', 0) or 0
            data['backlinks_total'] = fad.get('backlinks_total', 0) or 0
            ref_dom = fad.get('referring_domains', 0)
            data['referring_domains'] = len(ref_dom) if isinstance(ref_dom, list) else (ref_dom or 0)
            data['domain_rank'] = fad.get('domain_rank', 0) or 0
            data['top_keywords'] = fad.get('organic_keywords', []) or []
            if data['total_keywords'] or data['total_traffic']:
                return data
        
        # Try projects table
        proj_res = type('obj', (object,), {'data': []})()
        if audit_id:
            proj_res = db.table('projects').select('full_audit_data').eq('audit_id', audit_id).limit(1).execute()
        elif campaign_id:
            # Get latest technical audit for this campaign
            aud_res = db.table('audits').select('id').eq('campaign_id', campaign_id).eq('type', 'technical').order('created_at', desc=True).limit(1).execute()
            if aud_res.data:
                proj_res = db.table('projects').select('full_audit_data').eq('audit_id', aud_res.data[0]['id']).limit(1).execute()
        
        if proj_res.data:
            fad = proj_res.data[0].get('full_audit_data') or {}
            data['total_keywords'] = fad.get('total_keywords', 0) or 0
            data['total_traffic'] = fad.get('total_traffic', 0) or 0
            data['backlinks_total'] = fad.get('backlinks_total', 0) or 0
            ref_dom = fad.get('referring_domains', 0)
            data['referring_domains'] = len(ref_dom) if isinstance(ref_dom, list) else (ref_dom or 0)
            data['domain_rank'] = fad.get('domain_rank', 0) or 0
            data['top_keywords'] = fad.get('organic_keywords', []) or []
            if data['total_keywords'] or data['total_traffic']:
                return data
        
        # Fallback: try audits.results directly (competitor audits store data here)
        if audit_id:
            aud_direct = db.table('audits').select('results').eq('id', audit_id).limit(1).execute()
            if aud_direct.data:
                results = aud_direct.data[0].get('results') or {}
                data['total_keywords'] = results.get('total_keywords', 0) or 0
                data['total_traffic'] = results.get('total_traffic', 0) or 0
                bl_summary = results.get('backlinks_summary') or results.get('backlinks', {})
                if isinstance(bl_summary, dict):
                    data['backlinks_total'] = bl_summary.get('total_backlinks', 0) or 0
                    data['referring_domains'] = bl_summary.get('total_referring_domains', 0) or 0
                data['top_keywords'] = results.get('keywords', []) or []
                
        # Fallback for campaign: try fetching live from DataForSEO if still empty
        if not data['total_keywords'] and not data['total_traffic'] and domain:
            try:
                from api.dataforseo_client import fetch_domain_metrics
                dm = fetch_domain_metrics(domain)
                if dm and dm.get('success'):
                    data['total_keywords'] = dm.get('total_keywords', 0) or 0
                    data['total_traffic'] = dm.get('total_traffic', 0) or 0
            except Exception:
                pass
                
    except Exception as e:
        logger.warning(f"_collect_domain_data({domain}): {e}")
    
    return data


def _compute_scorecards(domains_data):
    """Compute all scoring dimensions for a list of domains.
    
    Scoring model:
      Brand Authority (0-100): 40% DR + 30% Referring Domains + 30% Traffic
      Funnel Score: Classify keywords into ToFu/MoFu/BoFu by search intent
      Competitive Score (0-100): Weighted composite of 6 parameters
    """
    import math
    
    # Find max values for normalization
    max_traffic = max((d.get('total_traffic', 0) for d in domains_data), default=1) or 1
    max_keywords = max((d.get('total_keywords', 0) for d in domains_data), default=1) or 1
    max_backlinks = max((d.get('backlinks_total', 0) for d in domains_data), default=1) or 1
    max_ref_domains = max((d.get('referring_domains', 0) for d in domains_data), default=1) or 1
    max_dr = max((d.get('domain_rank', 0) for d in domains_data), default=1) or 1
    
    scorecards = []
    
    for d in domains_data:
        traffic = d.get('total_traffic', 0) or 0
        keywords = d.get('total_keywords', 0) or 0
        backlinks = d.get('backlinks_total', 0) or 0
        ref_domains = d.get('referring_domains', 0) or 0
        dr = d.get('domain_rank', 0) or 0
        top_kws = d.get('top_keywords', []) or []
        
        # --- Brand Authority Score (0-100) ---
        # DR is already 0-100 scale, normalize others to 0-100
        dr_norm = min(dr, 100)
        ref_norm = min((ref_domains / max_ref_domains) * 100, 100)
        traffic_norm = min((traffic / max_traffic) * 100, 100)
        
        brand_authority = round(
            (dr_norm * 0.40) +
            (ref_norm * 0.30) +
            (traffic_norm * 0.30)
        )
        brand_authority = min(brand_authority, 100)
        
        # --- Funnel Orientation Score ---
        # Classify keywords by intent signals
        tofu, mofu, bofu = 0, 0, 0
        tofu_terms = ['what', 'how', 'why', 'guide', 'tutorial', 'tips', 'learn', 'example', 'best']
        mofu_terms = ['vs', 'comparison', 'review', 'alternative', 'top', 'compare', 'difference']
        bofu_terms = ['buy', 'price', 'cost', 'deal', 'discount', 'coupon', 'near me', 'hire', 'service', 'agency', 'free trial']
        
        for kw_item in top_kws[:100]:  # Limit to top 100 for speed
            kw_text = ''
            if isinstance(kw_item, dict):
                kw_text = (kw_item.get('keyword_data', {}).get('keyword', '') or 
                          kw_item.get('keyword', '')).lower()
            elif isinstance(kw_item, str):
                kw_text = kw_item.lower()
            
            if not kw_text:
                continue
            
            matched = False
            for term in bofu_terms:
                if term in kw_text:
                    bofu += 1
                    matched = True
                    break
            if not matched:
                for term in mofu_terms:
                    if term in kw_text:
                        mofu += 1
                        matched = True
                        break
            if not matched:
                for term in tofu_terms:
                    if term in kw_text:
                        tofu += 1
                        matched = True
                        break
            if not matched:
                tofu += 1  # Default: unclassified = informational/ToFu
        
        total_classified = tofu + mofu + bofu
        funnel_score = {
            'tofu': round((tofu / total_classified * 100) if total_classified else 0),
            'mofu': round((mofu / total_classified * 100) if total_classified else 0),
            'bofu': round((bofu / total_classified * 100) if total_classified else 0),
            'tofu_count': tofu,
            'mofu_count': mofu,
            'bofu_count': bofu,
            'total_classified': total_classified,
            # Funnel health: higher BoFu% = more conversion-ready
            'conversion_readiness': round((bofu / total_classified * 100) if total_classified else 0)
        }
        
        # --- Multi-Parameter Competitive Score (0-100) ---
        # 6 dimensions, each normalized to 0-100
        params = {
            'organic_visibility': min(round((traffic / max_traffic) * 100), 100),
            'keyword_portfolio': min(round((keywords / max_keywords) * 100), 100),
            'link_strength': min(round((backlinks / max_backlinks) * 100), 100),
            'domain_diversity': min(round((ref_domains / max_ref_domains) * 100), 100),
            'domain_authority': min(dr_norm, 100),
            'conversion_focus': funnel_score['conversion_readiness']
        }
        
        # Weighted composite
        weights = {
            'organic_visibility': 0.25,
            'keyword_portfolio': 0.20,
            'link_strength': 0.15,
            'domain_diversity': 0.15,
            'domain_authority': 0.15,
            'conversion_focus': 0.10
        }
        
        competitive_score = round(sum(params[k] * weights[k] for k in params))
        competitive_score = min(competitive_score, 100)
        
        scorecards.append({
            'domain': d['domain'],
            'is_client': d.get('is_client', False),
            'brand_authority': brand_authority,
            'funnel_score': funnel_score,
            'competitive_score': competitive_score,
            'parameter_breakdown': params,
            'raw_metrics': {
                'total_traffic': traffic,
                'total_keywords': keywords,
                'backlinks_total': backlinks,
                'referring_domains': ref_domains,
                'domain_rank': dr
            }
        })
    
    # Sort: client first, then by competitive_score descending
    scorecards.sort(key=lambda x: (not x['is_client'], -x['competitive_score']))
    
    return scorecards


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
            # ---- CHECK site_audits table first (Global Site Audit feature) ----
            try:
                site_audit_res = client.table('site_audits').select('*').eq('id', audit_id).single().execute()
                if site_audit_res.data:
                    sa = site_audit_res.data
                    sa_data = sa.get('audit_data', {}) or {}
                    domain = sa.get('domain', '')
                    logger.info(f"Found audit {audit_id} in site_audits table for domain {domain}")
                    return jsonify({
                        'success': True,
                        'project_id': None,
                        'audit_id': audit_id,
                        'domain': domain,
                        'data': {
                            'task_id': sa.get('task_id', ''),
                            'domain': domain,
                            'status': sa.get('status', 'completed'),
                            'created_at': sa.get('created_at', ''),
                            'organic_keywords': sa_data.get('organic_keywords', sa_data.get('keywords', [])),
                            'total_keywords': sa_data.get('total_keywords', 0),
                            'total_traffic': sa_data.get('total_traffic', 0),
                            'keywords_at_limit': sa_data.get('keywords_at_limit', False),
                            'backlinks_summary': sa_data.get('backlinks_summary', {}),
                            'referring_domains': sa_data.get('referring_domains', []),
                            'pages': sa_data.get('pages', []),
                            'pagespeed': sa_data.get('pagespeed', {}),
                            'max_pages': sa.get('max_pages', 50),
                            'crawl_summary': sa_data.get('crawl_summary', {}),
                            'page_issues': sa_data.get('page_issues', [])
                        }
                    })
            except Exception as sa_err:
                logger.debug(f"site_audits lookup failed for {audit_id}: {sa_err}")
            
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
        
        # Fetch the on-page audit results from DataForSEO (with retry for stability)
        from api.dataforseo_client import get_page_issues, get_audit_summary
        import time as _time
        
        # Retry helper for transient API failures
        def _retry_fetch(fn, *args, max_retries=3, **kwargs):
            for attempt in range(max_retries):
                try:
                    result = fn(*args, **kwargs)
                    if result and (isinstance(result, dict) and result.get('success', True)):
                        return result
                    if attempt < max_retries - 1:
                        logger.warning(f"Retry {attempt+1}/{max_retries} for {fn.__name__}: {result.get('error', 'empty result') if isinstance(result, dict) else 'None'}")
                        _time.sleep(5 * (attempt + 1))  # 5s, 10s backoff
                except Exception as e:
                    logger.warning(f"Retry {attempt+1}/{max_retries} for {fn.__name__}: {e}")
                    if attempt < max_retries - 1:
                        _time.sleep(5 * (attempt + 1))
            return result if result else {}
        
        summary_result = _retry_fetch(get_audit_summary, task_id)
        summary = summary_result.get('summary', {}) if summary_result.get('success') else {}
        
        pages_data = _retry_fetch(get_page_issues, task_id, limit=1000)  # Get up to 1000 pages
        pages = pages_data.get('pages', []) if pages_data.get('success') else []
        
        # Log data quality for debugging
        logger.info(f"Audit data fetched: summary_keys={list(summary.keys()) if summary else 'EMPTY'}, pages_count={len(pages)}")
        
        # Get existing audit/project data
        result = client.table('audits').select('*').eq('id', audit_id).execute()
        if not result.data:
            return jsonify({"error": "Audit not found"}), 404
        
        audit_record = result.data[0]
        audit_results = audit_record.get('results', {}) or {}
        
        # Get domain from audit data
        domain = audit_results.get('competitor_domain') or audit_record.get('campaign_id') # Will need to fetch campaign domain if missing
        campaign_location = 'US'  # Default; resolved below from campaign settings
        
        if not domain or str(domain).startswith(('http', 'ww', '1', '2', '3', 'u', 'd', 'e')): # Crude fast check
           try:
              c_res = client.table('campaigns').select('domain, settings').eq('id', audit_record.get('campaign_id')).execute()
              if c_res.data:
                 domain = c_res.data[0]['domain']
                 campaign_location = (c_res.data[0].get('settings') or {}).get('location', 'US')
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
                
                # Add delay to prevent Google API rate limits/timeouts
                import time
                time.sleep(5)

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
        
        # Fetch accurate domain-level traffic/keyword totals
        domain_totals = {}
        if domain:
            try:
                from api.dataforseo_client import fetch_domain_metrics, location_code_for
                audit_loc = location_code_for(campaign_location)
                domain_totals = fetch_domain_metrics(domain, location_code=audit_loc)
                if domain_totals.get('success'):
                    logger.info(f"Domain metrics: traffic={domain_totals.get('total_traffic')}, keywords={domain_totals.get('total_keywords')}, loc={campaign_location}")
            except Exception as e:
                logger.warning(f"Domain metrics error (non-fatal): {e}")
        
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
                
                # Merge accurate domain totals if available
                if domain_totals.get('success'):
                    project_data['total_traffic'] = domain_totals.get('total_traffic', project_data.get('total_traffic', 0))
                    project_data['total_keywords'] = domain_totals.get('total_keywords', project_data.get('total_keywords', 0))
                    project_data['top_3_keywords'] = domain_totals.get('top_3_keywords', 0)
                    project_data['top_10_keywords'] = domain_totals.get('top_10_keywords', 0)
                
                client.table('projects').update({
                    'full_audit_data': project_data
                }).eq('id', project['id']).execute()
                logger.info(f"Dual-write: updated project for audit {audit_id} with pages + pagespeed")
        except Exception as dual_err:
            logger.error(f"Dual-write update to projects failed (non-fatal): {dual_err}")
        # ---- END DUAL WRITE ----
        
        # ---- ENRICH audits.results with traffic/backlink data for slides fallback ----
        try:
            if domain_totals.get('success'):
                audit_results['total_traffic'] = domain_totals.get('total_traffic', 0)
                audit_results['total_keywords'] = domain_totals.get('total_keywords', 0)
            # Fetch domain_rank if not already in results (needed for slides position distribution)
            if not audit_results.get('domain_rank') and domain:
                from api.dataforseo_client import get_domain_rank_overview as _get_dro
                audit_results['domain_rank'] = _get_dro(domain)
            # Fetch backlinks_summary if missing
            if not audit_results.get('backlinks_summary') and domain:
                from api.dataforseo_client import fetch_backlinks_summary as _fetch_bl
                audit_results['backlinks_summary'] = _fetch_bl(domain)
            # Re-save enriched results
            client.table('audits').update({'results': audit_results}).eq('id', audit_id).execute()
            logger.info(f"Enriched audits.results with traffic/rank/backlinks data")
        except Exception as enrich_err:
            logger.error(f"Enrichment of audits.results failed (non-fatal): {enrich_err}")
        # ---- END ENRICHMENT ----
        
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


@app.route('/api/audit-links', methods=['GET'])
@login_required
def get_audit_links():
    """Fetch all crawled URLs from the CLIENT's own site audit (not competitor audits)."""
    try:
        project_id = request.args.get('project_id')
        if not project_id:
            return jsonify({"error": "project_id is required"}), 400
            
        client = supabase_admin or supabase
        
        # CRITICAL: Only fetch from 'technical' audits (the client's own site audit),
        # never from 'competitor' audits which belong to rival domains.
        # Also only return completed audits that actually have pages data.
        result = (client.table('audits')
                  .select('results')
                  .eq('campaign_id', project_id)
                  .eq('type', 'technical')
                  .eq('status', 'done')
                  .order('created_at', desc=True)
                  .limit(1)
                  .execute())
        
        if not result.data:
            # Fallback: try any non-competitor audit (could be 'crawling' status if just started)
            result = (client.table('audits')
                      .select('results')
                      .eq('campaign_id', project_id)
                      .neq('type', 'competitor')
                      .order('created_at', desc=True)
                      .limit(1)
                      .execute())
        
        if not result.data:
            return jsonify({"pages": []})
            
        audit_results = result.data[0].get('results', {}) or {}
        pages = audit_results.get('pages', [])
        
        return jsonify({"pages": pages})
    except Exception as e:
        logger.error(f"Error fetching audit links: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/assign-page-type', methods=['POST'])
@login_required
def assign_page_type():
    """Manually assign a page_type (BOFU/MOFU/TOFU) to a URL, adding it to the pages table if needed."""
    try:
        data = request.json
        project_id = data.get('project_id')
        url = data.get('url')
        page_type = data.get('page_type')
        tech_data = data.get('tech_audit_data', {})
        
        if not project_id or not url or not page_type:
            return jsonify({"error": "project_id, url, and page_type are required"}), 400
            
        client = supabase_admin or supabase
        
        # Check if the page already exists in the pages table
        existing_res = client.table('pages').select('id, page_type').eq('project_id', project_id).eq('url', url).execute()
        
        # Note: We need to map to frontend concepts
        db_page_type = 'topic'
        db_funnel_stage = ''
        
        if page_type.lower() == 'bofu':
            db_page_type = 'product'
            db_funnel_stage = 'BoFu'
        elif page_type.lower() == 'mofu':
            db_page_type = 'topic'
            db_funnel_stage = 'MoFu'
        elif page_type.lower() == 'tofu':
            db_page_type = 'topic'
            db_funnel_stage = 'ToFu'
            
        if existing_res.data:
            # Update existing page
            page_id = existing_res.data[0]['id']
            client.table('pages').update({
                'page_type': db_page_type,
                'funnel_stage': db_funnel_stage,
                'tech_audit_data': tech_data
            }).eq('id', page_id).execute()
        else:
            # Insert new page
            client.table('pages').insert({
                'project_id': project_id,
                'url': url,
                'page_type': db_page_type,
                'funnel_stage': db_funnel_stage,
                'tech_audit_data': tech_data,
                'content_description': 'Manually assigned from All Links'
            }).execute()
            
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error assigning page type: {e}")
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
                    # Try site_audits table first for global site audits
                    site_audit_res = client.table('site_audits').select('*').eq('id', audit_id).execute()
                    if site_audit_res.data:
                        sa = site_audit_res.data[0]
                        sa_data = sa.get('audit_data', {}) or {}
                        domain = sa.get('domain', 'unknown')
                        
                        audit_data = {
                            **sa_data,
                            'domain': domain,
                            'organic_keywords': sa_data.get('organic_keywords', sa_data.get('keywords', [])),
                            'backlinks_summary': sa_data.get('backlinks_summary', {}),
                            'referring_domains': sa_data.get('referring_domains', [])
                        }
                        logger.info(f"Slides: loaded data from site_audits table for audit {audit_id}")
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
        
        # Fetch pagespeed on-the-fly if missing from audit data
        if not audit_data.get('pagespeed') and domain and domain != 'unknown' and domain != 'Website':
            try:
                from execution.pagespeed_insights import fetch_pagespeed_scores
                import time as _time
                ps_data = {}
                mobile = fetch_pagespeed_scores(f"https://{domain}", strategy="mobile")
                if mobile and mobile.get('success') is not False:
                    ps_data['mobile'] = {'scores': mobile.get('scores', {}), 'metrics': mobile.get('metrics', {})}
                    ps_data['scores'] = mobile.get('scores', {})
                    ps_data['metrics'] = mobile.get('metrics', {})
                _time.sleep(3)
                desktop = fetch_pagespeed_scores(f"https://{domain}", strategy="desktop")
                if desktop and desktop.get('success') is not False:
                    ps_data['desktop'] = {'scores': desktop.get('scores', {}), 'metrics': desktop.get('metrics', {})}
                if ps_data:
                    audit_data['pagespeed'] = ps_data
                    logger.info(f"Slides: fetched pagespeed on-the-fly for {domain}")
            except Exception as ps_err:
                logger.warning(f"Slides: on-the-fly pagespeed failed: {ps_err}")
        
        # Fetch domain metrics on-the-fly if missing (for ranking opportunities in slides)
        if not audit_data.get('total_traffic') and not audit_data.get('domain_metrics') and domain and domain != 'unknown' and domain != 'Website':
            try:
                from api.dataforseo_client import fetch_domain_metrics
                dm = fetch_domain_metrics(domain)
                if dm and dm.get('success'):
                    audit_data['total_traffic'] = dm.get('total_traffic', 0)
                    audit_data['total_keywords'] = dm.get('total_keywords', 0)
                    audit_data['domain_metrics'] = dm
                    logger.info(f"Slides: fetched domain metrics on-the-fly for {domain}")
            except Exception as dm_err:
                logger.warning(f"Slides: on-the-fly domain metrics failed: {dm_err}")
        
        # Get Google credentials
        creds = get_google_credentials()
        if not creds:
            return jsonify({"error": "Google credentials not available"}), 500
        
        # Upload screenshots to Supabase Storage if present
        # NOTE: We do NOT auto-fetch screenshots via DataForSEO anymore.
        # Only process screenshots that the frontend explicitly sends.
        processed_screenshots = {}
        try:
            if not isinstance(screenshots, dict):
                screenshots = {}
            
            # No automatic screenshot fetching — only use what frontend sent
            logger.info(f"Slides: processing {len(screenshots)} screenshots from frontend (no auto-fetch)")

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

# ==========================================
# SITE AUDIT (GLOBAL) — Full-site audit without project
# ==========================================

@app.route('/api/site-audit/create', methods=['POST'])
@login_required
def site_audit_create():
    """Start a full-site audit with DataForSEO. Accepts domain + max_pages directly (no project needed)."""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    data = request.json
    domain = data.get('domain', '').strip()
    try:
        max_pages = int(data.get('max_pages', 50))
    except (ValueError, TypeError):
        max_pages = 50

    if not domain:
        return jsonify({"success": False, "error": "domain is required"}), 400

    domain = domain.replace('https://', '').replace('http://', '').strip('/')
    domain = domain.split('/')[0]

    client = supabase_admin or supabase

    # Start DataForSEO crawl
    dfs_result = start_onpage_audit(domain, max_pages)
    if not dfs_result or not dfs_result.get('success'):
        return jsonify(dfs_result or {"error": "Audit start failed"}), 500

    task_id = dfs_result.get('task_id')

    # Fetch keywords + backlinks in parallel (non-blocking enrichment)
    keywords, backlinks_summary_data, referring_domains_data = [], {}, []
    total_keywords, total_traffic = 0, 0
    try:
        keywords_data = fetch_ranked_keywords(domain)
        keywords = keywords_data.get('keywords', []) if isinstance(keywords_data, dict) else []
        total_keywords = keywords_data.get('total_count', len(keywords)) if isinstance(keywords_data, dict) else 0
        total_traffic = keywords_data.get('estimated_traffic', 0) if isinstance(keywords_data, dict) else 0
    except Exception as e:
        logger.warning(f"[site-audit] Keywords fetch failed (non-fatal): {e}")

    try:
        backlinks_summary_data = fetch_backlinks_summary(domain)
    except Exception as e:
        logger.warning(f"[site-audit] Backlinks fetch failed (non-fatal): {e}")

    try:
        referring_domains_data = get_referring_domains(domain)
    except Exception as e:
        logger.warning(f"[site-audit] Referring domains fetch failed (non-fatal): {e}")

    # Create a site_audits record in Supabase
    audit_record = {
        'domain': domain,
        'max_pages': max_pages,
        'task_id': task_id,
        'status': 'crawling',
        'created_at': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        'audit_data': {
            'task_id': task_id,
            'domain': domain,
            'organic_keywords': keywords[:200],
            'total_keywords': total_keywords,
            'total_traffic': total_traffic,
            'backlinks_summary': backlinks_summary_data,
            'referring_domains': referring_domains_data[:100] if isinstance(referring_domains_data, list) else [],
            'max_pages': max_pages
        }
    }

    audit_id = None
    try:
        res = client.table('site_audits').insert(audit_record).execute()
        audit_id = res.data[0]['id'] if res.data else None
    except Exception as e:
        logger.error(f"[site-audit] DB insert failed: {e}")

    return jsonify({
        "success": True,
        "task_id": task_id,
        "audit_id": audit_id,
        "domain": domain,
        "max_pages": max_pages,
        "message": f"Audit started for {domain} ({max_pages} pages)"
    })


@app.route('/api/site-audit/status/<task_id>', methods=['GET'])
@login_required
def site_audit_status(task_id):
    """Check DataForSEO crawl status."""
    try:
        status = get_audit_status(task_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/site-audit/save-results', methods=['POST'])
@login_required
def site_audit_save_results():
    """Save completed audit results (summary + pages) to the site_audits record."""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    data = request.json
    audit_id = data.get('audit_id')
    task_id = data.get('task_id')

    if not audit_id or not task_id:
        return jsonify({"error": "audit_id and task_id required"}), 400

    client = supabase_admin or supabase

    try:
        summary = get_audit_summary(task_id)
        pages_result = get_page_issues(task_id, limit=1000)
        pages = pages_result.get('pages', [])

        existing = client.table('site_audits').select('audit_data, domain').eq('id', audit_id).execute()
        audit_data = existing.data[0].get('audit_data', {}) if existing.data else {}
        domain = existing.data[0].get('domain', '') if existing.data else ''

        audit_data['summary'] = summary.get('summary', {})
        audit_data['pages'] = pages
        audit_data['status'] = 'completed'
        
        # --- Fetch PageSpeed Insights (mobile + desktop) ---
        if domain:
            try:
                from execution.pagespeed_insights import fetch_pagespeed_scores
                import time as _time
                pagespeed = {}
                
                mobile_result = fetch_pagespeed_scores(f"https://{domain}", strategy="mobile")
                if mobile_result and mobile_result.get('success') is not False:
                    pagespeed['mobile'] = {
                        'scores': mobile_result.get('scores', {}),
                        'metrics': mobile_result.get('metrics', {})
                    }
                    pagespeed['scores'] = mobile_result.get('scores', {})
                    pagespeed['metrics'] = mobile_result.get('metrics', {})
                
                _time.sleep(3)  # Avoid Google rate limit
                
                desktop_result = fetch_pagespeed_scores(f"https://{domain}", strategy="desktop")
                if desktop_result and desktop_result.get('success') is not False:
                    pagespeed['desktop'] = {
                        'scores': desktop_result.get('scores', {}),
                        'metrics': desktop_result.get('metrics', {})
                    }
                
                if pagespeed:
                    audit_data['pagespeed'] = pagespeed
                    logger.info(f"[site-audit] Fetched PageSpeed for {domain}: mobile={bool(pagespeed.get('mobile'))}, desktop={bool(pagespeed.get('desktop'))}")
            except Exception as ps_err:
                logger.warning(f"[site-audit] PageSpeed fetch failed (non-fatal): {ps_err}")
        
        # --- Fetch domain metrics for ranking opportunities ---
        if domain:
            try:
                from api.dataforseo_client import fetch_domain_metrics
                domain_metrics = fetch_domain_metrics(domain)
                if domain_metrics and domain_metrics.get('success'):
                    audit_data['domain_metrics'] = domain_metrics
                    audit_data['total_traffic'] = domain_metrics.get('total_traffic', audit_data.get('total_traffic', 0))
                    audit_data['total_keywords'] = domain_metrics.get('total_keywords', audit_data.get('total_keywords', 0))
                    logger.info(f"[site-audit] Domain metrics: traffic={domain_metrics.get('total_traffic')}, keywords={domain_metrics.get('total_keywords')}")
            except Exception as dm_err:
                logger.warning(f"[site-audit] Domain metrics fetch failed (non-fatal): {dm_err}")

        client.table('site_audits').update({
            'status': 'completed',
            'audit_data': audit_data
        }).eq('id', audit_id).execute()

        return jsonify({
            "success": True,
            "pages_count": len(pages),
            "summary": summary.get('summary', {})
        })
    except Exception as e:
        logger.error(f"[site-audit] Save results error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/site-audit/list', methods=['GET'])
@login_required
def site_audit_list():
    """List all site audits."""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    client = supabase_admin or supabase

    try:
        res = client.table('site_audits').select('id, domain, max_pages, status, task_id, created_at, slides_url').order('created_at', desc=True).limit(50).execute()
        return jsonify({"success": True, "audits": res.data or []})
    except Exception as e:
        logger.error(f"[site-audit] List error: {e}")
        return jsonify({"error": str(e)}), 500

# ==========================================
# END SITE AUDIT (GLOBAL)
# ==========================================

@app.route('/api/audit/<audit_id>/readability', methods=['GET'])
@login_required
def analyze_readability(audit_id):
    """Analyze content readability for audit pages"""
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        client = supabase_admin or supabase
        result = client.table('audits').select('*').eq('id', audit_id).execute()
        
        audit_data = {}
        is_site_audit = False
        
        if not result.data:
            # Try site_audits table
            sa_result = client.table('site_audits').select('*').eq('id', audit_id).execute()
            if not sa_result.data:
                return jsonify({"success": False, "error": "Audit not found"}), 404
            audit_record = sa_result.data[0]
            audit_data = audit_record.get('audit_data', {}) or {}
            is_site_audit = True
        else:
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
            
        blacklist = ['/collections', '/products', '/cart', '/checkout', '/account', '/search', '/policies/']
        blog_keywords = ['/blog', '/blogs', '/article', '/post', '/news', '/insight', '/guide', '202', '/journal', '/pages/']
            
        for page in pages:
            url = page.get('url', '')
            traffic = page.get('traffic', 0)
            if not url or is_homepage(url): continue
            if any(item in url.lower() for item in blacklist): continue
            is_blog = any(keyword in url.lower() for keyword in blog_keywords)
            candidates.append({'url': url, 'traffic': traffic, 'is_blog': is_blog})
            
        # Also extract URLs from top organic keywords since the crawler might have missed blogs in its 20-page limit
        keywords = audit_data.get('organic_keywords', [])
        kw_urls = set()
        for kw in keywords:
            try:
                serp_url = kw.get('ranked_serp_element', {}).get('serp_item', {}).get('url')
                if serp_url and serp_url not in kw_urls:
                    kw_urls.add(serp_url)
                    if is_homepage(serp_url) or any(item in serp_url.lower() for item in blacklist): continue
                    is_blog = any(keyword in serp_url.lower() for keyword in blog_keywords)
                    # We don't have exact page traffic here easily, so we just use 0, but is_blog will prioritize them
                    candidates.append({'url': serp_url, 'traffic': 0, 'is_blog': is_blog})
            except Exception:
                pass
                
        # Deduplicate candidates by URL while keeping highest traffic/is_blog
        unique_candidates = {}
        for c in candidates:
            url = c['url']
            if url not in unique_candidates:
                unique_candidates[url] = c
            else:
                if c['is_blog'] and not unique_candidates[url]['is_blog']:
                    unique_candidates[url]['is_blog'] = True
                if c['traffic'] > unique_candidates[url]['traffic']:
                    unique_candidates[url]['traffic'] = c['traffic']
                    
        candidates = list(unique_candidates.values())
        candidates.sort(key=lambda x: (x['is_blog'], x['traffic']), reverse=True)
        top_candidates = [c['url'] for c in candidates[:5]]
        
        if not top_candidates:
            urls = [p.get('url') for p in pages if p.get('url')]
            urls.extend(list(kw_urls))
            
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
            if is_site_audit:
                audit_data['readability_results'] = readability_results
                client.table('site_audits').update({'audit_data': audit_data}).eq('id', audit_id).execute()
            else:
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
        
        audit_res = client.table('audits').select('results, campaign_id, campaigns(domain)').eq('id', audit_id).execute()
        
        is_site_audit = False
        domain = ""
        
        if not audit_res.data:
            sa_result = client.table('site_audits').select('*').eq('id', audit_id).execute()
            if not sa_result.data:
                return jsonify({'error': 'Audit not found'}), 404
            record = sa_result.data[0]
            results = record.get('audit_data', {}) or {}
            domain = record.get('domain', '')
            is_site_audit = True
        else:
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
            pagespeed['mobile'] = {
                'scores': mobile.get('scores', {}), 
                'metrics': mobile.get('metrics', {}),
                'strategy': 'mobile',
                'url': f"https://{domain}"
            }
            # Keep flat keys for backward compat (default = mobile)
            pagespeed['scores'] = mobile.get('scores', {})
            pagespeed['metrics'] = mobile.get('metrics', {})
        
        # Add delay to prevent rate limits
        import time
        time.sleep(5)

        desktop = fetch_pagespeed_scores(f"https://{domain}", strategy="desktop")
        if desktop and desktop.get('success'):
            pagespeed['desktop'] = {
                'scores': desktop.get('scores', {}), 
                'metrics': desktop.get('metrics', {}),
                'strategy': 'desktop',
                'url': f"https://{domain}"
            }
        
        if not pagespeed:
            return jsonify({'error': 'PageSpeed fetch failed'}), 500
        
        # Save to correct table
        if is_site_audit:
            results['pagespeed'] = pagespeed
            client.table('site_audits').update({'audit_data': results}).eq('id', audit_id).execute()
        else:
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
        
        logger.info(f"PageSpeed refreshed for {domain}: mobile={pagespeed.get('mobile', {}).get('scores', {}).get('performance', 'N/A')}, desktop={pagespeed.get('desktop', {}).get('scores', {}).get('performance', 'N/A')}")
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
        
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=150)
            response.raise_for_status()
            result = response.json()
            
            # Extract text carefully to catch safety blocks or empty candidates
            if not result.get('candidates'):
                raise Exception(f"Gemini API returned no candidates. Raw response: {result}")
                
            candidate = result['candidates'][0]
            if 'content' not in candidate or 'parts' not in candidate['content']:
                finish_reason = candidate.get('finishReason', 'UNKNOWN')
                raise Exception(f"Gemini API blocked generation. Finish Reason: {finish_reason}. Raw response: {result}")
                
            try:
                text = candidate['content']['parts'][0]['text']
                print(f"DEBUG: REST API Success. Text length: {len(text)}", flush=True)
                return text
            except (KeyError, IndexError) as e:
                print(f"DEBUG: Unexpected REST response structure: {result}", flush=True)
                raise Exception(f"Unexpected response structure: {result}")
                
        except requests.exceptions.ReadTimeout as e:
            print(f"DEBUG: Gemini API Read Timed Out on attempt {attempt + 1}: {e}", flush=True)
            if attempt < max_retries - 1:
                print("DEBUG: Retrying on Timeout...", flush=True)
                # Wait 5 seconds before retrying a timeout
                import time
                time.sleep(5)
                continue
            raise e
            
        except Exception as e:
            print(f"DEBUG: REST API call failed on attempt {attempt + 1}: {e}")
            if 'response' in locals() and response is not None:
                print(f"DEBUG: Response content: {response.text}", flush=True)
                if response.status_code == 429 and attempt < max_retries - 1:
                    import time
                    sleep_time = (2 ** attempt) + 1  # Exponential backoff: 2s, 3s, ...
                    print(f"DEBUG: 429 Rate Limit Hit. Waiting {sleep_time} seconds before retry...", flush=True)
                    time.sleep(sleep_time)
                    continue  # Retry
            
            if attempt == max_retries - 1:
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
            use_grounding=True  # Grounded analysis for SEO
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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

@app.route('/api/publish-wordpress', methods=['POST'])
@login_required
def publish_wordpress():
    import requests
    from requests.auth import HTTPBasicAuth
    try:
        import markdown
        has_markdown = True
    except ImportError:
        has_markdown = False
    
    data = request.json
    page_id = data.get('page_id')
    source_type = data.get('source_type', 'page')
    wp_url = data.get('wp_url')
    wp_username = data.get('wp_username')
    wp_app_password = data.get('wp_app_password')
    
    if not all([page_id, wp_url, wp_username, wp_app_password]):
        return jsonify({"error": "Missing required fields"}), 400
        
    try:
        html_content = ""
        title = ""
        
        if source_type == 'piece':
            piece_res = (supabase_admin or supabase).table('content_pieces').select('*').eq('id', page_id).single().execute()
            if not piece_res.data:
                return jsonify({"error": "Piece not found"}), 404
            piece = piece_res.data
            html_content = piece.get('draft_html', '')
            if not html_content:
                return jsonify({"error": "No draft HTML generated yet for this piece"}), 400
            title = piece.get('title') or 'Untitled Piece'
        else:
            # Fetch page for content
            page_res = (supabase_admin or supabase).table('pages').select('*').eq('id', page_id).single().execute()
            if not page_res.data:
                return jsonify({"error": "Page not found"}), 404
            page = page_res.data
            
            # Get raw markdown content
            content_md = page.get('content', '')
            if not content_md:
                return jsonify({"error": "No content generated yet for this page"}), 400
                
            # Optional: Append main image if it exists
            main_image = page.get('main_image_url')
            if main_image:
                image_tag = f"![Main Image]({main_image})\n\n"
                content_md = image_tag + content_md
                
            # Convert Markdown to HTML (with fallback)
            if has_markdown:
                html_content = markdown.markdown(content_md, extensions=['tables', 'fenced_code'])
            else:
                # Simple fallback: wrap in paragraphs
                html_content = ''.join(f'<p>{line}</p>' for line in content_md.split('\n') if line.strip())
            
            # Determine Title
            tech_data = page.get('tech_audit_data') or {}
            title = tech_data.get('title') or page.get('url') or 'Untitled generated post'
        
        # 1. Structure WordPress API endpoint
        wp_api_base = wp_url.rstrip('/ ')
        post_url = f"{wp_api_base}/wp-json/wp/v2/posts"
        
        # 2. Build WordPress Payload
        post_data = {
            "title": title,
            "content": html_content,
            "status": "draft",
            "format": "standard"
        }
        
        # 3. Send POST to WordPress with Basic Auth (Application Passwords)
        auth = HTTPBasicAuth(wp_username, wp_app_password)
        response = requests.post(post_url, auth=auth, json=post_data, timeout=30)
        
        if response.status_code in [200, 201]:
            resp_data = response.json()
            return jsonify({
                "message": "Successfully published draft to WordPress",
                "link": resp_data.get('link', '')
            })
        else:
            # Truncate response.text to avoid returning HTML
            error_text = response.text[:500] if response.text else 'Unknown error'
            logger.error(f"WordPress API error ({response.status_code}): {error_text}")
            return jsonify({
                "error": f"WordPress API Error ({response.status_code}): {error_text}"
            }), 500
            
    except Exception as e:
        print(f"Error publishing to WordPress: {e}")
        return jsonify({"error": str(e)}), 500


# --- generate_blog_image (L6243-6285) ---
@app.route('/api/generate-blog-image', methods=['POST'])
@login_required
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
@login_required
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
@login_required
def publish_webflow_legacy():
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
@login_required
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
        
        issues = get_page_issues(audit.get('dataforseo_task_id'), limit=1000)
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

# =============================================================================
# WINS OF THE WEEK
# =============================================================================

@app.route('/api/wins', methods=['POST'])
@login_required
def get_wins():
    """
    Aggregates 'wins' for the client portal — ranking improvements,
    traffic gains, and completed tasks. Accessible by all roles including viewer.
    """
    import traceback
    from datetime import timedelta

    user = session['user']
    data = request.json or {}
    campaign_id = data.get('campaign_id')
    duration = data.get('duration', '7d')  # 7d, 14d, 30d

    if not campaign_id:
        return jsonify({'error': 'campaign_id is required'}), 400

    # Scope check: viewers can only see their assigned campaigns
    user_role = user.get('role', 'viewer')
    if user_role == 'viewer':
        assigned = user.get('assigned_campaigns', [])
        if assigned and campaign_id not in assigned:
            return jsonify({'error': 'Not authorized'}), 403

    client = supabase_admin or supabase

    duration_days = {'7d': 7, '14d': 14, '30d': 30, '90d': 90}.get(duration, 7)
    now = datetime.utcnow()
    cutoff = (now - timedelta(days=duration_days)).isoformat()

    wins = {
        'tasks': {'completed': 0, 'total': 0, 'items': []},
        'rankings': {'improved': 0, 'top3': 0, 'top10': 0, 'items': []},
        'traffic': {'clicks_current': 0, 'clicks_prev': 0, 'impressions_current': 0, 'impressions_prev': 0},
        'highlights': [],
        'duration': duration,
        'duration_days': duration_days
    }

    try:
        # -----------------------------------------------------------------
        # 1. TASK WINS — completed tasks in the period
        # -----------------------------------------------------------------
        try:
            tasks_res = client.table('tasks').select('*').eq('campaign_id', campaign_id).execute()
            all_tasks = tasks_res.data or []
            wins['tasks']['total'] = len(all_tasks)

            completed = [t for t in all_tasks if t.get('status') == 'done']
            # Filter to recently completed (updated_at within cutoff)
            recent_completed = []
            for t in completed:
                updated = t.get('updated_at') or t.get('created_at') or ''
                if updated >= cutoff:
                    recent_completed.append(t)

            wins['tasks']['completed'] = len(recent_completed)
            wins['tasks']['items'] = [
                {
                    'title': t.get('title', 'Untitled'),
                    'type': t.get('type', 'general'),
                    'assigned_role': t.get('assigned_role', ''),
                    'completed_at': t.get('updated_at', '')
                }
                for t in recent_completed[:20]
            ]

            if len(recent_completed) > 0:
                wins['highlights'].append({
                    'type': 'tasks',
                    'icon': 'check-circle',
                    'color': 'emerald',
                    'title': f"{len(recent_completed)} Tasks Completed",
                    'subtitle': f"In the last {duration_days} days"
                })
        except Exception as e:
            logger.warning(f"Wins: Task fetch failed: {e}")

        # -----------------------------------------------------------------
        # 2. RANKING WINS — GSC query comparison (current vs previous)
        # -----------------------------------------------------------------
        try:
            # Get campaign details for GSC property
            campaign_res = client.table('campaigns').select('settings, tracked_keywords').eq('id', campaign_id).single().execute()
            campaign_data = campaign_res.data or {}
            settings = campaign_data.get('settings') or {}
            gsc_property = settings.get('gsc_property')
            tracked_keywords = campaign_data.get('tracked_keywords') or []

            if gsc_property and tracked_keywords:
                # Get Google integration credentials
                org_id = user.get('organization_id')
                integration = client.table('agency_integrations').select('*').eq('organization_id', org_id).eq('provider', 'google').execute()

                if integration.data:
                    from google.oauth2.credentials import Credentials
                    from googleapiclient.discovery import build as google_build
                    from api.google_integration import get_client_config

                    refresh_token = integration.data[0].get('refresh_token')
                    if refresh_token:
                        client_config = get_client_config()
                        creds = Credentials(
                            None,
                            refresh_token=refresh_token,
                            client_id=client_config['web']['client_id'],
                            client_secret=client_config['web']['client_secret'],
                            token_uri=client_config['web']['token_uri']
                        )

                        gsc_service = google_build('searchconsole', 'v1', credentials=creds)

                        today = now.date()
                        current_start = (today - timedelta(days=duration_days)).isoformat()
                        current_end = today.isoformat()
                        prev_start = (today - timedelta(days=duration_days * 2)).isoformat()
                        prev_end = (today - timedelta(days=duration_days)).isoformat()

                        def _gsc_query(start, end):
                            body = {
                                'startDate': start,
                                'endDate': end,
                                'dimensions': ['query'],
                                'rowLimit': 5000
                            }
                            resp = gsc_service.searchanalytics().query(siteUrl=gsc_property, body=body).execute()
                            return resp.get('rows', [])

                        current_rows = _gsc_query(current_start, current_end)
                        prev_rows = _gsc_query(prev_start, prev_end)

                        # Build lookup maps
                        current_map = {}
                        for r in current_rows:
                            q = r['keys'][0]
                            current_map[q] = {
                                'position': r['position'],
                                'clicks': r['clicks'],
                                'impressions': r['impressions'],
                                'ctr': r['ctr']
                            }

                        prev_map = {}
                        for r in prev_rows:
                            q = r['keys'][0]
                            prev_map[q] = {
                                'position': r['position'],
                                'clicks': r['clicks'],
                                'impressions': r['impressions'],
                                'ctr': r['ctr']
                            }

                        # Calculate traffic totals
                        wins['traffic']['clicks_current'] = sum(r['clicks'] for r in current_rows)
                        wins['traffic']['impressions_current'] = sum(r['impressions'] for r in current_rows)
                        wins['traffic']['clicks_prev'] = sum(r['clicks'] for r in prev_rows)
                        wins['traffic']['impressions_prev'] = sum(r['impressions'] for r in prev_rows)

                        # Ranking improvements for tracked keywords
                        ranking_items = []
                        for kw in tracked_keywords:
                            curr = current_map.get(kw)
                            prev = prev_map.get(kw)
                            curr_pos = round(curr['position'], 1) if curr else None
                            prev_pos = round(prev['position'], 1) if prev else None
                            
                            if curr_pos and prev_pos:
                                improvement = round(prev_pos - curr_pos, 1)
                            elif curr_pos and not prev_pos:
                                improvement = 0 # New ranking
                            else:
                                improvement = 0 # Dropped out or no data

                            item = {
                                'keyword': kw,
                                'current_position': curr_pos or 999, # Sort to bottom if no data
                                'previous_position': prev_pos,
                                'improvement': improvement,
                                'clicks': curr['clicks'] if curr else 0,
                                'impressions': curr['impressions'] if curr else 0,
                                'has_data': bool(curr)
                            }
                            ranking_items.append(item)

                            if curr_pos:
                                if curr_pos <= 3:
                                    wins['rankings']['top3'] += 1
                                if curr_pos <= 10:
                                    wins['rankings']['top10'] += 1
                                if improvement > 0:
                                    wins['rankings']['improved'] += 1

                        # Sort by improvement descending
                        ranking_items.sort(key=lambda x: x['improvement'], reverse=True)
                        wins['rankings']['items'] = ranking_items

                        # Generate highlights
                        if wins['rankings']['improved'] > 0:
                            best = ranking_items[0] if ranking_items else None
                            wins['highlights'].append({
                                'type': 'rankings',
                                'icon': 'trending-up',
                                'color': 'violet',
                                'title': f"{wins['rankings']['improved']} Keywords Improved",
                                'subtitle': f"Best: '{best['keyword']}' moved up {best['improvement']} positions" if best else ''
                            })

                        if wins['rankings']['top3'] > 0:
                            wins['highlights'].append({
                                'type': 'top3',
                                'icon': 'trophy',
                                'color': 'amber',
                                'title': f"{wins['rankings']['top3']} Keywords in Top 3",
                                'subtitle': 'First page dominance'
                            })

                        click_delta = wins['traffic']['clicks_current'] - wins['traffic']['clicks_prev']
                        if click_delta > 0:
                            pct = round((click_delta / max(wins['traffic']['clicks_prev'], 1)) * 100, 1)
                            wins['highlights'].append({
                                'type': 'traffic',
                                'icon': 'mouse-pointer-click',
                                'color': 'blue',
                                'title': f"+{click_delta:,} More Clicks",
                                'subtitle': f"{pct}% increase vs previous period"
                            })

        except Exception as e:
            logger.warning(f"Wins: Ranking fetch failed: {e}")

        return jsonify(wins)

    except Exception as e:
        logger.error(f"Wins endpoint error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# CAMPAIGN MANAGER DASHBOARD
# =============================================================================

@app.route('/api/cm-dashboard', methods=['GET'])
@login_required
def cm_dashboard():
    """Aggregate data for the Campaign Manager overview dashboard."""
    user = session['user']
    org_id = user.get('organization_id')
    user_role = user.get('role', 'viewer')
    if not org_id:
        return jsonify({'error': 'No organization'}), 400

    client = supabase_admin or supabase
    try:
        # Fresh fetch of assigned campaigns to prevent stale session issues
        profile_res = client.table('profiles').select('assigned_campaigns').eq('id', user['id']).execute()
        assigned = profile_res.data[0].get('assigned_campaigns', []) if profile_res.data else []

        # 1. All campaigns in the org
        campaigns_query = client.table('campaigns').select('id, name, domain, status, created_at').eq('organization_id', org_id)
        campaigns_res = campaigns_query.order('created_at', desc=True).execute()
        campaigns = campaigns_res.data or []

        # Filter by assigned campaigns for non-admin roles
        if user_role != 'admin':
            if assigned:
                campaigns = [c for c in campaigns if str(c['id']) in assigned]
            else:
                campaigns = []

        campaign_ids = [c['id'] for c in campaigns]
        active_campaigns = [c for c in campaigns if c.get('status') == 'active']
        inactive_campaigns = [c for c in campaigns if c.get('status') != 'active']

        # 2. All tasks across these campaigns
        all_tasks = []
        if campaign_ids:
            tasks_res = client.table('tasks').select('id, title, type, status, assigned_to, assigned_role, campaign_id, created_at, updated_at').in_('campaign_id', campaign_ids).execute()
            all_tasks = tasks_res.data or []

        pending = [t for t in all_tasks if t.get('status') == 'pending']
        in_progress = [t for t in all_tasks if t.get('status') == 'in_progress']
        done = [t for t in all_tasks if t.get('status') == 'done']

        # 3. Team members
        team_res = client.table('profiles').select('id, full_name, email, role').eq('organization_id', org_id).execute()
        team = team_res.data or []

        # 4. Workload: tasks per member
        workload = {}
        for t in all_tasks:
            assignee = t.get('assigned_to') or 'Unassigned'
            if assignee not in workload:
                workload[assignee] = {'pending': 0, 'in_progress': 0, 'done': 0, 'total': 0}
            workload[assignee]['total'] += 1
            s = t.get('status', 'pending')
            if s in workload[assignee]:
                workload[assignee][s] += 1

        # Map IDs to names
        id_name = {m['id']: m.get('full_name') or m.get('email', 'Unknown') for m in team}
        team_workload = []
        for uid, stats in workload.items():
            team_workload.append({
                'id': uid,
                'name': id_name.get(uid, uid if uid != 'Unassigned' else 'Unassigned'),
                'role': next((m['role'] for m in team if m['id'] == uid), ''),
                **stats
            })
        team_workload.sort(key=lambda x: x['total'], reverse=True)

        # 5. Per-campaign breakdown
        campaign_health = []
        for c in campaigns:
            ct = [t for t in all_tasks if t.get('campaign_id') == c['id']]
            total = len(ct)
            d = len([t for t in ct if t.get('status') == 'done'])
            campaign_health.append({
                'id': c['id'],
                'name': c['name'],
                'domain': c.get('domain', ''),
                'status': c.get('status', 'active'),
                'total_tasks': total,
                'done_tasks': d,
                'progress': round((d / total) * 100) if total > 0 else 0
            })

        # 6. Recent activity (last 10 updated tasks)
        recent = sorted(all_tasks, key=lambda t: t.get('updated_at', ''), reverse=True)[:10]
        recent_activity = [{
            'title': t.get('title', 'Untitled'),
            'status': t.get('status', 'pending'),
            'type': t.get('type', ''),
            'campaign_id': t.get('campaign_id', ''),
            'campaign_name': next((c['name'] for c in campaigns if c['id'] == t.get('campaign_id')), 'Unknown'),
            'updated_at': t.get('updated_at', '')
        } for t in recent]

        return jsonify({
            'success': True,
            'stats': {
                'total_campaigns': len(campaigns),
                'active_campaigns': len(active_campaigns),
                'inactive_campaigns': len(inactive_campaigns),
                'total_tasks': len(all_tasks),
                'pending_tasks': len(pending),
                'in_progress_tasks': len(in_progress),
                'completed_tasks': len(done),
                'team_size': len(team)
            },
            'team_workload': team_workload,
            'campaign_health': campaign_health,
            'recent_activity': recent_activity
        })
    except Exception as e:
        logger.error(f"CM dashboard error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# PHASE 3: CONTENT PIECES — Full Lifecycle CRUD
# =============================================================================

@app.route('/api/content-pieces', methods=['GET'])
@login_required
def list_content_pieces():
    """List content pieces for a campaign with optional status filter."""
    campaign_id = request.args.get('campaign_id')
    status_filter = request.args.get('status')  # brief, draft, review, revision, approved, published
    
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400
    
    db = supabase_admin or supabase
    
    try:
        query = db.table('content_pieces').select('*').eq('campaign_id', campaign_id)
        if status_filter:
            query = query.eq('status', status_filter)
        
        result = query.order('updated_at', desc=True).limit(100).execute()
        pieces = result.data or []
        
        # Summary stats
        all_res = db.table('content_pieces').select('id, status').eq('campaign_id', campaign_id).execute()
        all_pieces = all_res.data or []
        stats = {
            'total': len(all_pieces),
            'brief': len([p for p in all_pieces if p['status'] == 'brief']),
            'draft': len([p for p in all_pieces if p['status'] == 'draft']),
            'review': len([p for p in all_pieces if p['status'] == 'review']),
            'revision': len([p for p in all_pieces if p['status'] == 'revision']),
            'approved': len([p for p in all_pieces if p['status'] == 'approved']),
            'published': len([p for p in all_pieces if p['status'] == 'published'])
        }
        
        return jsonify({'success': True, 'pieces': pieces, 'stats': stats})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/content-pieces', methods=['POST'])
@login_required
def create_content_piece():
    """Create a new content piece (from brief, manual entry, or AI generation)."""
    data = request.json or {}
    campaign_id = data.get('campaign_id')
    
    if not campaign_id or not data.get('title'):
        return jsonify({'error': 'campaign_id and title are required'}), 400
    
    db = supabase_admin or supabase
    user = session.get('user', {})
    
    try:
        piece = {
            'campaign_id': campaign_id,
            'title': data['title'],
            'slug': data.get('slug', data['title'].lower().replace(' ', '-')[:80]),
            'target_keyword': data.get('target_keyword', ''),
            'secondary_keywords': data.get('secondary_keywords', []),
            'funnel_stage': data.get('funnel_stage', 'tofu'),
            'content_type': data.get('content_type', 'blog_post'),
            'brief': data.get('brief', {}),
            'outline': data.get('outline', []),
            'draft_html': data.get('draft_html', ''),
            'word_count': data.get('word_count', 0),
            'meta_title': data.get('meta_title', ''),
            'meta_description': data.get('meta_description', ''),
            'status': data.get('status', 'brief'),
            'assigned_to': data.get('assigned_to'),
            'assigned_by': user.get('id'),
            'cluster_id': data.get('cluster_id'),
        }
        
        result = db.table('content_pieces').insert(piece).execute()
        
        return jsonify({'success': True, 'piece': result.data[0] if result.data else piece})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/content-pieces/<piece_id>', methods=['PUT'])
@login_required
def update_content_piece(piece_id):
    """Update a content piece — status changes, content, reviewer notes, etc."""
    data = request.json or {}
    db = supabase_admin or supabase
    
    try:
        update_fields = {}
        allowed = ['title', 'slug', 'target_keyword', 'secondary_keywords', 'funnel_stage',
                    'content_type', 'brief', 'outline', 'draft_html', 'word_count',
                    'meta_title', 'meta_description', 'schema_markup', 'internal_links',
                    'status', 'assigned_to', 'reviewer_notes', 'published_url', 'publish_platform']
        
        for key in allowed:
            if key in data:
                update_fields[key] = data[key]
        
        # Track revisions
        if data.get('status') == 'revision':
            # Increment revision count
            existing = db.table('content_pieces').select('revision_count').eq('id', piece_id).single().execute()
            if existing.data:
                update_fields['revision_count'] = (existing.data.get('revision_count') or 0) + 1
        
        if data.get('status') == 'published' and data.get('published_url'):
            from datetime import datetime
            update_fields['published_at'] = datetime.utcnow().isoformat()
        
        # Auto-calculate word count if HTML provided
        if 'draft_html' in update_fields and update_fields['draft_html']:
            import re
            text = re.sub(r'<[^>]+>', ' ', update_fields['draft_html'])
            update_fields['word_count'] = len(text.split())
        
        update_fields['updated_at'] = datetime.utcnow().isoformat() if 'datetime' in dir() else __import__('datetime').datetime.utcnow().isoformat()
        
        result = db.table('content_pieces').update(update_fields).eq('id', piece_id).execute()
        
        return jsonify({'success': True, 'piece': result.data[0] if result.data else {}})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/content-pieces/<piece_id>', methods=['DELETE'])
@login_required
def delete_content_piece(piece_id):
    """Delete a content piece."""
    db = supabase_admin or supabase
    try:
        db.table('content_pieces').delete().eq('id', piece_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# PHASE 3: STANDALONE AI ARTICLE GENERATION
# =============================================================================

@app.route('/api/generate-article', methods=['POST'])
@login_required
def generate_article():
    """Generate a full SEO-optimized article using Gemini.
    
    Pipeline: Research → Outline → Chunked Sections → Polish
    
    Input: title, target_keyword, funnel_stage, campaign_id, optional brief data
    Output: full HTML article, outline, meta tags, word_count
    """
    data = request.json or {}
    campaign_id = data.get('campaign_id')
    title = data.get('title')
    target_keyword = data.get('target_keyword', title)
    funnel_stage = data.get('funnel_stage', 'tofu')
    word_target = data.get('word_target', 1800)
    brief_data = data.get('brief', {})
    content_piece_id = data.get('content_piece_id')  # Optionally update existing piece
    
    if not title:
        return jsonify({'error': 'title is required'}), 400
    
    db = supabase_admin or supabase
    
    try:
        # Get campaign context
        project_loc = 'US'
        project_lang = 'English'
        brand_context = ''
        
        if campaign_id:
            camp_res = db.table('campaigns').select('domain, settings').eq('id', campaign_id).single().execute()
            if camp_res.data:
                settings = camp_res.data.get('settings') or {}
                project_loc = settings.get('location', 'US')
                bc = settings.get('brand_config', {})
                if bc:
                    brand_context = f"\nBrand Voice: {bc.get('voice', 'professional')}\nUSP: {bc.get('usp', '')}\nTarget Audience: {bc.get('target_audience', '')}"
        
        # 1. Research via Gemini grounding
        research_section = ""
        try:
            research = perform_gemini_research(
                f"{title} {target_keyword} SEO guide", 
                location=project_loc
            )
            if research:
                research_section = f"# RESEARCH:\n{research}"
        except Exception as re_err:
            logger.warning(f"Research step failed (non-fatal): {re_err}")
        
        # 2. Generate outline
        outline = generate_dynamic_outline(title, research_section, project_loc, gemini_client)
        if not outline:
            return jsonify({'error': 'Failed to generate outline'}), 500
        
        # 3. Generate sections (chunked for quality)
        internal_links_str = ""
        if brief_data.get('internal_links'):
            internal_links_str = "\n".join([f"- {lnk}" for lnk in brief_data['internal_links']])
        
        full_content = generate_sections_chunked(
            title, outline, research_section, project_loc, gemini_client, internal_links_str
        )
        
        if not full_content:
            return jsonify({'error': 'Failed to generate article sections'}), 500
        
        # 4. Final polish
        cta_url = brief_data.get('cta_url', '')
        generated_text = final_polish(
            full_content, title, target_keyword, cta_url, project_loc, gemini_client
        )
        
        if not generated_text:
            generated_text = full_content  # Fall back to unpolished version
        
        # 5. Generate meta tags
        meta_title = title[:60]
        meta_description = ''
        try:
            meta_prompt = f"""Generate a compelling SEO meta description for this article.
Title: {title}
Primary Keyword: {target_keyword}
Requirements: 150-160 characters, includes the keyword, has a call-to-action.
Output ONLY the meta description text, nothing else."""
            
            meta_result = gemini_client.generate_content(prompt=meta_prompt, model_name="gemini-2.5-flash")
            if meta_result:
                meta_description = meta_result.strip()[:160]
        except:
            meta_description = f"Learn about {target_keyword}. Comprehensive guide covering everything you need to know."
        
        # Calculate word count
        import re as re_mod
        text_only = re_mod.sub(r'<[^>]+>', ' ', generated_text)
        word_count = len(text_only.split())
        
        # 6. If content_piece_id provided, update the existing piece
        if content_piece_id:
            try:
                db.table('content_pieces').update({
                    'draft_html': generated_text,
                    'outline': outline if isinstance(outline, list) else [{'content': outline}],
                    'word_count': word_count,
                    'meta_title': meta_title,
                    'meta_description': meta_description,
                    'status': 'draft',
                    'updated_at': datetime.utcnow().isoformat()
                }).eq('id', content_piece_id).execute()
            except Exception as up_err:
                logger.warning(f"Failed to update content piece: {up_err}")
        
        return jsonify({
            'success': True,
            'article': {
                'title': title,
                'html': generated_text,
                'outline': outline,
                'meta_title': meta_title,
                'meta_description': meta_description,
                'word_count': word_count,
                'target_keyword': target_keyword,
                'funnel_stage': funnel_stage
            }
        })
        
    except Exception as e:
        logger.error(f"Article generation error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# PHASE 3: LINK SEARCH & AUTO-RECOMMEND
# =============================================================================

@app.route('/api/link-search', methods=['GET'])
@login_required
def search_link_inventory():
    """Search the link inventory with filters for niche, DA range, price range.
    
    Params:
        q: search query (matches domain or niche)
        niche: filter by niche
        min_da: minimum domain authority
        max_da: maximum domain authority
        min_price: minimum price
        max_price: maximum price
        sort_by: da, price, traffic (default: da)
        limit: max results (default: 20)
    """
    db = supabase_admin or supabase
    
    try:
        q = request.args.get('q', '').strip()
        niche = request.args.get('niche', '').strip()
        min_da = int(request.args.get('min_da', 0))
        max_da = int(request.args.get('max_da', 100))
        min_price = float(request.args.get('min_price', 0))
        max_price = float(request.args.get('max_price', 99999))
        sort_by = request.args.get('sort_by', 'da')
        limit = min(int(request.args.get('limit', 20)), 50)
        
        query = db.table('link_inventory').select('*').eq('is_active', True)
        
        # Filters
        query = query.gte('da', min_da).lte('da', max_da)
        query = query.gte('price', min_price).lte('price', max_price)
        
        if niche:
            query = query.ilike('niche', f'%{niche}%')
        
        if q:
            query = query.or_(f'domain.ilike.%{q}%,niche.ilike.%{q}%')
        
        # Sort
        desc = True
        if sort_by == 'price':
            query = query.order('price', desc=False)
        elif sort_by == 'traffic':
            query = query.order('organic_traffic', desc=True)
        else:
            query = query.order('da', desc=True)
        
        result = query.limit(limit).execute()
        links = result.data or []
        
        # Get available niches for filter dropdown
        all_niches_res = db.table('link_inventory').select('niche').eq('is_active', True).execute()
        niches = sorted(set(item['niche'] for item in (all_niches_res.data or []) if item.get('niche')))
        
        return jsonify({
            'success': True,
            'links': links,
            'total': len(links),
            'available_niches': niches
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/link-recommend', methods=['GET'])
@login_required
def recommend_links():
    """Auto-recommend best backlinks for a campaign based on niche fit and anchor needs.
    
    Analyzes:
    1. Campaign niche/domain to find relevant link sources
    2. Existing placements to avoid duplicates
    3. Anchor text distribution to suggest what's needed
    4. Budget efficiency (best DA per dollar)
    
    Params: campaign_id, budget (optional, default 500), count (default 5)
    """
    campaign_id = request.args.get('campaign_id')
    budget = float(request.args.get('budget', 500))
    count = min(int(request.args.get('count', 5)), 15)
    
    if not campaign_id:
        return jsonify({'error': 'campaign_id required'}), 400
    
    db = supabase_admin or supabase
    
    try:
        # 1. Get campaign info
        camp_res = db.table('campaigns').select('domain, name, settings').eq('id', campaign_id).single().execute()
        if not camp_res.data:
            return jsonify({'error': 'Campaign not found'}), 404
        
        campaign = camp_res.data
        domain = campaign.get('domain', '')
        settings = campaign.get('settings') or {}
        niche = settings.get('niche', settings.get('brand_config', {}).get('industry', ''))
        
        # 2. Get existing placements to avoid duplicates
        placements_res = db.table('link_placements').select('target_url').eq('campaign_id', campaign_id).execute()
        existing_domains = set()
        for p in (placements_res.data or []):
            url = p.get('target_url', '')
            if url:
                try:
                    existing_domains.add(url.split('/')[2].replace('www.', ''))
                except:
                    pass
        
        # 3. Get all available links from inventory
        inv_res = db.table('link_inventory').select('*').eq('is_active', True).order('da', desc=True).execute()
        all_links = inv_res.data or []
        
        # 4. Score each link
        scored = []
        for link in all_links:
            link_domain = link.get('domain', '')
            
            # Skip if already placed
            if link_domain.replace('www.', '') in existing_domains:
                continue
            
            # Skip if over budget
            if link.get('price', 0) > budget:
                continue
            
            # Scoring: DA weight (40%) + niche relevance (30%) + value ratio (30%)
            da_score = min(link.get('da', 0) / 100, 1.0) * 40
            
            # Niche relevance
            link_niche = (link.get('niche', '') or '').lower()
            niche_score = 0
            if niche and niche.lower() in link_niche:
                niche_score = 30  # Exact niche match
            elif niche:
                # Partial relevance for related niches
                niche_map = {
                    'saas': ['technology', 'software', 'startup'],
                    'technology': ['saas', 'software', 'startup', 'coding'],
                    'health': ['fitness', 'wellness', 'medical'],
                    'finance': ['business', 'startup', 'investment'],
                    'ecommerce': ['retail', 'shopping', 'business'],
                    'travel': ['lifestyle', 'hospitality'],
                }
                related = niche_map.get(niche.lower(), [])
                if link_niche in related:
                    niche_score = 20
                else:
                    niche_score = 10  # Generic
            else:
                niche_score = 15  # No niche specified, moderate score
            
            # Value ratio: DA per dollar
            price = max(link.get('price', 1), 1)
            value_score = min((link.get('da', 0) / price) * 100, 30)
            
            total_score = da_score + niche_score + value_score
            
            scored.append({
                **link,
                'recommendation_score': round(total_score, 1),
                'reason': f"DA {link.get('da', 0)} | {link.get('niche', 'General')} | ${price:.0f}"
            })
        
        # Sort by recommendation score
        scored.sort(key=lambda x: x['recommendation_score'], reverse=True)
        recommendations = scored[:count]
        
        # 5. Generate anchor text suggestions
        anchor_suggestions = []
        if domain:
            anchor_suggestions = [
                {'type': 'branded', 'text': domain.replace('.com', '').replace('.io', '').title(), 'recommended_pct': '30-40%'},
                {'type': 'exact_match', 'text': niche or 'target keyword', 'recommended_pct': '10-15%'},
                {'type': 'partial_match', 'text': f'best {niche.lower() if niche else "solutions"}', 'recommended_pct': '15-20%'},
                {'type': 'generic', 'text': 'click here / learn more', 'recommended_pct': '10-15%'},
                {'type': 'url', 'text': f'https://{domain}', 'recommended_pct': '10-15%'},
            ]
        
        return jsonify({
            'success': True,
            'recommendations': recommendations,
            'total_available': len(scored),
            'budget': budget,
            'existing_placements': len(existing_domains),
            'anchor_distribution': anchor_suggestions,
            'estimated_cost': sum(r.get('price', 0) for r in recommendations)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# GOOGLE INTEGRATION — Metrics, Properties, Sync
# =============================================================================

@app.route('/api/google/metrics', methods=['POST'])
@login_required
def google_metrics():
    """Fetch GSC/GA4 metrics for a property over a given duration."""
    data = request.json
    gsc_property = data.get('gsc_property')
    duration = data.get('duration', '3m')

    if not gsc_property:
        return jsonify({'error': 'gsc_property is required'}), 400

    user = session['user']
    org_id = user.get('organization_id')
    client = supabase_admin or supabase

    duration_map = {'7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
    duration_days = duration_map.get(duration, 90)

    try:
        integration = client.table('agency_integrations').select('*').eq('organization_id', org_id).eq('provider', 'google').execute()

        if not integration.data:
            return jsonify({'error': 'Google integration not connected. Go to Settings → Integrations.'}), 400

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build as google_build
        from api.google_integration import get_client_config
        from datetime import timedelta, date

        refresh_token = integration.data[0].get('refresh_token')
        if not refresh_token:
            return jsonify({'error': 'No Google refresh token found.'}), 400

        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )

        gsc_service = google_build('searchconsole', 'v1', credentials=creds)

        today = date.today()
        current_start = (today - timedelta(days=duration_days)).isoformat()
        current_end = today.isoformat()
        prev_start = (today - timedelta(days=duration_days * 2)).isoformat()
        prev_end = (today - timedelta(days=duration_days)).isoformat()

        def _gsc_query(start, end, row_limit=5000):
            body = {
                'startDate': start,
                'endDate': end,
                'dimensions': ['query'],
                'rowLimit': row_limit
            }
            resp = gsc_service.searchanalytics().query(siteUrl=gsc_property, body=body).execute()
            return resp.get('rows', [])

        current_rows = _gsc_query(current_start, current_end)
        prev_rows = _gsc_query(prev_start, prev_end)

        queries = [{
            'query': r['keys'][0],
            'clicks': r['clicks'],
            'impressions': r['impressions'],
            'ctr': round(r['ctr'], 4),
            'position': round(r['position'], 1)
        } for r in current_rows]

        prev_queries = [{
            'query': r['keys'][0],
            'clicks': r['clicks'],
            'impressions': r['impressions'],
            'ctr': round(r['ctr'], 4),
            'position': round(r['position'], 1)
        } for r in prev_rows]

        return jsonify({
            'gsc': {
                'queries': queries[:25],
                'allQueries': queries,
                'prevQueries': prev_queries,
                'total_clicks': sum(r['clicks'] for r in current_rows),
                'total_impressions': sum(r['impressions'] for r in current_rows)
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/google-metrics', methods=['POST'])
@login_required
def google_metrics_legacy():
    """Legacy alias — routes to the same logic as /api/google/metrics."""
    return google_metrics()


@app.route('/api/google/properties', methods=['GET'])
@login_required
def google_properties():
    """List GSC and GA4 properties available to the connected Google account."""
    user = session['user']
    org_id = user.get('organization_id')
    client = supabase_admin or supabase

    try:
        integration = client.table('agency_integrations').select('*').eq('organization_id', org_id).eq('provider', 'google').execute()

        if not integration.data:
            return jsonify({'gsc': [], 'ga4': []})

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build as google_build
        from api.google_integration import get_client_config

        refresh_token = integration.data[0].get('refresh_token')
        if not refresh_token:
            return jsonify({'gsc': [], 'ga4': []})

        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )

        # Fetch GSC sites
        gsc_service = google_build('searchconsole', 'v1', credentials=creds)
        sites_response = gsc_service.sites().list().execute()
        gsc_sites = sites_response.get('siteEntry', [])

        gsc_props = [{
            'property_url_or_id': s['siteUrl'],
            'property_name': s['siteUrl'].replace('sc-domain:', '').replace('https://', '').rstrip('/')
        } for s in gsc_sites]

        return jsonify({
            'gsc': gsc_props,
            'ga4': []  # GA4 requires Analytics Admin API — added as separate integration later
        })
    except Exception as e:
        return jsonify({'error': str(e), 'gsc': [], 'ga4': []}), 200


@app.route('/api/google/sync-properties', methods=['POST'])
@login_required
def google_sync_properties():
    """Re-sync Google properties from the connected account."""
    # This just re-fetches — same as GET /properties but POSTed for UI semantics
    user = session['user']
    org_id = user.get('organization_id')
    client = supabase_admin or supabase

    try:
        integration = client.table('agency_integrations').select('*').eq('organization_id', org_id).eq('provider', 'google').execute()

        if not integration.data:
            return jsonify({'success': False, 'error': 'Google not connected'}), 400

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build as google_build
        from api.google_integration import get_client_config

        refresh_token = integration.data[0].get('refresh_token')
        client_config = get_client_config()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            client_id=client_config['web']['client_id'],
            client_secret=client_config['web']['client_secret'],
            token_uri=client_config['web']['token_uri']
        )

        gsc_service = google_build('searchconsole', 'v1', credentials=creds)
        sites_response = gsc_service.sites().list().execute()
        gsc_sites = sites_response.get('siteEntry', [])

        gsc_props = [{
            'property_url_or_id': s['siteUrl'],
            'property_name': s['siteUrl'].replace('sc-domain:', '').replace('https://', '').rstrip('/')
        } for s in gsc_sites]

        return jsonify({'success': True, 'gsc': gsc_props, 'ga4': [], 'synced': len(gsc_props)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/webflow/publish', methods=['POST'])
@login_required
def webflow_publish():
    """Publish a content piece to a Webflow collection."""
    data = request.json
    api_key = data.get('api_key')
    collection_id = data.get('collection_id')
    page_id = data.get('page_id')

    if not api_key or not collection_id:
        return jsonify({'error': 'api_key and collection_id are required'}), 400

    client = supabase_admin or supabase

    try:
        # Fetch the page content
        page = client.table('pages').select('*').eq('id', page_id).single().execute()
        if not page.data:
            return jsonify({'error': 'Page not found'}), 404

        page_data = page.data
        title = page_data.get('title', 'Untitled')
        html_content = page_data.get('html_content', '')
        meta_description = page_data.get('meta_description', '')
        slug = page_data.get('slug', title.lower().replace(' ', '-'))

        # Webflow API v2
        import requests as ext_requests
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'accept': 'application/json'
        }

        payload = {
            'isArchived': False,
            'isDraft': False,
            'fieldData': {
                'name': title,
                'slug': slug,
                'post-body': html_content,
                'post-summary': meta_description
            }
        }

        resp = ext_requests.post(
            f'https://api.webflow.com/v2/collections/{collection_id}/items',
            headers=headers,
            json=payload,
            timeout=30
        )

        if resp.status_code >= 400:
            return jsonify({'error': f'Webflow API error: {resp.text}'}), resp.status_code

        return jsonify({'success': True, 'webflow_item': resp.json()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# PHASE 4: EMBEDDED SOPs — Role & Task-Type Specific
# =============================================================================

SOP_LIBRARY = {
    'technical': {
        'title': 'Technical SEO Fix',
        'role': 'Optimization Specialist',
        'steps': [
            'Review the flagged issue from the audit report',
            'Reproduce the issue in browser DevTools or PageSpeed Insights',
            'Identify root cause (missing tags, slow resources, broken links, etc.)',
            'Implement the fix in the CMS or codebase',
            'Validate fix using the same audit tool that flagged it',
            'Document what was changed and why in the task notes',
            'Mark task as Done'
        ],
        'quality_checks': [
            'Page loads under 3 seconds after fix',
            'No new console errors introduced',
            'Schema validates in Google Rich Results Test',
            'Fix verified on both mobile and desktop'
        ]
    },
    'content': {
        'title': 'Content Creation',
        'role': 'Content Creator',
        'steps': [
            'Read the content brief and target keyword cluster',
            'Review competitor articles ranking for the target keyword',
            'Write the article following the provided outline structure',
            'Include 2-3 internal links to related pages',
            'Add a compelling meta title (≤60 chars) and meta description (≤160 chars)',
            'Run content through readability check (target: Grade 8-10)',
            'Submit draft for review in the Content Library',
            'Address reviewer feedback and resubmit if needed',
            'Publish to the designated platform (WordPress/Webflow)'
        ],
        'quality_checks': [
            'Word count meets brief target (±10%)',
            'Primary keyword in H1, first paragraph, and 2-3 H2s',
            'All images have descriptive alt text',
            'No AI-detectable patterns (run through humanization)',
            'Internal links open correctly',
            'CTA is clear and placed above the fold'
        ]
    },
    'link_building': {
        'title': 'Link Building Outreach',
        'role': 'Link Builder',
        'steps': [
            'Review the recommended link opportunities from AI Recommendations',
            'Verify the target domain DA and traffic in Ahrefs/Semrush',
            'Check the anchor text plan for this campaign',
            'Draft outreach email following brand voice guidelines',
            'Send outreach and log in Placements tracker',
            'Follow up after 3-5 business days if no response',
            'Once placed, verify the link is live and DoFollow',
            'Update placement status to Confirmed',
            'Report the new backlink in the Wins of the Week'
        ],
        'quality_checks': [
            'Link is DoFollow and contextually placed',
            'Anchor text matches the planned distribution',
            'Referring page is indexed in Google',
            'No link farm signals on the referring domain',
            'Link points to the correct target URL'
        ]
    },
    'optimization': {
        'title': 'On-Page Optimization',
        'role': 'Optimization Specialist',
        'steps': [
            'Pull the current on-page data for the target URL',
            'Compare against top 3 SERP competitors',
            'Optimize title tag, meta description, and H1',
            'Add/improve schema markup (FAQ, HowTo, Product, etc.)',
            'Optimize image sizes and add lazy loading',
            'Improve internal linking (add 2-3 contextual links)',
            'Submit URL for re-indexing in Google Search Console',
            'Monitor ranking changes over 2-4 weeks'
        ],
        'quality_checks': [
            'Title tag ≤60 chars with primary keyword',
            'Meta description ≤160 chars with CTA',
            'Schema validates without errors',
            'Page passes Core Web Vitals',
            'No broken internal/external links'
        ]
    },
    'reporting': {
        'title': 'Client Report Generation',
        'role': 'Reporting Manager',
        'steps': [
            'Pull analytics data from Google Analytics / Search Console',
            'Compare current period vs previous period',
            'Highlight top keyword movements (+/- rankings)',
            'Document completed tasks and their impact',
            'Add Wins of the Week section',
            'Generate PDF/Slides export',
            'Send to client with executive summary'
        ],
        'quality_checks': [
            'Data matches source (GA/GSC) — no discrepancies',
            'All charts have clear labels and context',
            'Executive summary is under 200 words',
            'Client brand name is correct throughout',
            'Report covers the agreed reporting period'
        ]
    },
    'strategy': {
        'title': 'Content Strategy Research',
        'role': 'Content Strategist',
        'steps': [
            'Audit existing content inventory against target keywords',
            'Run keyword research for gaps and opportunities',
            'Map keywords to funnel stages (ToFu/MoFu/BoFu)',
            'Create topic clusters with pillar-spoke structure',
            'Prioritize topics by opportunity score',
            'Generate content briefs for top priorities',
            'Add entries to the content calendar',
            'Present strategy to Campaign Manager for approval'
        ],
        'quality_checks': [
            'Every keyword has assigned funnel stage',
            'No keyword cannibalization across pages',
            'Topic clusters have clear pillar pages',
            'Content calendar has realistic deadlines',
            'Strategy aligns with brand voice and USP'
        ]
    }
}

@app.route('/api/sops', methods=['GET'])
@login_required
def get_sops():
    """Get SOPs from DB. Supports ?issue_key= for single lookup or lists all org SOPs."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'sops': []})

    client = supabase_admin or supabase
    issue_key = request.args.get('issue_key', '').strip()

    try:
        query = client.table('sops').select('*').eq('organization_id', org_id).eq('is_archived', False)
        if issue_key:
            query = query.eq('issue_key', issue_key)
        res = query.order('created_at', desc=True).execute()
        sops = res.data or []

        # If looking up by issue_key and not found in DB, auto-seed it from defaults so they can edit it
        if issue_key and not sops:
            fallback = SOP_LIBRARY.get(issue_key)
            if not fallback:
                for key, val in TASK_SOP_DEFAULTS.items():
                    if issue_key.startswith(key):
                        fallback = val
                        break
            
            if fallback:
                steps = [{'order': i+1, 'text': s} for i, s in enumerate(fallback.get('steps', []))]
                sop_data = {
                    'organization_id': org_id,
                    'issue_key': issue_key,
                    'title': fallback.get('title', issue_key),
                    'issue_description': fallback.get('issue', f"Standard operating procedure for {issue_key}"),
                    'steps': steps,
                    'quality_checks': fallback.get('quality_checks', []),
                    'category': fallback.get('category', 'onpage'),
                    'assigned_role': fallback.get('role', None),
                    'is_default': True,
                    'created_by': user.get('id'),
                    'updated_by': user.get('id')
                }
                try:
                    insert_res = client.table('sops').upsert(sop_data, on_conflict='organization_id,issue_key').execute()
                    if insert_res.data:
                        new_sop = insert_res.data[0]
                        new_sop['notes'] = []
                        return jsonify({'sop': new_sop, 'source': 'auto-seeded'})
                except Exception as db_err:
                    logger.error(f"Failed to auto-seed SOP {issue_key}: {db_err}")
                    # Fallback to in-memory if DB insert fails
                    return jsonify({'sop': {'issue_key': issue_key, 'is_default': True, **fallback}, 'source': 'fallback'})
            
            return jsonify({'sop': None, 'source': 'none'})

        if issue_key and sops:
            # Also fetch notes for this SOP
            sop = sops[0]
            notes_res = client.table('sop_notes').select('*').eq('sop_id', sop['id']).order('created_at', desc=True).execute()
            sop['notes'] = notes_res.data or []
            return jsonify({'sop': sop, 'source': 'db'})

        return jsonify({'sops': sops, 'success': True})
    except Exception as e:
        logger.error(f"Error fetching SOPs: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/sops', methods=['POST'])
@login_required
def create_sop():
    """Create a new SOP entry."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'error': 'No organization'}), 400
    if user.get('role') not in ['admin', 'campaign_manager']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()
    client = supabase_admin or supabase

    try:
        sop_data = {
            'organization_id': org_id,
            'issue_key': data.get('issue_key', ''),
            'title': data.get('title', ''),
            'issue_description': data.get('issue_description', ''),
            'steps': data.get('steps', []),
            'quality_checks': data.get('quality_checks', []),
            'category': data.get('category', 'onpage'),
            'difficulty': data.get('difficulty', 'easy'),
            'estimated_minutes': data.get('estimated_minutes', 5),
            'tools_recommended': data.get('tools_recommended', []),
            'video_url': data.get('video_url'),
            'assigned_role': data.get('assigned_role'),
            'created_by': user.get('id'),
            'updated_by': user.get('id'),
            'is_default': data.get('is_default', False)
        }
        res = client.table('sops').upsert(sop_data, on_conflict='organization_id,issue_key').execute()
        return jsonify({'success': True, 'sop': (res.data or [None])[0]})
    except Exception as e:
        logger.error(f"Error creating SOP: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/sops/<sop_id>', methods=['PUT'])
@login_required
def update_sop(sop_id):
    """Update an existing SOP."""
    user = session['user']
    if user.get('role') not in ['admin', 'campaign_manager']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()
    client = supabase_admin or supabase

    try:
        update_fields = {k: v for k, v in data.items() if k in [
            'title', 'issue_description', 'steps', 'quality_checks', 'category',
            'difficulty', 'estimated_minutes', 'tools_recommended', 'video_url', 'assigned_role'
        ]}
        update_fields['updated_by'] = user.get('id')
        update_fields['updated_at'] = 'now()'

        res = client.table('sops').update(update_fields).eq('id', sop_id).execute()
        return jsonify({'success': True, 'sop': (res.data or [None])[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sops/<sop_id>/notes', methods=['POST'])
@login_required
def add_sop_note(sop_id):
    """Add a community note to an SOP."""
    user = session['user']
    data = request.get_json()
    client = supabase_admin or supabase

    try:
        note = {
            'sop_id': sop_id,
            'author_id': user.get('id'),
            'author_name': user.get('full_name') or user.get('email', '').split('@')[0],
            'content': data.get('content', ''),
            'note_type': data.get('note_type', 'tip')
        }
        res = client.table('sop_notes').insert(note).execute()
        return jsonify({'success': True, 'note': (res.data or [None])[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sops/seed', methods=['POST'])
@login_required
def seed_sops():
    """Seed default SOPs for the org from the built-in library."""
    user = session['user']
    org_id = user.get('organization_id')
    if not org_id:
        return jsonify({'error': 'No organization'}), 400
    if user.get('role') not in ['admin', 'campaign_manager']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    client = supabase_admin or supabase
    seeded = 0

    try:
        # Seed from TASK_SOP_DEFAULTS (the detailed issue-level SOPs)
        for issue_key, sop in TASK_SOP_DEFAULTS.items():
            steps = [{'order': i+1, 'text': s} for i, s in enumerate(sop.get('steps', []))]
            sop_data = {
                'organization_id': org_id,
                'issue_key': issue_key,
                'title': issue_key,
                'issue_description': sop.get('issue', ''),
                'steps': steps,
                'quality_checks': [],
                'category': 'onpage',
                'assigned_role': None,
                'is_default': True,
                'created_by': user.get('id'),
                'updated_by': user.get('id')
            }
            try:
                client.table('sops').upsert(sop_data, on_conflict='organization_id,issue_key').execute()
                seeded += 1
            except Exception:
                pass

        # Seed from SOP_LIBRARY (the role-level SOPs)
        for sop_type, sop in SOP_LIBRARY.items():
            steps = [{'order': i+1, 'text': s} for i, s in enumerate(sop.get('steps', []))]
            qc = sop.get('quality_checks', [])
            sop_data = {
                'organization_id': org_id,
                'issue_key': sop_type,
                'title': sop.get('title', sop_type),
                'issue_description': f"Standard operating procedure for {sop_type}",
                'steps': steps,
                'quality_checks': qc,
                'category': sop_type,
                'assigned_role': sop.get('role'),
                'is_default': True,
                'created_by': user.get('id'),
                'updated_by': user.get('id')
            }
            try:
                client.table('sops').upsert(sop_data, on_conflict='organization_id,issue_key').execute()
                seeded += 1
            except Exception:
                pass

        return jsonify({'success': True, 'seeded': seeded})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Hardcoded defaults used as fallback and for seeding
TASK_SOP_DEFAULTS = {
    'Fix Missing Page Titles': {
        'issue': 'Page has no <title> tag defined',
        'steps': [
            'Open the page in your CMS or HTML editor',
            'Locate the <head> section of the page',
            'Add a <title> tag: <title>Primary Keyword - Brand Name</title>',
            'Keep it under 60 characters for full SERP visibility',
            'Include primary keyword near the beginning',
            'Make it unique - no other page should share this title',
            'Save and re-crawl the page to verify'
        ]
    },
    'Rewrite Duplicate Page Titles': {
        'issue': 'Title tag is identical to another page on this site',
        'steps': [
            'Identify which pages share the same title (listed in affected pages)',
            'Determine the unique focus keyword for each page',
            'Rewrite each title to be unique: include the page-specific keyword',
            'Format: Primary Keyword - Secondary Modifier | Brand',
            'Ensure no two pages share the same title after edits',
            'Publish changes and re-crawl to confirm'
        ]
    },
    'Add Meta Descriptions': {
        'issue': 'Page has no meta description - Google will auto-generate one',
        'steps': [
            'Open the page in your CMS or code editor',
            'Add: <meta name="description" content="...">',
            'Write a compelling 120-155 character summary',
            'Include the primary keyword naturally',
            'Add a call-to-action (Learn more, Get started, Shop now)',
            'Make it unique per page - never copy/paste across pages',
            'Publish and verify in Google Search Console'
        ]
    },
    'Add H1 Tags': {
        'issue': 'Page is missing an H1 heading entirely',
        'steps': [
            'Every page must have exactly one <h1> tag',
            'The H1 should contain the primary keyword for that page',
            'Place it as the first visible heading on the page',
            'Make it descriptive - it tells users and Google what the page is about',
            'Do not use the same H1 text as the title tag (use a variation)',
            'Verify with Chrome DevTools: Ctrl+F then search for <h1>'
        ]
    },
    'Fix Broken Links': {
        'issue': 'Page contains links pointing to URLs that return 4xx/5xx errors',
        'steps': [
            'Click each affected URL to verify it is truly broken',
            'For internal broken links: fix the destination URL or create a redirect',
            'For external broken links: find an updated URL or remove the link',
            'Replace with a relevant alternative resource if the content is gone',
            'Use Screaming Frog or Ahrefs to find all broken links in bulk',
            'Set up automated broken link monitoring going forward'
        ]
    },
    'Expand Thin Content': {
        'issue': 'Page has fewer than 300 words - considered thin content by Google',
        'steps': [
            'Assess if the page serves a valuable purpose',
            'If yes: expand content to 500+ words with useful information',
            'Add FAQs, how-tos, comparisons, or case studies',
            'If the page has no unique value: consider redirecting (301) to a stronger page',
            'Alternatively, consolidate multiple thin pages into one comprehensive page',
            'After expanding, add internal links to/from relevant pages'
        ]
    },
    'Add Alt Text to Images': {
        'issue': 'Images on this page are missing alt text attributes',
        'steps': [
            'Find all <img> tags without alt attributes on the page',
            'Write descriptive alt text that explains what the image shows',
            'Include relevant keywords naturally (do not keyword-stuff)',
            'For decorative images, use alt="" (empty alt, not missing)',
            'Keep alt text under 125 characters',
            'Alt text is critical for accessibility and Image SEO'
        ]
    },
    'Optimize Core Web Vitals': {
        'issue': 'Page failed LCP (>2.5s) or CLS (>0.1) thresholds',
        'steps': [
            'Run PageSpeed Insights for the specific URL',
            'LCP Fix: Optimize the largest image/text block above the fold',
            'LCP Fix: Preload critical resources with <link rel="preload">',
            'CLS Fix: Set explicit width/height on all images and videos',
            'CLS Fix: Do not inject content above existing content dynamically',
            'Reduce JavaScript that blocks rendering (defer/async)',
            'Enable server-side caching and use a CDN',
            'Re-test with Lighthouse after each change'
        ]
    }
}


if __name__ == '__main__':
    print("Starting server...")
    port = int(os.getenv('PORT', 3000))
    print(f"Running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)

