"""Arbonne-only demo relevance correction.

Matches explicit product-purpose tokens to *specific* flagged QRMA sections.
Does not infer ingredients, therapeutic efficacy, or marker-level evidence.
The full catalogue remains searchable; products without defensible topic
matches are excluded rather than used to fill a fixed recommendation count.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from app.services.global_catalogue import records
from app.services.arbonne_links import Storefront, consultant_product_url
from app.services.scoring_engine import SECTION_WEIGHTS, DEFAULT_SECTION_WEIGHT
from app.services.product_resolver import _collect_flagged_sections
DATA = Path(__file__).resolve().parents[1] / 'data'

def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()

# Exact purpose-to-assessment bridges; *not* claims that a product treats a condition.
# Generic tokens (vitamin, mineral, collagen, antioxidant, etc.) intentionally do
# not bridge into unrelated high-weight systems.
TOPICS = {
    'digestive': (('gut', 'digest', 'microbiome', 'probiotic', 'prebiotic', 'fiber', 'fibre', 'intestinal'),
                  ('gastrointestinal function', 'large intestine function', 'stomach function', 'small intestine function')),
    'liver': (('liver support',), ('liver function',)),
    'glucose': (('blood sugar', 'glucose'), ('blood sugar',)),
    'cardiovascular': (('heart health', 'cardiovascular'), ('cardiovascular and cerebrovascular',)),
    'skin': (('skin', 'dermresults', 'bodycare', 'lip balm', 'eye mask', 'eye masks', 'face', 'facial', 'moisturiz', 'cleanser', 'serum', 'cream'),
             ('skin', 'skin function', 'skin index')),
    'bone': (('bone support', 'bone health'), ('bone mineral density', 'bone disease')),
    'protein': (('protein',), ('protein', 'amino acid', 'muscle')),
    'energy': (('energy',), ('energy', 'coenzyme', 'physical quality')),
}
# Broad nutritional products are eligible for a modest *foundation* match only
# when the assessment includes a corresponding nutrition category.
FOUNDATION = ('multivitamin', 'multi vitamin', 'vitamin & mineral', 'vitamin and mineral', 'greens', 'superfood')
FOUNDATION_SECTIONS = ('vitamin', 'trace element', 'amino acid')

# B.3: context descriptions use explicit matched topics and report sections only.
# They are not assertions of treatment efficacy or confirmed QRMA diagnoses.
TOPIC_ROLE = {
    'digestive': 'Digestive wellness product category',
    'liver': 'Liver wellness product category',
    'glucose': 'Blood sugar wellness product category',
    'cardiovascular': 'Cardiovascular wellness product category',
    'skin': 'Skin care product category',
    'bone': 'Bone wellness product category',
    'protein': 'Protein nutrition product category',
    'energy': 'Energy product category',
    'foundation nutrition': 'General nutritional support category',
}

def _context_copy(name, topics, sections):
    role = '; '.join(TOPIC_ROLE[t] for t in topics if t in TOPIC_ROLE)
    if not role:
        role = 'Product category for individual suitability review'
    context = ', '.join(str(s) for s in sections[:3])
    # Product name is a catalogue identity, not evidence of clinical efficacy.
    rationale = (f'{name}: considered under {role.lower()} in relation to '
                 f'assessment sections {context}. This is a category-based match, '
                 'not evidence of a deficiency, diagnosis or treatment benefit; '
                 'individual suitability requires review.')
    return role, rationale

def _image_url(row):
    path = str(row.get('image_local_path') or '').replace('\\', '/')
    if path.startswith('app/static/') and '..' not in path.split('/'):
        return '/static/' + path[len('app/static/'):]
    return None

def _consultant_url(row, market, language, consultant):
    if not consultant:
        return None, 'not_configured'
    official = str(row.get('official_catalogue_url') or '')
    try:
        parts = urlsplit(official)
        path = parts.path
        prefix = f'/{market}/{language}/arb/'
        if path.startswith(prefix):
            remaining = path[len(prefix):].split('/', 1)
            if len(remaining) != 2:
                return None, 'storefront_only'
            official = urlunsplit((parts.scheme, parts.netloc, f'/{market}/{language}/' + remaining[1], parts.query, parts.fragment))
        url = consultant_product_url(Storefront(market, language, consultant), official)
        return url, 'consultant_product_url_candidate'
    except (ValueError, TypeError):
        return None, 'storefront_only'

def resolve(report, market='gb', language='en', consultant=None, limit=10):
    sections = _collect_flagged_sections(report)
    if not sections:
        return []
    active = [(_norm(s.get('title')), str(s.get('title'))) for s in sections]
    candidates = []
    for row in records(market, language):
        if row.get('recommendation_eligible') is not True:
            continue
        title = _norm(row.get('name'))
        matches = []
        matched_topics = []
        for topic, (product_tokens, section_tokens) in TOPICS.items():
            if not any(token in title for token in product_tokens):
                continue
            for section_norm, section_title in active:
                if any(section_norm == token or section_norm.startswith(token + ' ') for token in section_tokens):
                    weight = float(SECTION_WEIGHTS.get(section_norm, DEFAULT_SECTION_WEIGHT))
                    matches.append((section_title, weight * 0.75))
                    matched_topics.append(topic)
        if any(token in title for token in FOUNDATION):
            for section_norm, section_title in active:
                if section_norm in FOUNDATION_SECTIONS:
                    weight = float(SECTION_WEIGHTS.get(section_norm, DEFAULT_SECTION_WEIGHT))
                    matches.append((section_title, weight * 0.25))
                    matched_topics.append('foundation nutrition')
        if not matches:
            continue
        # A single section contributes at most once; no topic stacking.
        per_section = {}
        for section, contribution in matches:
            per_section[section] = max(per_section.get(section, 0), contribution)
        score = round(sum(per_section.values()), 4)
        url, link_type = _consultant_url(row, market, language, consultant)
        # GO360_ARB_CATEGORY_STAGE_V1 — additive product metadata.
        from app.services.arbonne_categories import classify, display_name
        _category = classify(row.get('name'))
        _role, _rationale = _context_copy(str(row.get('name') or ''), list(dict.fromkeys(matched_topics)), list(per_section)[:5])
        candidates.append({
            **_category,
            'product_name_official': None,
            'product_name_display': display_name(row.get('name')),
            'product_name_normalized': str(row.get('name') or '').lower(),
            'name': row.get('name'), 'sku': row.get('sku'), 'source': 'arbonne',
            'source_label': 'Arbonne', 'market': market, 'language': language,
            'supporting_sections': list(per_section)[:5], 'supporting_markers': [],
            'marker_evidence_status': 'not_established',
            'mapping_status': 'DEMO_EXPLICIT_PURPOSE_BRIDGE',
            'matched_topics': list(dict.fromkeys(matched_topics)),
            'role': _role, 'rationale': _rationale,
            'image_url': _image_url(row), 'image_match_status': row.get('image_match_status'),
            'official_catalogue_url': row.get('official_catalogue_url'),
            'url': url, 'link_type': link_type, '_score': score,
            'support_phase': 2, 'support_phase_label': 'Phase 2 — Targeted Support',
        })
    candidates.sort(key=lambda x: (-x['_score'], str(x['name']), str(x['sku'])))
    unique, seen = [], set()
    for row in candidates:
        key = str(row['sku'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique if limit is None else unique[:max(0, int(limit))]


def resolve_by_category(report, market='gb', language='en', consultant=None, limit=10):
    """Rank each category against the full eligible market catalogue, independently."""
    from app.services.arbonne_categories import classify
    pool = resolve(report, market=market, language=language, consultant=consultant, limit=None)
    keys = ('nutrition_wellness', 'beauty_skincare', 'body_personal_care')
    output = {key: [] for key in keys}
    for item in pool:
        key = item.get('product_category') or classify(item.get('name')).get('product_category')
        if key in output:
            output[key].append(item)
    if limit is not None:
        cap = max(0, int(limit))
        output = {key: rows[:cap] for key, rows in output.items()}
    return output
