import os
import requests
import json
import time

def perform_research(query, model="sonar-pro"):
    """
    Performs deep research using Perplexity API.
    Returns the research text.
    """
    api_key = os.environ.get('PERPLEXITY_API_KEY')
    if not api_key:
        print("ERROR: PERPLEXITY_API_KEY not found.")
        return None

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [{
            "role": "system",
            "content": "You are a deep research assistant. Provide comprehensive, fact-checked information with citations."
        }, {
            "role": "user",
            "content": query
        }]
    }

    try:
        # print(f"DEBUG: Calling Perplexity API ({model})...")
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"ERROR: Perplexity API returned {response.status_code}: {response.text}")
            return None
            
        result = response.json()
        return result['choices'][0]['message']['content']
            
    except Exception as e:
        print(f"ERROR: Perplexity API call failed: {str(e)}")
        return None
