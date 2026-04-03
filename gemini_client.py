import os
import requests
import json
import time
import base64

def generate_content(prompt, model_name="gemini-2.5-pro", temperature=0.7, use_grounding=False):
    """
    Generates content using the Gemini REST API directly via requests.
    This avoids SDK compatibility issues on Railway/Linux.
    
    Args:
        prompt (str): The text prompt to send.
        model_name (str): The model to use (e.g., "gemini-2.5-pro", "gemini-2.5-flash").
        temperature (float): Controls randomness (0.0 to 1.0).
        use_grounding (bool): Whether to enable Google Search Grounding.
        
    Returns:
        str: The generated text content.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not found in environment variables.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 16384
        }
    }
    
    if use_grounding:
        payload["tools"] = [{
            "google_search": {}
        }]
    
    try:
        # print(f"DEBUG: Calling Gemini REST API ({model_name})...")
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"ERROR: Gemini API returned {response.status_code}: {response.text}")
            return None
            
        result = response.json()
        
        # Extract text from response
        # Extract text from response
        try:
            candidate = result['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content']:
                return candidate['content']['parts'][0]['text']
            else:
                print(f"WARNING: Gemini returned no text content. FinishReason: {candidate.get('finishReason')}")
                return ""
        except (KeyError, IndexError) as e:
            print(f"ERROR: Unexpected response structure from Gemini: {result}")
            return None
            
    except Exception as e:
        print(f"ERROR: Gemini REST API call failed: {str(e)}")
        return None

def generate_image(prompt, output_path, model_name="gemini-2.5-flash-image", input_image_data=None, aspect_ratio="16:9"):
    """
    Generates an image using the Gemini REST API.
    Supports optional input_image_data (base64 string) for image-to-image tasks.
    aspect_ratio: "16:9", "4:3", "3:4", "1:1", "9:16"
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not found.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    parts = [{"text": prompt}]
    
    if input_image_data:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg", # Assuming JPEG for now, or detect?
                "data": input_image_data
            }
        })
    
    payload = {
        "contents": [{
            "parts": parts
        }],
        "generationConfig": {
            "response_modalities": ["IMAGE"],
            "image_config": {
                "aspectRatio": aspect_ratio
            }
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            print(f"ERROR: Gemini Image API returned {response.status_code}: {response.text}")
            return None
            
        result = response.json()
        
        # Extract image data
        try:
            image_b64 = result['candidates'][0]['content']['parts'][0]['inlineData']['data']
            image_data = base64.b64decode(image_b64)
            
            # Re-encode with PIL to ensure valid JPEG format
            from PIL import Image
            import io
            
            # Load image from bytes
            img = Image.open(io.BytesIO(image_data))
            
            # Convert to RGB if needed (remove alpha channel, ensure 8-bit RGB)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Save as high-quality JPEG
            img.save(output_path, 'JPEG', quality=95, optimize=True)
            
            return output_path
        except (KeyError, IndexError) as e:
            print(f"ERROR: Unexpected image response structure: {result}")
            return None
            
    except Exception as e:
        print(f"ERROR: Gemini Image API call failed: {str(e)}")
        return None

