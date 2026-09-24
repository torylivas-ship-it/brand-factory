"""Creates the Standard and Pro BFN Ops prices on the existing BFN Ops product
(Founding keeps the original $49 / $29 prices). Idempotent via lookup_key —
re-running prints the existing IDs instead of creating duplicates.

    railway run python create_ops_tier_prices.py

Then set the four printed STRIPE_PRICE_OPS_* variables on Railway.
"""
import os

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
PRODUCT_ID = os.getenv("BFN_OPS_PRODUCT_ID", "prod_VA2CRdv2VQcyys")

PRICES = [
    ("STRIPE_PRICE_OPS_STANDARD_SETUP", "bfn_ops_standard_setup", 9900, None, "BFN Ops Standard — setup"),
    ("STRIPE_PRICE_OPS_STANDARD_MONTHLY", "bfn_ops_standard_monthly", 8900, "month", "BFN Ops Standard — monthly"),
    ("STRIPE_PRICE_OPS_PRO_SETUP", "bfn_ops_pro_setup", 19900, None, "BFN Ops Pro — setup"),
    ("STRIPE_PRICE_OPS_PRO_MONTHLY", "bfn_ops_pro_monthly", 14900, "month", "BFN Ops Pro — monthly"),
]

existing = {p.lookup_key: p for p in stripe.Price.list(lookup_keys=[p[1] for p in PRICES], limit=10).data}

for env_name, lookup_key, amount, interval, nickname in PRICES:
    price = existing.get(lookup_key)
    if price is None:
        kwargs = {"product": PRODUCT_ID, "unit_amount": amount, "currency": "usd",
                  "lookup_key": lookup_key, "nickname": nickname}
        if interval:
            kwargs["recurring"] = {"interval": interval}
        price = stripe.Price.create(**kwargs)
    assert price.unit_amount == amount and price.product == PRODUCT_ID, f"{lookup_key} mismatch: {price.id}"
    print(f"{env_name}={price.id}")
