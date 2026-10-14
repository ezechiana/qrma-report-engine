"""Read-only market/SKU product-media join for GO360 global catalogue."""
from __future__ import annotations
import json
from pathlib import Path
DATA=Path(__file__).resolve().parents[1]/'data'
ROOT=Path(__file__).resolve().parents[2]

def lookup(market:str,sku:str,language:str='en'):
    rows=json.loads((DATA/'global_arbonne_catalogue_2026.json').read_text(encoding='utf-8'))['records']
    found=[r for r in rows if str(r.get('market','')).lower()==market.lower() and str(r.get('sku','')).strip().upper()==str(sku).strip().upper() and r.get('language')==language]
    if not found: return None
    item=dict(found[0]); rel=item.get('image_local_path')
    item['image_available']=bool(rel and (ROOT/rel).is_file())
    item['image_web_path']='/static/'+rel.split('app/static/',1)[1].replace('\\','/') if item['image_available'] and rel.startswith('app/static/') else None
    return item

def market_inventory(market:str,language:str='en'):
    rows=json.loads((DATA/'global_arbonne_catalogue_2026.json').read_text(encoding='utf-8'))['records']
    return [r for r in rows if r.get('market')==market.lower() and r.get('language')==language]
