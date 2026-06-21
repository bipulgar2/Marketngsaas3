import requests

url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
params = {
    "url": "https://weedposters.io",
    "key": "AIzaSyBSz0KCoCYy_9VSUaqVlWr-wF-BL2KdpPM",
    "strategy": "mobile",
    "category": ["performance"]
}
try:
    resp = requests.get(url, params=params)
    print("Status:", resp.status_code)
    print(resp.text[:500])
except Exception as e:
    print(e)
