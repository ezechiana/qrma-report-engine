"""Global Arbonne catalogue, URL matching and conservative mapping candidates.
Source product URLs are catalogue observations, not guaranteed live/attributed URLs.
"""
import json,re
from pathlib import Path
from urllib.parse import urlsplit
PATH=Path(__file__).resolve().parents[1]/'data'/'global_arbonne_catalogue_2026.json'
def records(market=None,language=None):
    entries=json.loads(PATH.read_text(encoding='utf-8'))['records']
    return [x for x in entries if (market is None or x['market']==market) and (language is None or x['language']==language)]
def normalise(value):
    return re.sub(r'[^a-z0-9]+','',str(value).lower())
def match_product(name,market,language='en'):
    matches=[x for x in records(market,language) if normalise(x['name'])==normalise(name)]
    return matches[0] if len(matches)==1 else None

def market_product_link(product_name,market,language,storefront):
    """Return (url, type), never claim a storefront fallback is a product URL."""
    from app.services.arbonne_links import safe_product_link
    entry=match_product(product_name,market,language)
    if not entry:
        return storefront.url,'consultant_storefront'
    official=entry['official_catalogue_url']
    # Catalogue URLs may already have a generic 'arb/bkind' segment. Remove
    # only that known non-consultant route; preserve the market SKU/path.
    from urllib.parse import urlsplit,urlunsplit
    parts=urlsplit(official)
    prefix=f'/{market}/{language}/arb/bkind/'
    if parts.path.startswith(prefix):
        official=urlunsplit((parts.scheme,parts.netloc,f'/{market}/{language}/'+parts.path[len(prefix):],parts.query,parts.fragment))
    try:
        from app.services.arbonne_links import consultant_product_url
        return consultant_product_url(storefront,official),'catalogue_product_candidate'
    except ValueError:
        return storefront.url,'consultant_storefront'
