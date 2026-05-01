from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

VALID_FEE_MODELS = {
    "platform_absorbs",
    "practitioner_absorbs",
    "split_proportional",
}


def get_revenue_fee_model() -> str:
    """
    Controls how Stripe processing fees are represented in revenue reporting.

    platform_absorbs:
        Practitioner payout = gross - platform commission.
        Platform net = platform commission - Stripe fee.

    practitioner_absorbs:
        Practitioner payout = gross - platform commission - Stripe fee.
        Platform net = platform commission.

    split_proportional:
        Stripe fee is split between platform and practitioner according to their
        share of the gross payment.
    """
    value = os.getenv("REVENUE_FEE_MODEL", "platform_absorbs").strip().lower()
    return value if value in VALID_FEE_MODELS else "platform_absorbs"


def _to_decimal_minor(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_revenue_split(
    *,
    gross_minor: int | None,
    commission_minor: int | None,
    stripe_fee_minor: int | None = 0,
    fee_model: str | None = None,
) -> dict[str, int | str]:
    """
    Calculate a bundle's revenue allocation in Stripe minor units.

    This function deliberately works in minor units so we avoid currency rounding
    errors and keep the API aligned with Stripe values.
    """
    model = (fee_model or get_revenue_fee_model()).strip().lower()
    if model not in VALID_FEE_MODELS:
        model = "platform_absorbs"

    gross = _to_decimal_minor(gross_minor)
    commission = max(Decimal("0"), min(_to_decimal_minor(commission_minor), gross))
    stripe_fee = max(Decimal("0"), _to_decimal_minor(stripe_fee_minor))

    practitioner_before_fee = max(gross - commission, Decimal("0"))

    if model == "practitioner_absorbs":
        practitioner = max(practitioner_before_fee - stripe_fee, Decimal("0"))
        platform_net = commission

    elif model == "split_proportional":
        if gross <= 0:
            practitioner = Decimal("0")
            platform_net = Decimal("0")
        else:
            practitioner_ratio = practitioner_before_fee / gross
            platform_ratio = commission / gross
            practitioner_fee = stripe_fee * practitioner_ratio
            platform_fee = stripe_fee * platform_ratio
            practitioner = max(practitioner_before_fee - practitioner_fee, Decimal("0"))
            platform_net = commission - platform_fee

    else:
        # Default and recommended marketplace model.
        practitioner = practitioner_before_fee
        platform_net = commission - stripe_fee

    return {
        "fee_model": model,
        "gross_minor": _round_minor(gross),
        "commission_minor": _round_minor(commission),
        "stripe_fee_minor": _round_minor(stripe_fee),
        "practitioner_payout_minor": max(_round_minor(practitioner), 0),
        "platform_net_minor": _round_minor(platform_net),
    }
