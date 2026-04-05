# app/services/product_ranker.py

from __future__ import annotations
from typing import List, Dict, Any


def score_product(product: Dict[str, Any]) -> float:
    """
    Score product importance based on:
    - primary pattern alignment
    - number of supporting sections
    - source weighting
    """

    score = 0

    # Pattern alignment
    if product.get("is_primary"):
        score += 5

    if product.get("pattern_alignment"):
        score += 3

    # Breadth of support
    sections = product.get("supporting_sections") or []
    score += min(len(sections), 5)

    # Source weighting
    source = (product.get("source") or "").lower()

    if source == "natural_approaches":
        score += 3
    elif source == "custom":
        score += 4
    elif source == "vitalhealth":
        score += 1

    return score

def rank_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(products, key=score_product, reverse=True)


def deduplicate_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Prevent duplicate or overlapping products
    """
    seen = set()
    unique = []

    for p in products:
        key = (p.get("name") or "").lower()

        if key in seen:
            continue

        seen.add(key)
        unique.append(p)

    return unique


def limit_products(products: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    return products[:limit]