import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from api.google_auth import get_google_credentials

def upload_image_to_drive(file_path):
    """
    Uploads an image to Google Drive and returns a public webContentLink.
    This link can be used by Google Slides API to insert the image.
    """
    try:
        creds = get_google_credentials()
        service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {'name': os.path.basename(file_path)}
        media = MediaFileUpload(file_path, mimetype='image/png')
        
        # Create file
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webContentLink, webViewLink'
        ).execute()
        
        file_id = file.get('id')
        print(f"Uploaded {file_path} to Drive ID: {file_id}")
        
        # Make public
        permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=file_id,
            body=permission,
        ).execute()
        
        return file.get('webContentLink')
        
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
        return None
