"""
Social platform OAuth 2.0 routes — /auth/{platform}/login + /auth/{platform}/callback

Routes:
  GET    /auth/{platform}/login    — authenticated fetch; returns {auth_url} for the frontend to navigate to
  GET    /auth/{platform}/callback — hit directly by Meta/TikTok/Google as a top-level browser redirect
  GET    /auth/{platform}/status   — check if user has an active token
  PUT    /auth/{platform}/refresh  — manually refresh token
  DELETE /auth/{platform}/revoke   — deactivate token
  GET    /auth/tokens              — list all active tokens (metadata only)

/callback deliberately does NOT depend on get_current_user: a provider redirect is a plain
browser navigation and can't carry an Authorization header. The signed `state` param (which
embeds user_id, see oauth_service.generate_state) is the sole source of identity there.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from middleware.auth_guard import get_current_user
from services.oauth_service import (
    get_authorization_url,
    exchange_code_for_token,
    refresh_token,
    get_active_token,
    revoke_token,
    list_active_tokens,
    handle_oauth_exception,
    OAuthError,
    verify_state,
    VALID_PLATFORMS,
)

router = APIRouter()


def _check_platform(platform: str):
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform '{platform}'. Supported: {', '.join(VALID_PLATFORMS)}",
        )


def _connect_page_url() -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return f"{frontend_url.rstrip('/')}/connect.html"


# ── /auth/{platform}/login ──────────────────────────────────────────

@router.get("/{platform}/login")
async def oauth_login(
    platform: str,
    current_user: dict = Depends(get_current_user),
    scope: Optional[str] = Query(None, description="Override default scopes for this platform"),
):
    """Returns the provider's consent URL as JSON. The frontend does the actual
    navigation (window.location.href = auth_url) — this endpoint itself can't be
    hit as a raw redirect because it needs the caller's auth token."""
    _check_platform(platform)
    try:
        auth_url = await get_authorization_url(platform, current_user["user_id"], scope)
        return {"auth_url": auth_url}
    except OAuthError as e:
        raise handle_oauth_exception(e)


# ── /auth/{platform}/callback ─────────────────────────────────────────

@router.get("/{platform}/callback")
async def oauth_callback(
    platform: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    _check_platform(platform)
    connect_url = _connect_page_url()

    if error:
        return RedirectResponse(url=f"{connect_url}?platform={platform}&status=error&reason={error}")

    if not code or not state:
        return RedirectResponse(url=f"{connect_url}?platform={platform}&status=error&reason=missing_params")

    try:
        verified_user_id = verify_state(state)
        await exchange_code_for_token(platform, code, verified_user_id)
        return RedirectResponse(url=f"{connect_url}?platform={platform}&status=success")
    except OAuthError as e:
        return RedirectResponse(url=f"{connect_url}?platform={platform}&status=error&reason={e.detail}")


# ── /auth/{platform}/status ───────────────────────────────────────────

@router.get("/{platform}/status")
async def get_token_status(platform: str, current_user: dict = Depends(get_current_user)):
    _check_platform(platform)
    try:
        token = await get_active_token(platform, current_user["user_id"])
        if token is None:
            return {"status": "no_token", "platform": platform}
        return {
            "status": "active",
            "platform": platform,
            "token_id": token.get("id"),
            "expires_at": token.get("expires_at"),
            "business_id": token.get("business_id"),
        }
    except OAuthError as e:
        raise handle_oauth_exception(e)


# ── /auth/{platform}/refresh ──────────────────────────────────────────

@router.put("/{platform}/refresh")
async def manual_refresh_token(
    platform: str,
    token_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _check_platform(platform)
    try:
        token = await refresh_token(platform, current_user["user_id"], token_id)
        return {"status": "refreshed", "platform": platform, "expires_at": token.get("expires_at")}
    except OAuthError as e:
        raise handle_oauth_exception(e)


# ── /auth/{platform}/revoke ────────────────────────────────────────────

@router.delete("/{platform}/revoke")
async def revoke_oauth_token(
    platform: str,
    token_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _check_platform(platform)
    try:
        await revoke_token(platform, current_user["user_id"], token_id)
        return {"status": "revoked", "platform": platform}
    except OAuthError as e:
        raise handle_oauth_exception(e)


# ── /auth/tokens ─────────────────────────────────────────────────────

@router.get("/tokens")
async def list_tokens(
    platform: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    tokens = await list_active_tokens(platform, current_user["user_id"])
    return {"status": "ok", "count": len(tokens), "tokens": tokens}
