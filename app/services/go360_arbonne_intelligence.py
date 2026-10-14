"""Opt-in, fail-closed, metadata-only Arbonne intelligence bridge.

No draft benefits, rationale or links are attached to client report products.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.services.go360_arbonne_intelligence_adapter import load_library, find_products, adapt_product

LIBRARY = Path(__file__).resolve().parents[1] / "data" / "go360_global_recommendation_library_DRAFT.json"

def attach_verified_intelligence_metadata(products: list[dict], *, market: str = "gb") -> list[dict]:
    """Add private audit metadata only; never alter public-facing report fields.

    Existing Arbonne products are parent-ID keyed, while new library is SKU keyed.
    Until a reviewed parent-to-market-SKU mapping exists, no automatic joins occur.
    """
    if market not in {"gb", "us", "us-es", "ca", "ca-fr", "pl", "au", "nz"}:
        raise ValueError("Unsupported Arbonne catalogue edition")
    data = load_library(LIBRARY)
    count = len(find_products(market, library=data))
    for product in products:
        product["_go360_arbonne_intelligence"] = {
            "stage": "2F.2D.11.1", "catalogue_market": market,
            "catalogue_entries": count, "matching_sku": None,
            "join_status": "PARENT_TO_SKU_MAPPING_NOT_VERIFIED",
            "client_claims_added": False, "client_links_added": False,
        }
    return products
