"""
Platform-specific OAuth 2.0 service logic.

Handles:
- OAuth state generation (CSRF protection)
- Authorization URL building per platform
- Code -> token exchange
- Token refresh (rotating on every call for TikTok; 60-day IG long-lived)
- Active token retrieval with auto-expiry filtering
- Platform-specific error handling

Platforms:
  instagram   — Meta OAuth2 (IG Basic -> FB Pages -> IG Content Publish)
  tiktok      — TikTok Open Platform OAuth2
  google_business — Google OAuth2 (Business Profile API)

access_token/refresh_token are encrypted at rest (Fernet) before hitting Supabase —
this DB has twice leaked customer rows via a misconfigured RLS policy (see schema.sql
history), so a live platform token must not be recoverable from a raw table read alone.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode, quote_plus

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from jose import jwt

from services.supabase_service import get_supabase_admin


# ── Platform configuration ──────────────────────────────────────────

AUTH_URLS = {
    "instagram": "https://api.instagram.com/oauth/authorize",
    "tiktok": "https://www.tiktok.com/v2/auth/authorize",
    "google_business": "https://accounts.google.com/o/oauth2/v2/auth",
}

TOKEN_URLS = {
    "instagram": "https://api.instagram.com/oauth/access_token",
    "tiktok": "https://open.tiktokapis.com/v2/auth/oauth/token/",
    "google_business": "https://oauth2.googleapis.com/token",
}

REFRESH_URLS = {
    "instagram": "https://graph.instagram.com/ig-user-access-token",
    "tiktok": "https://open.tiktokapis.com/v2/auth/oauth/refresh_token/",
    "google_business": "https://oauth2.googleapis.com/token",
}

DEFAULT_SCOPES = {
    "instagram": "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement",
    "tiktok": "user.info.email,user.info.profile",
    "google_business": "https://www.googleapis.com/auth/business.manage",
}

VALID_PLATFORMS = ("instagram", "tiktok", "google_business")


class OAuthError(Exception):
    """Raised when an OAuth operation fails."""
    def __init__(self, detail: str, platform: str, status_code: int = 400):
        self.detail = detail
        self.platform = platform
        self.status_code = status_code
        super().__init__(detail)


# ── Secrets — fail loudly instead of silently signing with a public default ──

def _oauth_jwt_secret() -> str:
    secret = os.getenv("OAUTH_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "OAUTH_JWT_SECRET is not set. Refusing to sign/verify OAuth state "
            "tokens with a default secret — set a real random value in Railway."
        )
    return secret


def _fernet() -> Fernet:
    key = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "OAUTH_TOKEN_ENCRYPTION_KEY is not set. Refusing to store platform "
            "tokens unencrypted — set a real Fernet key in Railway."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Token was stored before encryption was enabled, or key rotated —
        # treat as unusable rather than silently returning ciphertext.
        raise OAuthError("Stored token could not be decrypted; re-authenticate", "", 401)


def generate_state(user_id: str) -> str:
    """Generate a signed OAuth state token with CSRF + user_id."""
    expiration_ts = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "sub": user_id,
        "exp": expiration_ts,
    }
    return jwt.encode(payload, _oauth_jwt_secret(), algorithm="HS256")


def verify_state(token: str) -> str:
    """Verify state token and return the user_id. Raises on failure."""
    try:
        payload = jwt.decode(token, _oauth_jwt_secret(), algorithms=["HS256"])
    except Exception:
        raise OAuthError("Invalid or expired OAuth state token", "", 400)
    return payload["sub"]


async def get_app_config(platform: str) -> dict:
    """Fetch platform OAuth config from Supabase app_config table."""
    supabase = get_supabase_admin()
    result = (
        supabase.table("app_config")
        .select("client_id, client_secret, redirect_uri, requested_scopes")
        .eq("platform", platform)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise OAuthError(f"Platform '{platform}' not configured (missing app_config entry)", platform, 400)
    return rows[0]


async def get_authorization_url(platform: str, user_id: str, scope: Optional[str] = None) -> str:
    """Build the OAuth consent redirect URL for a platform."""
    config = await get_app_config(platform)
    scopes = scope or config.get("requested_scopes") or DEFAULT_SCOPES.get(platform, "")
    state = generate_state(user_id)

    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "state": state,
        "scope": scopes,
    }

    if platform == "instagram":
        params["display"] = "popup"

    if platform == "google_business":
        params["prompt"] = "consent"
        params["access_type"] = "offline"

    base = AUTH_URLS[platform]
    return f"{base}?{urlencode(params)}"


def _existing_active_token_id(supabase, user_id: str, platform: str) -> Optional[str]:
    result = (
        supabase.table("oauth_tokens")
        .select("id")
        .eq("user_id", user_id)
        .eq("platform", platform)
        .eq("is_active", True)
        .execute()
    )
    rows = result.data or []
    return rows[0]["id"] if rows else None


async def exchange_code_for_token(platform: str, code: str, user_id: str) -> dict:
    """
    Exchange an authorization code for access/refresh tokens.
    Stores the (encrypted) token result in Supabase oauth_tokens table.
    Returns the plaintext token dict for immediate use by the caller.
    """
    import httpx

    config = await get_app_config(platform)
    token_url = TOKEN_URLS[platform]
    scopes = config.get("requested_scopes") or DEFAULT_SCOPES.get(platform, "")

    if platform == "instagram":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(token_url, data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "grant_type": "authorization_code",
                "redirect_uri": config["redirect_uri"],
                "code": code,
            })

            if resp.status_code != 200:
                raise OAuthError(
                    f"IG token exchange failed ({resp.status_code}): {resp.text}",
                    platform, 400
                )
            data = resp.json()
            short_token = data.get("access_token")
            user_id_ig = data.get("user_id")
            business_id = data.get("id") or data.get("business_id")

            long_resp = await client.post(
                "https://graph.instagram.com/ig-user-access-token",
                params={
                    "grant_type": "ig_exchange_code",
                    "access_token": short_token,
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                }
            )
            if long_resp.status_code == 200:
                long_token = long_resp.json().get("access_token")
            else:
                long_token = short_token
            expiry = datetime.now(timezone.utc) + timedelta(days=60)

            fb_user_id = None
            tokens_resp = await client.get(
                "https://graph.instagram.com/me",
                params={"fields": "id", "access_token": long_token},
            )
            if tokens_resp.status_code == 200:
                fb_user_id = tokens_resp.json().get("id")

        token_record = {
            "access_token": long_token,
            "refresh_token": None,
            "scope": scopes,
            "token_type": "Bearer",
            "expires_at": expiry.isoformat(),
            "business_id": business_id or (str(user_id_ig) if user_id_ig else None),
            "extra_meta": {"fb_user_id": fb_user_id, "user_id_ig": user_id_ig},
        }

    elif platform == "tiktok":
        encoded_scopes = quote_plus(scopes)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config["redirect_uri"],
                "client_key": config["client_id"],
                "client_secret": config["client_secret"],
            })

        if resp.status_code != 200:
            raise OAuthError(
                f"TikTok token exchange failed ({resp.status_code}): {resp.text}",
                platform, 400
            )
        data = resp.json()
        expires_in = data.get("expires_in", 900)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        token_record = {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": encoded_scopes,
            "token_type": data.get("token_type", "Bearer"),
            "expires_at": expiry.isoformat(),
            "business_id": None,
            "extra_meta": {"open_id": data.get("open_id"), "union_id": data.get("union_id")},
        }

    elif platform == "google_business":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config["redirect_uri"],
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            })

        if resp.status_code != 200:
            raise OAuthError(
                f"Google token exchange failed ({resp.status_code}): {resp.text}",
                platform, 400
            )
        data = resp.json()
        expires_in = data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        token_record = {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope", ""),
            "token_type": data.get("token_type", "Bearer"),
            "expires_at": expiry.isoformat(),
            "business_id": None,
            "extra_meta": {"account_id": data.get("account_id")},
        }

    else:
        raise OAuthError(f"Unsupported platform: {platform}", platform, 400)

    plaintext_record = dict(token_record)

    # ── Store encrypted token in Supabase ──
    supabase = get_supabase_admin()
    stored_record = dict(token_record)
    stored_record["access_token"] = _encrypt(stored_record["access_token"])
    stored_record["refresh_token"] = _encrypt(stored_record.get("refresh_token"))
    stored_record["user_id"] = user_id
    stored_record["platform"] = platform
    stored_record["is_active"] = True

    existing_id = _existing_active_token_id(supabase, user_id, platform)
    if existing_id:
        supabase.table("oauth_tokens").update(stored_record).eq("id", existing_id).execute()
    else:
        supabase.table("oauth_tokens").insert(stored_record).execute()

    return plaintext_record


async def refresh_token(platform: str, user_id: str, token_id: Optional[str] = None) -> dict:
    """
    Refresh a platform token. Handles platform-specific rotation.
    Returns the updated (plaintext) token fields.
    """
    import httpx

    supabase = get_supabase_admin()
    query = (
        supabase.table("oauth_tokens")
        .select("id, access_token, refresh_token, platform")
        .eq("user_id", user_id)
        .eq("is_active", True)
    )
    query = query.eq("id", token_id) if token_id else query.eq("platform", platform)
    result = query.execute()
    rows = result.data or []
    if not rows:
        raise OAuthError(f"No active {platform} token found", platform, 404)
    current = rows[0]
    resolved_token_id = current["id"]

    refresh = _decrypt(current.get("refresh_token"))
    if not refresh:
        raise OAuthError(
            f"No refresh token available for {platform}. Re-authenticate via /auth/{platform}/login",
            platform, 400
        )

    config = await get_app_config(platform)

    if platform == "instagram":
        current_access = _decrypt(current.get("access_token"))
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://graph.instagram.com/ig-user-access-token",
                params={
                    "grant_type": "ig_expired_token_refresh",
                    "access_token": current_access,
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                }
            )
        if resp.status_code != 200:
            raise OAuthError(f"IG token refresh failed ({resp.status_code}): {resp.text}", platform, 400)
        data = resp.json()
        new_token = data.get("access_token")
        new_refresh = None
        new_expiry = datetime.now(timezone.utc) + timedelta(days=60)

    elif platform == "tiktok":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(REFRESH_URLS[platform], data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_key": config["client_id"],
                "client_secret": config["client_secret"],
            })
        if resp.status_code != 200:
            raise OAuthError(f"TikTok refresh failed ({resp.status_code}): {resp.text}", platform, 400)
        data = resp.json()
        new_token = data.get("access_token")
        new_refresh = data.get("refresh_token", refresh)
        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 900))

    elif platform == "google_business":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(REFRESH_URLS[platform], data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            })
        if resp.status_code != 200:
            raise OAuthError(f"Google refresh failed ({resp.status_code}): {resp.text}", platform, 400)
        data = resp.json()
        new_token = data.get("access_token")
        new_refresh = None  # Google refresh tokens don't rotate
        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))

    else:
        raise OAuthError(f"Unsupported platform: {platform}", platform, 400)

    update_payload = {
        "access_token": _encrypt(new_token),
        "expires_at": new_expiry.isoformat(),
    }
    if new_refresh:
        update_payload["refresh_token"] = _encrypt(new_refresh)

    supabase.table("oauth_tokens").update(update_payload).eq("id", resolved_token_id).execute()

    return {"access_token": new_token, "platform": platform, "expires_at": new_expiry.isoformat()}


async def get_active_token(platform: str, user_id: str) -> Optional[dict]:
    """
    Get the active (non-expired) token for a platform, decrypted.
    Auto-refreshes if all stored tokens are expired but a refresh token exists.
    """
    supabase = get_supabase_admin()

    result = (
        supabase.table("oauth_tokens")
        .select("id, access_token, refresh_token, platform, expires_at, business_id, extra_meta")
        .eq("user_id", user_id)
        .eq("platform", platform)
        .eq("is_active", True)
        .execute()
    )
    tokens = result.data or []

    for token in tokens:
        expires = token.get("expires_at")
        if not expires or datetime.fromisoformat(expires.replace('Z', '+00:00')) > datetime.now(timezone.utc):
            token = dict(token)
            token["access_token"] = _decrypt(token["access_token"])
            token["refresh_token"] = _decrypt(token.get("refresh_token"))
            return token

    if tokens:
        try:
            return await refresh_token(platform, user_id, tokens[0].get("id"))
        except OAuthError:
            return None

    return None


async def revoke_token(platform: str, user_id: str, token_id: Optional[str] = None):
    """Soft-delete (deactivate) a token instead of permanent deletion."""
    supabase = get_supabase_admin()
    query = supabase.table("oauth_tokens").update({"is_active": False}).eq("user_id", user_id)
    query = query.eq("id", token_id) if token_id else query.eq("platform", platform)
    query.execute()


async def list_active_tokens(platform: Optional[str], user_id: str) -> list:
    """List all active tokens for a user (metadata only, no raw token values)."""
    supabase = get_supabase_admin()
    query = (
        supabase.table("oauth_tokens")
        .select("id, platform, business_id, scope, expires_at, extra_meta")
        .eq("user_id", user_id)
        .eq("is_active", True)
    )
    if platform:
        query = query.eq("platform", platform)
    result = query.execute()
    return result.data or []


def handle_oauth_exception(e: OAuthError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail={"error": e.detail, "platform": e.platform})
