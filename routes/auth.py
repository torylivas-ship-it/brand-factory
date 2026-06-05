"""
Auth route — thin wrapper.
Actual auth (sign-up, sign-in) is handled client-side via the Supabase JS SDK.
This router exposes a /me endpoint so the frontend can verify the token server-side
and fetch the full profile (plan, is_admin) in one call.
"""
from fastapi import APIRouter, Depends

from middleware.auth_guard import get_current_user

router = APIRouter()


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Returns the authenticated user's profile from profiles table."""
    return current_user
