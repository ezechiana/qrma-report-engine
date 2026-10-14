"""GO360 Arbonne draft library adapter. Standard-library only; no app/db mutations."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

LIBRARY = Path(__file__).with_name('go360_global_recommendation_library_DRAFT.json')
VALID_MARKETS = {'gb', 'us', 'us-es', 'ca', 'ca-fr', 'pl', 'au', 'nz'}


def load_library(path=LIBRARY):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data.get('products'), list):
        raise ValueError('Missing products list')
    return data


def find_products(market, sku=None, goal=None, library=None):
    if market not in VALID_MARKETS:
        raise ValueError('Unknown catalogue edition')
    data = library if library is not None else load_library()
    return [p for p in data['products'] if p.get('market') == market
            and (sku is None or str(p.get('sku')) == str(sku))
            and (goal is None or p.get('wellness_goal_candidate') == goal)]


def attributed_url(product, consultant_config=None):
    """Only return attributed URL when an exact market+SKU URL has been externally verified.

    Config format: {'verified_urls': [{'market':'gb','sku':'1234','url':'https://...',
    'verified':true,'consultant_id':'...'}]}. Never infer query parameter names.
    """
    config = consultant_config or {}
    for item in config.get('verified_urls', []):
        if (item.get('market') == product.get('market')
                and str(item.get('sku')) == str(product.get('sku'))
                and item.get('verified') is True
                and item.get('consultant_id')
                and isinstance(item.get('url'), str)):
            url = item['url']
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in {'arbonne.com', 'www.arbonne.com'}:
                continue
            if '/p/' not in parsed.path:
                continue
            return url
    return None


def adapt_product(product, *, consultant_config=None, preview=False):
    """Fail closed for live recommendations; preview is explicitly non-publication."""
    url = attributed_url(product, consultant_config)
    eligible = (product.get('catalogue_2026_listed') is True
                and product.get('product_claim_verified') is True
                and product.get('formulation_verified') is True
                and product.get('recommendation_eligible') is True
                and url is not None)
    result = {
        'sku': str(product.get('sku', '')),
        'market': product.get('market'),
        'language': product.get('language'),
        'product': product.get('product_name'),
        'wellness_goal': product.get('wellness_goal_candidate') or None,
        'eligible_for_client_report': bool(eligible),
        'why_recommended': product.get('why_recommended_draft') if eligible else None,
        'benefits': product.get('benefits_draft', []) if eligible else [],
        'consultant_product_url': url if eligible else None,
        'source': {'epub': product.get('source_epub'), 'page': product.get('source_page'),
                   'catalogue_url': product.get('catalogue_url'),
                   'claim_evidence_id': product.get('claim_evidence_id')},
        'verification': {
            'claim_status': product.get('claim_status'),
            'product_claim_verified': product.get('product_claim_verified') is True,
            'formulation_verified': product.get('formulation_verified') is True,
            'consultant_url_verified': url is not None,
            'publication_state': product.get('publication_state')},
    }
    if preview:
        result['preview_only_NOT_FOR_PUBLICATION'] = True
        result['candidate_why_recommended'] = product.get('why_recommended_draft') or None
        result['candidate_benefits'] = product.get('benefits_draft') or []
        result['candidate_source_quote'] = product.get('source_quote') or None
    return result


def build_payload(market, *, sku=None, goal=None, preview=False, consultant_config=None, library=None):
    matches = find_products(market, sku=sku, goal=goal, library=library)
    adapted = [adapt_product(p, consultant_config=consultant_config, preview=preview) for p in matches]
    return {'stage': '2F.2D.10', 'market': market, 'preview': preview,
            'catalogue_matches': len(adapted),
            'eligible_recommendations': sum(p['eligible_for_client_report'] for p in adapted),
            'products': adapted}


def main():
    parser = argparse.ArgumentParser(description='GO360 read-only Arbonne intelligence adapter')
    parser.add_argument('--market', choices=sorted(VALID_MARKETS), required=True)
    parser.add_argument('--sku')
    parser.add_argument('--goal')
    parser.add_argument('--preview', action='store_true', help='Expose unverified draft text with warning')
    parser.add_argument('--consultant-config', type=Path, help='Exact SKU URLs verified independently')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    config = json.loads(args.consultant_config.read_text(encoding='utf-8')) if args.consultant_config else None
    payload = build_payload(args.market, sku=args.sku, goal=args.goal,
                            preview=args.preview, consultant_config=config)
    result = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(result + '\n', encoding='utf-8')
    else:
        print(result)

if __name__ == '__main__':
    main()
