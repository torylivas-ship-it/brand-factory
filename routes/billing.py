import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks

from services.supabase_service import get_supabase_admin
from services.pack_service import generate_pack

router = APIRouter()


@router.post("/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    supabase = get_supabase_admin()
    event_id = event["id"]

    existing = supabase.table("stripe_events").select("id").eq("stripe_event_id", event_id).execute()
    if existing.data:
        return {"status": "already_processed"}

    supabase.table("stripe_events").insert({
        "stripe_event_id": event_id,
        "event_type": event["type"],
        "payload": dict(event),
        "processed": False,
    }).execute()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session["id"]
        payment_intent = session.get("payment_intent")
        amount_total = session.get("amount_total")  # cents

        order_result = supabase.table("orders").select("id, status").eq("stripe_session_id", session_id).maybe_single().execute()

        if order_result.data and order_result.data["status"] == "pending":
            order_id = order_result.data["id"]
            paid_at = datetime.now(timezone.utc).isoformat()

            supabase.table("orders").update({
                "status": "generating",
                "stripe_payment_intent": payment_intent,
                "amount_paid": amount_total,
                "paid_at": paid_at,
            }).eq("id", order_id).execute()

            background_tasks.add_task(generate_pack, order_id)

    supabase.table("stripe_events").update({"processed": True}).eq("stripe_event_id", event_id).execute()
    return {"status": "ok"}
