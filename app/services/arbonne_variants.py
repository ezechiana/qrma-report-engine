"""Arbonne variant presentation data; no SKU-to-URL guesses."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlsplit
from app.services.arbonne_links import consultant_product_url, Storefront

DATA = Path(__file__).resolve().parents[1] / 'data'

def attach_variant_links(products, *, marketplace, language, consultant):
    variants = json.loads((DATA / 'arbonne_variant_labels.json').read_text(encoding='utf-8'))
    registry_file = DATA / 'arbonne_variant_product_urls.json'
    registry = json.loads(registry_file.read_text(encoding='utf-8')) if registry_file.exists() else {}
    try:
        storefront = Storefront(str(marketplace).lower(), str(language).lower(), str(consultant)) if consultant else None
    except (TypeError, ValueError):
        storefront = None
    for product in products:
        items=[]
        for v in variants.get(str(product.get('parent_id')), []):
            official = registry.get(v['variant_id'], {}).get(str(marketplace).lower(), {}).get(str(language).lower())
            link = None
            if official and storefront:
                try:
                    link = consultant_product_url(storefront, official)
                except (ValueError, AttributeError):
                    pass
            items.append({'label':v['label'], 'variant_id':v['variant_id'], 'url':link})
        product['variants'] = items
    return products
