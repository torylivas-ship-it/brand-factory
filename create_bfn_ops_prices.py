import os
import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

product = stripe.Product.create(
    name="BFN Ops",
    description="Automated booking reminders, review requests, lead follow-up, and restock nudges for BFN clients.",
)

setup_price = stripe.Price.create(
    product=product.id,
    unit_amount=4900,
    currency="usd",
)

monthly_price = stripe.Price.create(
    product=product.id,
    unit_amount=2900,
    currency="usd",
    recurring={"interval": "month"},
)

print("product:", product.id)
print("setup_price:", setup_price.id)
print("monthly_price:", monthly_price.id)

# Also add customer.subscription.deleted to the live webhook — the new BFN
# Ops cancellation handler needs it and the endpoint doesn't send it yet.
WEBHOOK_ID = "we_1U1U1101TjglX253DzffT8z4"
endpoint = stripe.WebhookEndpoint.retrieve(WEBHOOK_ID)
events = set(endpoint.enabled_events)
if "customer.subscription.deleted" not in events:
    events.add("customer.subscription.deleted")
    stripe.WebhookEndpoint.modify(WEBHOOK_ID, enabled_events=list(events))
    print("webhook updated, now listens for:", sorted(events))
else:
    print("webhook already listens for customer.subscription.deleted")
