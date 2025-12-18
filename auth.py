"""
Google API Authentication Module
Handles OAuth authentication for Google Docs and Drive APIs
"""
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Scopes required for creating and managing documents
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'  # Full Drive access to find folders and create docs
]

CREDENTIALS_FILE = Path(__file__).parent / 'credentials.json'
TOKEN_FILE = Path(__file__).parent / 'token.json'


def get_credentials():
    """
    Load or create OAuth credentials with automatic refresh

    Returns:
        google.oauth2.credentials.Credentials: Authenticated credentials

    Raises:
        FileNotFoundError: If credentials.json is not found
        ValueError: If the credentials file is invalid
    """
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"OAuth credentials file not found: {CREDENTIALS_FILE}\n"
            "Please download credentials.json from Google Cloud Console:\n"
            "1. Go to APIs & Services > Credentials\n"
            "2. Create OAuth Client ID (Desktop app)\n"
            "3. Download JSON and save as credentials.json"
        )

    creds = None

    # Load existing token if available
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            print(f"Warning: Could not load token.json: {e}")
            creds = None

    # Refresh or create new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh expired token
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}. Re-authenticating...")
                creds = None

        if not creds:
            # Run OAuth flow (opens browser)
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as e:
                raise ValueError(f"OAuth authentication failed: {e}")

        # Save credentials for future use
        TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')

    return creds


def get_docs_service():
    """
    Create an authenticated Google Docs API service client

    Returns:
        googleapiclient.discovery.Resource: Google Docs API service
    """
    credentials = get_credentials()
    return build('docs', 'v1', credentials=credentials)


def get_drive_service():
    """
    Create an authenticated Google Drive API service client

    Returns:
        googleapiclient.discovery.Resource: Google Drive API service
    """
    credentials = get_credentials()
    return build('drive', 'v3', credentials=credentials)


def find_folder_id(folder_name='Resources'):
    """
    Find the Google Drive folder ID by name

    Args:
        folder_name: Name of the folder to find (default: 'Resources')

    Returns:
        str: The folder ID

    Raises:
        RuntimeError: If folder is not found or not accessible
    """
    try:
        drive_service = get_drive_service()

        # Search in user's Drive (no need for 'corpora' with OAuth - you own the files)
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query,
            fields='files(id, name)',
            pageSize=10
        ).execute()

        files = results.get('files', [])

        if not files:
            raise RuntimeError(
                f"Folder '{folder_name}' not found in your Google Drive.\n"
                f"Please ensure the folder exists in your Drive."
            )

        if len(files) > 1:
            print(f"Warning: Multiple folders named '{folder_name}' found. Using the first one.")

        return files[0]['id']

    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Error accessing Google Drive: {e}")
