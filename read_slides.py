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
slides_service = build('slides', 'v1', http=authorized_http)

presentation_id = '1qXzhrZRuDdyJQS9XTgfcaY8clTitvXm8Pss7h2V-rEc'
presentation = slides_service.presentations().get(presentationId=presentation_id).execute()

for i, slide in enumerate(presentation.get('slides', [])):
    print(f"\n--- Slide {i+1} ---")
    for element in slide.get('pageElements', []):
        if 'shape' in element and 'text' in element['shape']:
            text_elements = element['shape']['text'].get('textElements', [])
            text = "".join([t.get('textRun', {}).get('content', '') for t in text_elements])
            if "{{" in text or "[" in text:
                print(text.strip())

