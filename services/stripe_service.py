import os
import stripe

TIER_PRICE_ENV = {
    "starter": "STRIPE_PRICE_STARTER",
    "growth": "STRIPE_PRICE_GROWTH",
    "agency": "STRIPE_PRICE_AGENCY",
}

# Tiers billed as a subscription: one-time price due now, recurring price
# starts after RECURRING_TRIAL_DAYS (so the first invoice is the one-time
# amount only, and the recurring amount begins the following cycle).
SUBSCRIPTION_TIER_PRICE_ENV = {
    "agency_ongoing": {
        "one_time": "STRIPE_PRICE_AGENCY",
        "recurring": "STRIPE_PRICE_AGENCY_ONGOING_MONTHLY",
    },
}
RECURRING_TRIAL_DAYS = 30


def _init():
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def create_checkout_session(
    order_id: str,
    tier: str,
    email: str,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    _init()
    tier = tier.lower()

    if tier in SUBSCRIPTION_TIER_PRICE_ENV:
        return _create_subscription_checkout_session(order_id, tier, email, success_url, cancel_url)

    price_key = TIER_PRICE_ENV.get(tier)
    if not price_key:
        raise ValueError(f"Unknown tier: {tier}")
    price_id = os.getenv(price_key)
    if not price_id:
        raise RuntimeError(f"Env var {price_key} is not set")

    return stripe.checkout.Session.create(
        customer_email=email,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"order_id": order_id, "tier": tier},
    )


def _create_subscription_checkout_session(
    order_id: str,
    tier: str,
    email: str,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    envs = SUBSCRIPTION_TIER_PRICE_ENV[tier]
    one_time_price_id = os.getenv(envs["one_time"])
    recurring_price_id = os.getenv(envs["recurring"])
    if not one_time_price_id:
        raise RuntimeError(f"Env var {envs['one_time']} is not set")
    if not recurring_price_id:
        raise RuntimeError(f"Env var {envs['recurring']} is not set")

    return stripe.checkout.Session.create(
        customer_email=email,
        payment_method_types=["card"],
        line_items=[
            {"price": one_time_price_id, "quantity": 1},
            {"price": recurring_price_id, "quantity": 1},
        ],
        mode="subscription",
        subscription_data={
            "trial_period_days": RECURRING_TRIAL_DAYS,
            "metadata": {"order_id": order_id, "tier": tier},
        },
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"order_id": order_id, "tier": tier},
    )
