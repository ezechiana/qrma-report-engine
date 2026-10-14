"""Arbonne MVP adapter: reuses GO360 VitalHealth mechanism inference/scoring primitives.

Development mode allows all mapped catalogue products to be tested.
No existing VitalHealth source files are modified by this component.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any
from app.data.vitalhealth_mechanism_map import (resolve_section_mechanisms, resolve_pattern_mechanisms, merge_mechanism_sets)
from app.services.product_resolver import (_collect_flagged_sections, _pattern_keys_for_products, _accumulate_weight_map, _score_overlap, _mechanism_threshold, GENERIC_MECHANISM_KEYS, SPECIFIC_FOCUS_AREAS, GENERIC_FOCUS_AREAS)

DATA = Path(__file__).resolve().parents[1] / 'data'

def resolve_arbonne_products(report: Any, *, mappings: list[dict] | None = None, catalogue: dict | None = None, max_results: int = 5) -> list[dict]:
    if mappings is None:
        mappings = json.loads((DATA/'arbonne_mechanism_mappings.json').read_text(encoding='utf-8'))
    if catalogue is None:
        catalogue = json.loads((DATA/'arbonne_catalogue_clean.json').read_text(encoding='utf-8'))
    parents = {p['parent_id']: p for p in catalogue['parents']}
    # MVP: eligibility and mapping enablement are environment-independent.
    flagged = _collect_flagged_sections(report)
    sections = [s['title'] for s in flagged]
    pattern_keys = _pattern_keys_for_products(report)
    section_resolution = resolve_section_mechanisms(sections)
    pattern_resolution = resolve_pattern_mechanisms(pattern_keys)
    resolved = merge_mechanism_sets(section_resolution, pattern_resolution)
    mw = _accumulate_weight_map(resolved.get('mechanisms', []))
    cw = _accumulate_weight_map(resolved.get('categories', []))
    # Conservative category bridge for a general multivitamin: this is NOT
    # a claim of deficiency, a validated QRMA micronutrient measurement,
    # or an activation of micronutrient_repletion.
    if cw.get('foundation_nutrition', 0) > 0:
        mw['vitamin_mineral_support'] = max(mw.get('vitamin_mineral_support', 0), 1)
    for key in GENERIC_MECHANISM_KEYS:
        if key in mw: mw[key] = max(0, mw[key] - 4)
    output=[]
    # Generic foundation-category matches alone do not establish a need for a
    # specific supplement. Require a non-generic matching mechanism for
    # targeted products; foundation candidates are reviewed separately.
    FOUNDATION_IDS = {'ARB-P019', 'ARB-P015', 'ARB-P003', 'ARB-P001', 'ARB-P002'}
    ALTERNATIVE_GROUPS = {
        'ARB-P001': 'protein', 'ARB-P002': 'protein', 'ARB-P003': 'protein',
        'ARB-P019': 'greens_elixir', 'ARB-P006': 'greens_elixir',
        'ARB-P010': 'cleanse', 'ARB-P011': 'cleanse',
    }
    for rule in mappings:
        if rule.get('enabled') is not True: continue
        parent = parents.get(rule.get('parent_id'))
        if not parent or str(parent.get('recommendation_eligible')).lower() != 'true': continue
        ms, matched_m = _score_overlap(mw, rule.get('mechanisms', []))
        cs, matched_c = _score_overlap(cw, rule.get('categories', []))
        if not matched_m and not matched_c: continue
        score = ms + cs + (3 if matched_m and matched_c else 0)
        focus = matched_m[0] if matched_m else None
        if focus in SPECIFIC_FOCUS_AREAS: score += 2
        if focus in GENERIC_FOCUS_AREAS: score -= 3
        if score < _mechanism_threshold(report) and rule.get('parent_id') != 'ARB-P015': continue
        if len(matched_m) < 2 and not matched_c: continue
        # Context is only claimed when a rule actually matches the
        # corresponding section/pattern vocabulary. No first-five-marker
        # fallback: those markers are not validated product evidence.
        def overlap(resolution):
            mechanism_names = {x.get('name') for x in resolution.get('mechanisms', [])}
            category_names = {x.get('name') for x in resolution.get('categories', [])}
            return bool(mechanism_names.intersection(matched_m) or category_names.intersection(matched_c))
        contributing_sections = [title for title in sections if overlap(resolve_section_mechanisms([title]))][:4]
        contributing_patterns = [key for key in pattern_keys if overlap(resolve_pattern_mechanisms([key]))][:4]
        pid = rule.get('parent_id')
        # Development narrative: describe the matched wellness theme and the
        # product's general role. Never expose internal review-status language.
        context = ', '.join(contributing_sections[:2])
        theme = (context.lower() if context else 'your current wellness priorities')
        if pid == 'ARB-P019':
            role = 'A greens-format addition to your everyday nutrition routine.'
        elif pid == 'ARB-P015':
            role = 'A vitamin-and-mineral option for a consistent nutritional foundation.'
        elif pid == 'ARB-P003':
            role = 'A meal-replacement option to consider alongside your dietary and protein goals.'
        elif pid in {'ARB-P001', 'ARB-P002'}:
            role = 'A protein-shake option to consider alongside your food intake and activity goals.'
        elif pid == 'ARB-P006':
            role = 'An elixir-format option to explore as part of your daily nutrition routine.'
        elif pid == 'ARB-P016':
            role = 'A mushroom-format nutrition option to explore alongside your wellbeing goals.'
        else:
            mechanism = (matched_m[0] if matched_m else '')
            category = (matched_c[0] if matched_c else '')
            topic = (mechanism or category).replace('_', ' ').strip()
            role = ('An option to explore for ' + topic + ' within your personal wellness plan.') if topic else 'An option to discuss in the context of your personal wellness goals.'
        reason = role
        # A candidate mapping is not a verified product-to-marker relationship.
        is_foundation = pid in FOUNDATION_IDS
        phase = 1 if is_foundation else 2
        output.append({'name': parent['parent_name'], 'parent_id': parent['parent_id'],
                       'source':'arbonne', 'source_label':'Arbonne',
                       'supporting_sections':contributing_sections,
                       'supporting_markers':[], 'marker_evidence_status':'not_established',
                       'contributing_patterns':contributing_patterns,
                       'pattern_alignment':', '.join(matched_c[:2]).replace('_',' ') or None,
                       'matched_mechanisms':matched_m, 'matched_categories':matched_c,
                       'focus_area':focus, 'is_primary':is_foundation, 'support_phase':phase, 'support_phase_label':('Phase 1 — Foundational Support' if is_foundation else 'Phase 2 — Targeted Support'),
                       'rationale':reason,
                       'verified_neutral_product_url':None,
                       '_score':score, 'url':None})
    # Avoid treating alternate protein formats as distinct clinical needs.
    # Other providers and VitalHealth remain untouched.
    ranked = sorted(output, key=lambda p: (-p['_score'], p['name']))
    selected = []
    seen_families = set()
    for product in ranked:
        pid = product['parent_id']
        family = ALTERNATIVE_GROUPS.get(pid)
        if family and family in seen_families:
            continue
        selected.append(product)
        if family:
            seen_families.add(family)
        if len(selected) >= max_results:
            break
    return selected
