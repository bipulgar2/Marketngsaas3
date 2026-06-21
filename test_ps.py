import sys
import requests
url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=https%3A%2F%2Fweedposters.io%2F&key=AIzaSyCsbP0OhpIAOlX98V6Mg9fj7YfbUMeFJhY&strategy=desktop&category=performance&category=accessibility&category=best-practices&category=seo"
res = requests.get(url)
print(res.status_code)
print(res.text[:500])
