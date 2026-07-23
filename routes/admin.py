from fastapi import APIRouter, Depends, HTTPException, status

from middleware.auth_guard import get_current_user
from services.supabase_service import get_supabase_admin

router = APIRouter()


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


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

    # One-time revenue estimate (Starter $49, Growth $99, Agency $149)
    tier_prices = {"starter": 49, "growth": 99, "agency": 149}
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
