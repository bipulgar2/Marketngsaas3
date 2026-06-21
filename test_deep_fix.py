import sys
import traceback
sys.path.append('.')
from api.rankjacker_slides import create_rankjacker_audit_slides

data = {
    'domain_metrics': {'total_traffic': 100, 'total_keywords': 50},
    'pages': [
        {'url': 'weedposters.io/product-tag/pink-diablo/', 'traffic': 0},
        {'url': 'weedposters.io/product-tag/moon/', 'traffic': 0}
    ]
}

try:
    res = create_rankjacker_audit_slides(data, "weedposters.io")
    print("Test passed without crashing!")
except Exception as e:
    traceback.print_exc()
