from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from services.supabase_service import get_supabase_admin

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)


def _verify(token: str) -> dict | None:
    """Verifies a Supabase access token by asking Supabase's own Auth server
    to validate it (supabase.auth.get_user), rather than decoding the JWT
    locally — avoids ever needing to handle the signing secret ourselves,
    and avoids the classic verify_signature=False bug where any forged
    token would be accepted."""
    supabase = get_supabase_admin()
    try:
        result = supabase.auth.get_user(token)
    except Exception:
        return None
    if not result or not result.user:
        return None
    return {"user_id": result.user.id, "email": result.user.email}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    user = _verify(credentials.credentials)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer_scheme),
) -> dict | None:
    """Same verification, but returns None instead of raising when there's no
    token or it's invalid — for endpoints that work for guests and signed-in
    users alike (e.g. checkout, which never requires an account)."""
    if credentials is None:
        return None
    return _verify(credentials.credentials)
