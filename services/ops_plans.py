"""BFN Ops plans — the one place prices, text caps, and Stripe price env vars
live. Chosen by Tory 2026-09-23 after a market comparison (NiceJob $75–125/mo
reviews-only, Podium ~$399+/mo on annual contract, Birdeye $299+/mo):

  founding  $49 + $29/mo, 500 texts   — first 10 paying clients only, price
                                        locked 12 months, in exchange for being
                                        a case study
  standard  $99 + $89/mo, 1,000 texts
  pro       $199 + $149/mo, 2,500 texts, done-for-you setup + monthly tuning

Text caps exist so no client can cost more in SMS fees than they pay.
"""

FOUNDING_SPOTS = 10

PLANS = {
    "founding": {
        "label": "Founding",
        "setup_usd": 49,
        "monthly_usd": 29,
        "text_cap": 500,
        "setup_price_env": "STRIPE_PRICE_OPS_SETUP",       # the original $49/$29 prices
        "monthly_price_env": "STRIPE_PRICE_OPS_MONTHLY",
        "blurb": "First 10 clients only. Price locked for 12 months.",
    },
    "standard": {
        "label": "Standard",
        "setup_usd": 99,
        "monthly_usd": 89,
        "text_cap": 1000,
        "setup_price_env": "STRIPE_PRICE_OPS_STANDARD_SETUP",
        "monthly_price_env": "STRIPE_PRICE_OPS_STANDARD_MONTHLY",
        "blurb": "Everything in BFN Ops for a busy single location.",
    },
    "pro": {
        "label": "Pro",
        "setup_usd": 199,
        "monthly_usd": 149,
        "text_cap": 2500,
        "setup_price_env": "STRIPE_PRICE_OPS_PRO_SETUP",
        "monthly_price_env": "STRIPE_PRICE_OPS_PRO_MONTHLY",
        "blurb": "We set it all up for you and tune it every month.",
    },
}


def founding_spots_taken(supabase) -> int:
    """Paid, active Founding clients. Admin comp orders (amount_paid = 0)
    don't consume a spot — only real paying clients do."""
    result = (
        supabase.table("ops_orders")
        .select("id", count="exact")
        .eq("plan", "founding")
        .eq("status", "active")
        .gt("amount_paid", 0)
        .execute()
    )
    return result.count or 0


def public_plans(supabase) -> list[dict]:
    taken = founding_spots_taken(supabase)
    out = []
    for key, p in PLANS.items():
        plan = {
            "key": key, "label": p["label"], "setup_usd": p["setup_usd"],
            "monthly_usd": p["monthly_usd"], "text_cap": p["text_cap"], "blurb": p["blurb"],
            "available": True,
        }
        if key == "founding":
            plan["spots_total"] = FOUNDING_SPOTS
            plan["spots_left"] = max(0, FOUNDING_SPOTS - taken)
            plan["available"] = plan["spots_left"] > 0
        out.append(plan)
    return out
