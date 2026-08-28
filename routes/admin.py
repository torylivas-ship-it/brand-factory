import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from middleware.auth_guard import get_current_user
from services.supabase_service import get_supabase_admin

router = APIRouter()

# Flat referral commission on the upfront sale only — recurring Agency Ongoing
# renewals ($125/mo) are not commissioned. Kept as one constant so the rate
# only has to change in one place.
COMMISSION_RATE = 0.25


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "employee"


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    supabase = get_supabase_admin()

    profiles = supabase.table("profiles").select("id, email, full_name, plan, created_at, is_admin").execute()

    project_counts = supabase.table("projects").select("user_id").execute()
    counts: dict[str, int] = {}
    for row in (project_counts.data or []):
        uid = row["user_id"]
        counts[uid] = counts.get(uid, 0) + 1

    users = []
    for p in (profiles.data or []):
        users.append({**p, "project_count": counts.get(p["id"], 0)})

    return {"users": users, "total": len(users)}


@router.get("/stats")
async def get_stats(admin: dict = Depends(require_admin)):
    supabase = get_supabase_admin()

    profiles = supabase.table("profiles").select("plan").execute()
    plan_counts: dict[str, int] = {"free": 0, "starter": 0, "growth": 0, "agency": 0}
    for p in (profiles.data or []):
        plan = p.get("plan", "free")
        plan_counts[plan] = plan_counts.get(plan, 0) + 1

    # One-time revenue estimate (Starter $99, Growth $149, Agency $199)
    tier_prices = {"starter": 99, "growth": 149, "agency": 199}
    total_revenue = sum(plan_counts.get(tier, 0) * price for tier, price in tier_prices.items())

    projects = supabase.table("projects").select("id").execute()
    exports = supabase.table("exports").select("id").execute()

    return {
        "total_users": len(profiles.data or []),
        "plan_breakdown": plan_counts,
        "total_revenue_usd": total_revenue,
        "total_projects": len(projects.data or []),
        "total_exports": len(exports.data or []),
    }


@router.get("/projects")
async def list_all_projects(admin: dict = Depends(require_admin)):
    supabase = get_supabase_admin()
    projects = (
        supabase.table("projects")
        .select("id, user_id, name, type, status, created_at")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return {"projects": projects.data or [], "total": len(projects.data or [])}


class CreateEmployeeRequest(BaseModel):
    email: str
    referral_code: Optional[str] = None  # auto-generated from name/email if omitted


@router.post("/employees")
async def create_employee(body: CreateEmployeeRequest, admin: dict = Depends(require_admin)):
    """Promotes an already-signed-up account (profiles row is auto-created on
    sign-up) to employee status and assigns it a unique referral code. There's
    no separate 'employee account type' — is_employee is just a flag on the
    normal profiles row, same account a customer would have."""
    supabase = get_supabase_admin()

    profile = (
        supabase.table("profiles")
        .select("id, email, full_name, is_employee, referral_code")
        .eq("email", body.email.strip().lower())
        .maybe_single()
        .execute()
    )
    if not profile.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for that email — they need to sign up at /auth first.",
        )

    if profile.data.get("is_employee") and profile.data.get("referral_code") and not body.referral_code:
        return {
            "employee_id": profile.data["id"],
            "email": profile.data["email"],
            "referral_code": profile.data["referral_code"],
            "already_existed": True,
        }

    base_code = _slugify(body.referral_code or profile.data.get("full_name") or profile.data["email"].split("@")[0])
    code = base_code
    for _ in range(5):
        clash = (
            supabase.table("profiles")
            .select("id")
            .eq("referral_code", code)
            .neq("id", profile.data["id"])
            .maybe_single()
            .execute()
        )
        if not clash.data:
            break
        code = f"{base_code}-{secrets.token_hex(2)}"
    else:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not generate a unique referral code — pick one manually.")

    supabase.table("profiles").update({"is_employee": True, "referral_code": code}).eq("id", profile.data["id"]).execute()

    return {"employee_id": profile.data["id"], "email": profile.data["email"], "referral_code": code, "already_existed": False}


@router.get("/referrals")
async def get_referrals(admin: dict = Depends(require_admin)):
    """Per-employee referral performance: who they referred, how much those
    orders paid, and the 25% commission owed on the upfront sale. Payout
    itself is manual (Tory reconciles and pays out herself) — this endpoint
    is the source of truth she reconciles against, not a payment trigger."""
    supabase = get_supabase_admin()

    employees = (
        supabase.table("profiles")
        .select("id, email, full_name, referral_code")
        .eq("is_employee", True)
        .execute()
    )

    orders = (
        supabase.table("orders")
        .select("business_name, tier, amount_paid, paid_at, referred_by_employee_id")
        .not_.is_("referred_by_employee_id", "null")
        .not_.is_("paid_at", "null")
        .order("paid_at", desc=True)
        .execute()
    )

    orders_by_employee: dict[str, list] = {}
    for o in (orders.data or []):
        orders_by_employee.setdefault(o["referred_by_employee_id"], []).append(o)

    results = []
    for e in (employees.data or []):
        emp_orders = orders_by_employee.get(e["id"], [])
        total_revenue_cents = sum(o["amount_paid"] or 0 for o in emp_orders)
        total_commission_cents = round(total_revenue_cents * COMMISSION_RATE)

        results.append({
            "employee_id": e["id"],
            "name": e.get("full_name") or e["email"],
            "email": e["email"],
            "referral_code": e["referral_code"],
            "referred_sales": [
                {
                    "business_name": o["business_name"],
                    "tier": o["tier"],
                    "amount_paid_usd": round((o["amount_paid"] or 0) / 100, 2),
                    "commission_usd": round((o["amount_paid"] or 0) * COMMISSION_RATE / 100, 2),
                    "paid_at": o["paid_at"],
                }
                for o in emp_orders
            ],
            "referred_count": len(emp_orders),
            "total_referred_revenue_usd": round(total_revenue_cents / 100, 2),
            "total_commission_owed_usd": round(total_commission_cents / 100, 2),
        })

    results.sort(key=lambda r: r["total_referred_revenue_usd"], reverse=True)

    return {"commission_rate": COMMISSION_RATE, "employees": results}
