import uuid
import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from services.supabase_service import get_supabase_admin
from services import stripe_service
from services.pack_service import generate_pack

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
    tier: str  # starter | growth | agency


@router.post("/create")
async def create_order(body: OrderCreateRequest):
    if body.tier not in ("starter", "growth", "agency"):
        raise HTTPException(status_code=400, detail="tier must be starter, growth, or agency")

    supabase = get_supabase_admin()
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    order_id = str(uuid.uuid4())
    supabase.table("orders").insert({
        "id": order_id,
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
    supabase = get_supabase_admin()

    order = supabase.table("orders").select("*").eq("id", order_id).single().execute()
    if not order.data:
        raise HTTPException(status_code=404, detail="Order not found")

    result: dict = {"order": order.data}

    if order.data["status"] == "complete":
        pack = supabase.table("packs").select("*").eq("order_id", order_id).single().execute()
        if pack.data:
            result["pack"] = pack.data

    return result


@router.post("/{order_id}/generate")
async def trigger_generation(order_id: str, background_tasks: BackgroundTasks):
    """Manual trigger for pack generation (admin/testing use)."""
    supabase = get_supabase_admin()
    order = supabase.table("orders").select("id, status").eq("id", order_id).single().execute()
    if not order.data:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.data["status"] not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail=f"Order status is {order.data['status']}, cannot regenerate")

    supabase.table("orders").update({"status": "generating"}).eq("id", order_id).execute()
    background_tasks.add_task(generate_pack, order_id)
    return {"status": "generating", "order_id": order_id}
