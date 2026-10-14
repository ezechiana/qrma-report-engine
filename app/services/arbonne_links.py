"""Arbonne consultant storefront and product link helpers.

Observed URL conventions only; not an official Arbonne API. Python stdlib only.
No network access, no changes to existing product resolver.
"""
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit
import re

_ALLOWED_MARKETS = {'gb', 'us', 'ca', 'au', 'nz'}  # observed examples, expand after verification
_ALLOWED_LANGUAGES = {'en', 'fr', 'es'}
_SEGMENT = re.compile(r'^[A-Za-z0-9_-]+$')


@dataclass(frozen=True)
class Storefront:
    market: str
    language: str
    consultant: str

    @property
    def url(self) -> str:
        return f'https://www.arbonne.com/{self.market}/{self.language}/arb/{self.consultant}'


def parse_consultant_storefront(url: str) -> Storefront:
    parts = urlsplit(url.strip())
    if parts.scheme != 'https' or parts.hostname not in ('www.arbonne.com', 'arbonne.com') or parts.username or parts.password or parts.port:
        raise ValueError('Use an official HTTPS arbonne.com storefront URL')
    segments = [unquote(p) for p in parts.path.split('/') if p]
    if len(segments) < 4 or segments[2] != 'arb':
        raise ValueError('Expected /{market}/{language}/arb/{consultant}')
    market, language, _, consultant = segments[:4]
    if market not in _ALLOWED_MARKETS or language not in _ALLOWED_LANGUAGES or not _SEGMENT.fullmatch(consultant):
        raise ValueError('Unsupported locale or invalid consultant identifier')
    return Storefront(market, language, consultant)


def consultant_product_url(storefront: Storefront, official_product_url: str) -> str:
    """Insert consultant route into an *already verified* official market+locale product URL.

    Never synthesizes product slugs, translates URLs, or changes market SKUs.
    """
    parts = urlsplit(official_product_url.strip())
    if parts.scheme != 'https' or parts.hostname not in ('www.arbonne.com', 'arbonne.com') or parts.username or parts.password or parts.port:
        raise ValueError('Product URL must be official HTTPS arbonne.com')
    path = parts.path
    prefix = f'/{storefront.market}/{storefront.language}/'
    if not path.startswith(prefix):
        raise ValueError('Product locale must match consultant storefront locale')
    remainder = path[len(prefix):]
    if remainder.startswith('arb/'):
        raise ValueError('Supply a neutral official product URL, not another consultant link')
    if not remainder or '/p/' not in '/' + remainder or remainder.endswith('/p/'):
        raise ValueError('Expected a product detail URL with /p/{market-specific-sku}')
    if any(x in remainder.lower() for x in ('%2f', '%5c')) or '\\' in remainder:
        raise ValueError('Invalid encoded product path')
    # Preserve exact official path, including percent-encoded language slugs and SKU.
    return f'{storefront.url}/{remainder}'


def safe_product_link(storefront: Storefront, official_product_url: str | None) -> str:
    """Fallback to consultant storefront when product URL is absent/unverified/incompatible."""
    if not official_product_url:
        return storefront.url
    try:
        return consultant_product_url(storefront, official_product_url)
    except ValueError:
        return storefront.url
