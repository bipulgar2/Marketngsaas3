"""
Google OAuth Authentication Module for Google Slides/Drive API access.
Supports Service Account (recommended for white-label) and OAuth flows.
"""
import os
import json
import tempfile
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow, Flow

# Scopes for Slides and Drive access
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
]

# Get project root (parent of api/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_production():
    """Check if running in production (Railway, Heroku, etc.)"""
    return os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RENDER') or os.getenv('HEROKU_APP_NAME') or os.getenv('PRODUCTION')


def get_service_account_credentials():
    """
    Get Service Account credentials from env var or file.
    This is the recommended approach for white-label deployments.
    
    Priority:
    1. GOOGLE_SERVICE_ACCOUNT env var (JSON string)
    2. service_account.json file in project root
    
    Returns google.oauth2.service_account.Credentials or None
    """
    # Check environment variable first
    env_sa = os.getenv('GOOGLE_SERVICE_ACCOUNT')
    if env_sa:
        try:
            sa_info = json.loads(env_sa)
            creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
            print("Using Service Account from GOOGLE_SERVICE_ACCOUNT env var")
            return creds
        except json.JSONDecodeError as e:
            print(f"Error parsing GOOGLE_SERVICE_ACCOUNT: {e}")
            return None
        except Exception as e:
            print(f"Error loading Service Account: {e}")
            return None
    
    # Fall back to file
    file_path = os.path.join(PROJECT_ROOT, "service_account.json")
    if os.path.exists(file_path):
        try:
            creds = service_account.Credentials.from_service_account_file(file_path, scopes=SCOPES)
            print("Using Service Account from service_account.json file")
            return creds
        except Exception as e:
            print(f"Error loading service_account.json: {e}")
            return None
    
    return None


def get_client_secret_config():
    """
    Get OAuth client secret from env var or file.
    Used for user OAuth flow (not recommended for white-label).
    """
    env_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    if env_secret:
        try:
            return json.loads(env_secret)
        except json.JSONDecodeError as e:
            raise ValueError(f"GOOGLE_CLIENT_SECRET contains invalid JSON: {e}")
    
    file_path = os.path.join(PROJECT_ROOT, "client_secret.json")
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    
    return None


def get_client_secret_path():
    """Get path to client_secret.json (creates temp file if using env var)."""
    env_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    if env_secret:
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        temp_file.write(env_secret)
        temp_file.close()
        return temp_file.name
    
    file_path = os.path.join(PROJECT_ROOT, "client_secret.json")
    if os.path.exists(file_path):
        return file_path
    
    return None


def get_google_credentials():
    """
    Main entry point for getting Google credentials.
    
    Priority:
    1. Service Account (recommended, no user interaction)
    2. OAuth token.json (local development)
    3. OAuth flow (requires user login)
    """
    # Try Service Account first (best for production)
    sa_creds = get_service_account_credentials()
    if sa_creds:
        return sa_creds
    
    # Fall back to OAuth for local development
    token_file = os.path.join(PROJECT_ROOT, "token.json")
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            if creds and creds.valid:
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
                return creds
        except Exception as e:
            print(f"Error loading token.json: {e}")
    
    # Try Desktop OAuth flow (local only)
    if not is_production():
        client_secret_path = get_client_secret_path()
        if client_secret_path:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_file, "w") as f:
                f.write(creds.to_json())
            return creds
    
    raise FileNotFoundError(
        "No Google credentials found. For production, set GOOGLE_SERVICE_ACCOUNT env var. "
        "For local dev, add service_account.json or client_secret.json to project root."
    )


# ============ OAuth Web Flow functions (kept for compatibility) ============

def get_web_oauth_flow(redirect_uri):
    """Create OAuth flow for web-based authentication."""
    client_secret_path = get_client_secret_path()
    if not client_secret_path:
        raise FileNotFoundError("No client_secret.json found for OAuth flow")
    
    flow = Flow.from_client_secrets_file(
        client_secret_path,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    return flow


def get_auth_url(redirect_uri):
    """Generate Google OAuth URL for user to visit."""
    flow = get_web_oauth_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    return auth_url, state


def exchange_code_for_credentials(code, redirect_uri):
    """Exchange authorization code for credentials."""
    flow = get_web_oauth_flow(redirect_uri)
    flow.fetch_token(code=code)
    return flow.credentials


def credentials_from_session(session_data):
    """Reconstruct credentials from session-stored data."""
    if not session_data:
        return None
    
    try:
        creds = Credentials(
            token=session_data.get('token'),
            refresh_token=session_data.get('refresh_token'),
            token_uri=session_data.get('token_uri'),
            client_id=session_data.get('client_id'),
            client_secret=session_data.get('client_secret'),
            scopes=session_data.get('scopes')
        )
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds
    except Exception as e:
        print(f"Error reconstructing credentials: {e}")
        return None


def credentials_to_session_data(creds):
    """Convert credentials to dict for session storage."""
    return {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes) if creds.scopes else SCOPES
    }


if __name__ == "__main__":
    try:
        creds = get_google_credentials()
        print(f"Authentication successful! Type: {type(creds).__name__}")
    except Exception as e:
        print(f"Authentication failed: {e}")
