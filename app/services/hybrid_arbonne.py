"""Additive market-aware Arbonne ranking; name-only mappings are provisional.
No edits to VitalHealth or Natural Approaches paths.
"""
import json
from pathlib import Path
from app.services.scoring_engine import SECTION_WEIGHTS, MARKER_WEIGHTS, DEFAULT_SECTION_WEIGHT
from app.services.product_resolver import _collect_flagged_sections
DATA=Path(__file__).resolve().parents[1]/'data'

def rank(report,market='gb',language='en',limit=10,include_unmapped=False):
    catalogue=json.loads((DATA/'global_arbonne_catalogue_2026.json').read_text(encoding='utf8'))['records']
    matrix=json.loads((DATA/'arbonne_hybrid_matrix.json').read_text(encoding='utf8'))['rows']
    lookup={(x['market'],x['language'],str(x['sku'])):x for x in matrix}
    sections=_collect_flagged_sections(report)
    ranked=[]
    for row in catalogue:
        if row.get('market')!=market or row.get('language')!=language or not row.get('recommendation_eligible'):continue
        rule=lookup.get((market,language,str(row['sku'])))
        if not rule:continue
        relevance=rule.get('category_relevance') or {}
        if not relevance and not include_unmapped:continue
        matches=[]
        # Match exact explicit topic words against flagged section titles only.
        # No inferred medical/product benefits, and no fabricated marker associations.
        for sec in sections:
            title=str(sec.get('title','')).lower()
            for topic,rel in relevance.items():
                if topic in title:
                    w=float(SECTION_WEIGHTS.get(title,DEFAULT_SECTION_WEIGHT))
                    matches.append((title,topic,rel*w))
        if not matches and not include_unmapped:continue
        score=sum(x[2] for x in matches)
        item={'name':row['name'],'sku':row['sku'],'source':'arbonne','source_label':'Arbonne',
              'market':market,'language':language,'official_catalogue_url':row.get('official_catalogue_url'),
              'image_local_path':row.get('image_local_path'),'image_match_status':row.get('image_match_status'),
              'supporting_sections':list(dict.fromkeys(x[0] for x in matches))[:5],
              'supporting_markers':[],'marker_evidence_status':'not_established',
              'mapping_status':rule['mapping_status'],'_score':round(score,3),
              'rationale':'Catalogue title topic overlap with prioritised report sections; provisional relevance only.',
              'url':None,'link_type':'unverified'}
        ranked.append(item)
    ranked.sort(key=lambda x:(-x['_score'],x['name'],str(x['sku'])))
    return ranked[:max(0,int(limit))] if limit is not None else ranked
