import sys
import json
sys.path.append('.')
from api.dataforseo_client import get_keyword_gap

gap_results = get_keyword_gap("supergoop.com", "82e.com", filters=[["keyword_info.search_volume", ">=", 10], "and", ["ranked_serp_element.serp_item.rank_absolute", "<=", 100]])
print(json.dumps(gap_results, indent=2))
