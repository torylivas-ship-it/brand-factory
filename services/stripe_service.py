import os
import stripe

TIER_PRICE_ENV = {
    "starter": "STRIPE_PRICE_STARTER",
    "growth": "STRIPE_PRICE_GROWTH",
    "agency": "STRIPE_PRICE_AGENCY",
}


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
    price_key = TIER_PRICE_ENV.get(tier.lower())
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
