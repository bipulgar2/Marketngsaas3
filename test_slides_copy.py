from api.google_auth import get_google_credentials
from googleapiclient.discovery import build
import httplib2
import ssl
from google_auth_httplib2 import AuthorizedHttp

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

creds = get_google_credentials()
http = httplib2.Http(disable_ssl_certificate_validation=True)
authorized_http = AuthorizedHttp(creds, http=http)
drive_service = build('drive', 'v3', http=authorized_http)
slides_service = build('slides', 'v1', http=authorized_http)

template_id = '1qXzhrZRuDdyJQS9XTgfcaY8clTitvXm8Pss7h2V-rEc'
body = {'name': 'Test RankJacker Copy'}
response = drive_service.files().copy(fileId=template_id, body=body).execute()
new_id = response.get('id')

print(f"Created new presentation: {new_id}")
