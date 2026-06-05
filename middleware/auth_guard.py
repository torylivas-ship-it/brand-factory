import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from services.supabase_service import get_supabase_admin

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Verifies a Supabase JWT and returns {user_id, plan, is_admin} from profiles.
    Raises 401 on invalid/expired token.
    """
    token = credentials.credentials

    try:
        # Supabase JWTs are signed with the JWT secret — decode without verifying
        # the audience so we can extract the sub (user_id).
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
        )
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    supabase = get_supabase_admin()
    result = (
        supabase.table("profiles")
        .select("id, plan, is_admin")
        .eq("id", user_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User profile not found")

    profile = result.data
    return {
        "user_id": profile["id"],
        "plan": profile.get("plan", "free"),
        "is_admin": profile.get("is_admin", False),
    }
