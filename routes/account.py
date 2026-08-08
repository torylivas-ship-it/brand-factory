from fastapi import APIRouter, Depends

from middleware.auth_guard import get_current_user
from services.supabase_service import get_supabase_admin

router = APIRouter()


@router.get("/orders")
async def list_my_orders(current_user: dict = Depends(get_current_user)):
    supabase = get_supabase_admin()
    user_id = current_user["user_id"]
    email = current_user["email"]

    # Claim any past guest orders placed with this same email (case-insensitive)
    # before this account existed, so previously-paid packs become visible too.
    if email:
        supabase.table("orders").update({"user_id": user_id}).ilike("email", email).is_("user_id", "null").execute()

    orders = (
        supabase.table("orders")
        .select("id, business_name, tier, status, amount_paid, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"orders": orders.data or []}
