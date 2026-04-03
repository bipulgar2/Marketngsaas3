import requests
import json

class WebflowClient:
    def __init__(self):
        self.base_url = "https://api.webflow.com/v2"

    def list_sites(self, api_key):
        """Lists all sites for the authenticated user."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json"
        }
        try:
            url = f"{self.base_url}/sites"
            with open('webflow_debug.log', 'a') as f:
                f.write(f"DEBUG: Requesting Webflow URL: {url}\n")
            
            response = requests.get(url, headers=headers)
            
            with open('webflow_debug.log', 'a') as f:
                f.write(f"DEBUG: Response Status: {response.status_code}\n")
                f.write(f"DEBUG: Response Body: {response.text}\n")

            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return data.get('sites', [])
        except Exception as e:
            print(f"Error listing sites: {e}", flush=True)
            if hasattr(e, 'response') and e.response:
                print(f"Webflow API Response: {e.response.text}", flush=True)
                raise Exception(f"Webflow API Error: {e} - {e.response.text}")
            raise e

    def list_collections(self, api_key, site_id):
        """Lists all CMS collections for a specific site."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json"
        }
        try:
            url = f"{self.base_url}/sites/{site_id}/collections"
            print(f"DEBUG: Requesting Webflow URL: {url}", flush=True)
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return data.get('collections', [])
        except Exception as e:
            print(f"Error listing collections: {e}", flush=True)
            if hasattr(e, 'response') and e.response:
                print(f"Webflow API Response: {e.response.text}", flush=True)
                raise Exception(f"Webflow API Error: {e} - {e.response.text}")
            raise e

    def upload_asset(self, api_key, site_id, file_path):
        """
        Uploads an asset (image) to Webflow and returns the asset object.
        This is required for CMS image fields - you can't just pass a URL.
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json"
        }
        
        try:
            # Read the file
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # Get filename from path
            import os
            filename = os.path.basename(file_path)
            
            # Upload to Webflow
            url = f"{self.base_url}/sites/{site_id}/assets"
            files = {
                'file': (filename, file_data, 'image/jpeg')
            }
            
            print(f"DEBUG: Uploading asset to Webflow: {url}", flush=True)
            response = requests.post(url, headers=headers, files=files)
            response.raise_for_status()
            
            asset = response.json()
            print(f"DEBUG: Asset uploaded successfully. ID: {asset.get('id')}", flush=True)
            return asset
            
        except Exception as e:
            print(f"Error uploading asset: {e}")
            if hasattr(e, 'response') and e.response:
                print(f"Webflow API Response: {e.response.text}")
            raise e

    def create_item(self, api_key, collection_id, fields, is_draft=True):
        """Creates a new item in a CMS collection."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json"
        }
        
        payload = {
            "fieldData": fields,
            "isDraft": is_draft,
            "isArchived": False
        }
        
        try:
            url = f"{self.base_url}/collections/{collection_id}/items"
            print(f"DEBUG: Requesting Webflow URL: {url}", flush=True)
            print(f"DEBUG: Webflow Payload (Inside Client): {json.dumps(payload, indent=2)}", flush=True)
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error creating item: {e}")
            if hasattr(e, 'response') and e.response:
                print(f"Webflow API Response: {e.response.text}")
            raise e

    def list_items(self, api_key, collection_id, limit=100, offset=0):
        """Lists items in a CMS collection."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json"
        }
        
        try:
            url = f"{self.base_url}/collections/{collection_id}/items?limit={limit}&offset={offset}"
            print(f"DEBUG: Requesting Webflow URL: {url}", flush=True)
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and 'items' in data:
                return data['items']
            return data # Fallback if list
        except Exception as e:
            print(f"Error listing items: {e}")
            raise e

    def update_item(self, api_key, collection_id, item_id, fields, is_draft=False):
        """Updates an existing item in a CMS collection."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json"
        }
        
        payload = {
            "fieldData": fields,
            "isDraft": is_draft,
            "isArchived": False
        }
        
        try:
            url = f"{self.base_url}/collections/{collection_id}/items/{item_id}"
            print(f"DEBUG: Updating Webflow Item: {url}", flush=True)
            response = requests.patch(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error updating item: {e}")
            if hasattr(e, 'response') and e.response:
                print(f"Webflow API Response: {e.response.text}")
            raise e

webflow_client = WebflowClient()
