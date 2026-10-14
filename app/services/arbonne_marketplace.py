"""MVP marketplace/language settings; independent of recommendation logic."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from .arbonne_links import Storefront, parse_consultant_storefront, safe_product_link

LANGUAGES_BY_MARKET = {
    'gb': ('en', 'fr', 'es'),
    'us': ('en', 'es'),
    'ca': ('en', 'fr'),
    'au': ('en',),
    'nz': ('en',),
}

@dataclass(frozen=True)
class ArbonneSettings:
    marketplace: str
    language: str
    consultant_identifier: str

    def __post_init__(self):
        if self.marketplace not in LANGUAGES_BY_MARKET:
            raise ValueError('Unsupported marketplace')
        if self.language not in LANGUAGES_BY_MARKET[self.marketplace]:
            raise ValueError('Language not configured for marketplace')
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.consultant_identifier):
            raise ValueError("Invalid consultant identifier")
        # Delegate storefront validation to the existing trusted URL parser.
        parse_consultant_storefront(self.storefront_url)

    @property
    def storefront_url(self):
        return f'https://www.arbonne.com/{self.marketplace}/{self.language}/arb/{self.consultant_identifier}'

    def product_link(self, verified_neutral_url: str | None):
        return safe_product_link(Storefront(self.marketplace, self.language, self.consultant_identifier), verified_neutral_url)


def from_storefront_url(url: str, *, marketplace: str | None = None, language: str | None = None):
    """Extract consultant ID from official URL; explicit locale settings take precedence."""
    parsed = parse_consultant_storefront(url)
    return ArbonneSettings(marketplace or parsed.market, language or parsed.language, parsed.consultant)
