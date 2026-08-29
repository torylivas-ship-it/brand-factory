import uuid
import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.supabase_service import get_supabase_admin
from services import stripe_service

router = APIRouter()

VALID_AUTOMATIONS = (
    "booking_reminders",
    "review_requests",
    "lead_followup",
    "restock_nudges",
)


class OpsOrderCreateRequest(BaseModel):
    email: str
    business_name: str
    business_type: str
    city: str
    phone: Optional[str] = None
    booking_system: Optional[str] = None
    automations: List[str]
    notes: Optional[str] = None
    referral_code: Optional[str] = None


@router.post("/create")
async def create_ops_order(body: OpsOrderCreateRequest):
    if not body.automations:
        raise HTTPException(status_code=400, detail="Select at least one automation")
    unknown = [a for a in body.automations if a not in VALID_AUTOMATIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown automation(s): {', '.join(unknown)}")

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

    ops_order_id = str(uuid.uuid4())
    supabase.table("ops_orders").insert({
        "id": ops_order_id,
        "referred_by_employee_id": referred_by_employee_id,
        "email": body.email,
        "business_name": body.business_name,
        "business_type": body.business_type,
        "city": body.city,
        "phone": body.phone,
        "booking_system": body.booking_system,
        "automations": body.automations,
        "notes": body.notes,
        "status": "pending",
    }).execute()

    session = stripe_service.create_ops_checkout_session(
        ops_order_id=ops_order_id,
        email=body.email,
        success_url=f"{frontend_url}/ops-success?ops_order_id={ops_order_id}",
        cancel_url=f"{frontend_url}/ops?canceled=1",
    )

    supabase.table("ops_orders").update({"stripe_session_id": session.id}).eq("id", ops_order_id).execute()

    return {"ops_order_id": ops_order_id, "checkout_url": session.url}


@router.get("/{ops_order_id}")
async def get_ops_order(ops_order_id: str):
    try:
        uuid.UUID(ops_order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ops order not found")

    supabase = get_supabase_admin()
    order = supabase.table("ops_orders").select("*").eq("id", ops_order_id).maybe_single().execute()
    if not order or not order.data:
        raise HTTPException(status_code=404, detail="Ops order not found")
    return {"ops_order": order.data}
