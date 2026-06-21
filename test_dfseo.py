from api.dataforseo_client import get_domain_rank_overview
from dotenv import load_dotenv
load_dotenv()
res = get_domain_rank_overview("weedposters.io")
print("DataForSEO Labs Result:", res)
