import uuid
import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel

from services.supabase_service import get_supabase_admin
from services import stripe_service
from services.pack_service import generate_pack
from middleware.auth_guard import get_current_user_optional

router = APIRouter()


class OrderCreateRequest(BaseModel):
    email: str
    business_name: str
    business_type: str
    city: str
    neighborhood: Optional[str] = None
    target_audience: str
    platforms: List[str]
    tone: str
    special_offers: Optional[str] = None
    goals: Optional[str] = None
    tier: str  # starter | growth | agency | agency_ongoing
    referral_code: Optional[str] = None


VALID_TIERS = ("starter", "growth", "agency", "agency_ongoing")


@router.post("/create")
async def create_order(body: OrderCreateRequest, current_user: dict | None = Depends(get_current_user_optional)):
    if body.tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {', '.join(VALID_TIERS)}")

    supabase = get_supabase_admin()
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    referred_by_employee_id = None
    if body.referral_code:
        employee = (
            supabase.table("profiles")
            .select("id")
            .eq("referral_code", body.referral_code)
            .eq("is_employee", True)
            .maybe_single()
            .execute()
        )
        if employee and employee.data:
            referred_by_employee_id = employee.data["id"]
        # An unrecognized code just means no attribution — never block checkout over it.

    order_id = str(uuid.uuid4())
    supabase.table("orders").insert({
        "id": order_id,
        "user_id": current_user["user_id"] if current_user else None,
        "referred_by_employee_id": referred_by_employee_id,
        "email": body.email,
        "business_name": body.business_name,
        "business_type": body.business_type,
        "city": body.city,
        "neighborhood": body.neighborhood,
        "target_audience": body.target_audience,
        "platforms": body.platforms,
        "tone": body.tone,
        "special_offers": body.special_offers,
        "goals": body.goals,
        "tier": body.tier,
        "status": "pending",
    }).execute()

    session = stripe_service.create_checkout_session(
        order_id=order_id,
        tier=body.tier,
        email=body.email,
        success_url=f"{frontend_url}/success?order_id={order_id}",
        cancel_url=f"{frontend_url}/order?canceled=1",
    )

    supabase.table("orders").update({"stripe_session_id": session.id}).eq("id", order_id).execute()

    return {"order_id": order_id, "checkout_url": session.url}


@router.get("/{order_id}")
async def get_order(order_id: str):
    try:
        uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Order not found")

    supabase = get_supabase_admin()

    order = supabase.table("orders").select("*").eq("id", order_id).maybe_single().execute()
    if not order or not order.data:
        raise HTTPException(status_code=404, detail="Order not found")

    result: dict = {"order": order.data}

    if order.data["status"] == "complete":
        packs = (
            supabase.table("packs")
            .select("*")
            .eq("order_id", order_id)
            .order("billing_period", desc=True)
            .execute()
        )
        if packs.data:
            result["pack"] = packs.data[0]  # most recent, for existing frontend code
            result["packs"] = packs.data    # full history, newest first

    return result


@router.post("/{order_id}/generate")
async def trigger_generation(order_id: str, background_tasks: BackgroundTasks):
    """Manual retry for pack generation after a failed OpenAI call. Requires the
    order to already show proof of real Stripe payment (set by the webhook) —
    without this check, anyone could create an unpaid order and call this
    directly to get a free AI-generated pack."""
    try:
        uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Order not found")

    supabase = get_supabase_admin()
    order = (
        supabase.table("orders")
        .select("id, status, stripe_payment_intent, stripe_subscription_id")
        .eq("id", order_id)
        .maybe_single()
        .execute()
    )
    if not order or not order.data:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.data["status"] not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail=f"Order status is {order.data['status']}, cannot regenerate")
    if not order.data["stripe_payment_intent"] and not order.data["stripe_subscription_id"]:
        raise HTTPException(status_code=402, detail="No confirmed payment on this order")

    supabase.table("orders").update({"status": "generating"}).eq("id", order_id).execute()
    background_tasks.add_task(generate_pack, order_id)
    return {"status": "generating", "order_id": order_id}
