import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from typing import Optional

from middleware.auth_guard import get_current_user
from services.supabase_service import get_supabase_admin
from services.sms_service import send_sms, SmsNotConfigured, twilio_signature_valid
from services.places_service import discover_candidates, PlacesNotConfigured
from services.workframe_engine import handle_inbound_sms
from services.audit_service import run_audit, AuditError

router = APIRouter()


def _public_url(request: Request) -> str:
    """The URL Twilio actually posted to. Railway terminates TLS at its proxy,
    so request.url says http:// — rebuild it from the forwarded headers.
    Set TWILIO_WEBHOOK_URL to skip the guesswork entirely."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{host}{request.url.path}{query}"

# How many outbound sends (hook + pitch + follow-up, combined) are allowed
# per calendar day across ALL leads. Deliberately conservative default —
# see BFN_Selling_Automation_Playbook_2026-09-21.md: bursting outreach is
# what triggered a real Instagram restriction on this exact business before,
# and cold-SMS carriers penalize burst-looking sends similarly. Override via
# env var once real send history justifies raising it.
DAILY_SEND_CAP = int(os.getenv("OUTREACH_DAILY_SEND_CAP", "2"))

# Phrases that mark a lead do-not-contact automatically from an inbound
# reply, independent of Twilio's own carrier-level STOP handling (belt and
# suspenders — never re-contact someone who opted out, even if a carrier-
# level opt-out somehow didn't register on our side).
OPT_OUT_PHRASES = ("stop", "unsubscribe", "remove me", "do not contact", "don't contact")


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def _today_send_count(supabase) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    result = (
        supabase.table("outreach_leads")
        .select("id", count="exact")
        .gte("last_contacted_at", f"{today}T00:00:00Z")
        .execute()
    )
    return result.count or 0


def _enforce_daily_cap(supabase) -> None:
    sent_today = _today_send_count(supabase)
    if sent_today >= DAILY_SEND_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily outreach cap reached ({sent_today}/{DAILY_SEND_CAP} sends today). "
                "This is a deliberate pacing limit, not a bug — see the outreach playbook "
                "for why burst-sending is the thing to avoid. Raise OUTREACH_DAILY_SEND_CAP "
                "if this is genuinely too conservative."
            ),
        )


class CreateLeadRequest(BaseModel):
    business_name: str
    instagram_handle: Optional[str] = None
    website_url: Optional[str] = None
    phone: Optional[str] = None  # E.164, e.g. +15045551234
    cohort: str = "appointment"  # 'appointment' or 'retail'
    source: Optional[str] = None
    hook_message: Optional[str] = None
    pitch_message: Optional[str] = None
    followup_message: Optional[str] = None
    notes: Optional[str] = None


@router.get("/leads")
async def list_leads(admin: dict = Depends(require_admin)):
    supabase = get_supabase_admin()
    leads = supabase.table("outreach_leads").select("*").order("created_at", desc=True).execute()
    return {"leads": leads.data or [], "total": len(leads.data or []), "daily_send_cap": DAILY_SEND_CAP}


@router.post("/leads")
async def create_lead(body: CreateLeadRequest, admin: dict = Depends(require_admin)):
    supabase = get_supabase_admin()
    row = supabase.table("outreach_leads").insert(body.model_dump(exclude_none=True)).execute()
    return row.data[0] if row.data else {}


class DiscoverLeadsRequest(BaseModel):
    query: str  # e.g. "barbershop"
    location: str = "New Orleans, LA"
    cohort: str = "appointment"
    max_results: int = 20


@router.post("/discover")
async def discover_leads(body: DiscoverLeadsRequest, admin: dict = Depends(require_admin)):
    """Finds real local-business candidates via Google Places (name, real
    Google-verified phone, address, rating) and inserts them as 'new' leads
    — never drafts a Hook message and never sends anything. A human still
    has to write the actual Hook text per candidate before send-hook will
    allow a send (that endpoint requires hook_message to be set), same as
    every other lead in this table — discovery only replaces the slow,
    error-prone manual search step, not the judgment calls after it."""
    supabase = get_supabase_admin()

    try:
        candidates = await discover_candidates(body.query, body.location, body.max_results)
    except PlacesNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    existing = supabase.table("outreach_leads").select("business_name").execute()
    existing_names = {row["business_name"].strip().lower() for row in (existing.data or [])}

    today = datetime.now(timezone.utc).date().isoformat()
    new_rows = []
    skipped = []
    for c in candidates:
        if not c["business_name"] or c["business_name"].strip().lower() in existing_names:
            skipped.append(c["business_name"])
            continue
        new_rows.append({
            "business_name": c["business_name"],
            "instagram_handle": c["instagram_handle"],
            "phone": c["phone"],
            "cohort": body.cohort,
            "website_url": c.get("website") if c.get("website") and not c["instagram_handle"] else None,
            "source": f"google_places_{body.query}_{today}",
            "status": "new",
            "notes": (
                f"Discovered via Google Places: {c.get('address') or 'no address'} — "
                f"rating {c.get('rating', 'n/a')} ({c.get('review_count', 0)} reviews)"
                + (f" — website: {c['website']}" if c.get("website") and not c["instagram_handle"] else "")
                + ("" if c["phone"] else " — NO PHONE returned by Places, needs manual lookup")
            ),
        })

    inserted = supabase.table("outreach_leads").insert(new_rows).execute() if new_rows else None
    return {
        "found": len(candidates),
        "inserted": len(inserted.data) if inserted else 0,
        "skipped_duplicates": skipped,
        "leads": inserted.data if inserted else [],
    }


class UpdateLeadStatusRequest(BaseModel):
    status: str
    notes: Optional[str] = None


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, body: UpdateLeadStatusRequest, admin: dict = Depends(require_admin)):
    """Manual status transitions — e.g. marking 'replied_positive' after
    checking the phone yourself, or 'declined'/'do_not_contact'. Inbound
    Twilio replies also drive this automatically via the webhook below, but
    most real triage (was the reply actually positive?) needs a human."""
    supabase = get_supabase_admin()
    valid_statuses = {
        "new", "hook_sent", "replied_positive", "pitched",
        "followed_up", "booked", "declined", "do_not_contact",
    }
    if body.status not in valid_statuses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"status must be one of {sorted(valid_statuses)}")

    update = {"status": body.status}
    if body.notes is not None:
        update["notes"] = body.notes
    row = supabase.table("outreach_leads").update(update).eq("id", lead_id).execute()
    if not row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return row.data[0]


async def _send_stage(lead_id: str, stage: str, message_field: str, next_status: str, allowed_from: set[str]) -> dict:
    supabase = get_supabase_admin()

    lead_result = supabase.table("outreach_leads").select("*").eq("id", lead_id).maybe_single().execute()
    lead = lead_result.data if lead_result else None
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    if lead["status"] == "do_not_contact":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Lead is marked do_not_contact — will not send.")
    if lead["status"] not in allowed_from:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Lead status is '{lead['status']}', expected one of {sorted(allowed_from)} to send a {stage}.",
        )
    if not lead.get("phone"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lead has no phone number on file.")
    message = lead.get(message_field)
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Lead has no {message_field} drafted yet.")

    _enforce_daily_cap(supabase)

    try:
        await send_sms(lead["phone"], message)
    except SmsNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    updated = (
        supabase.table("outreach_leads")
        .update({"status": next_status, "last_contacted_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", lead_id)
        .execute()
    )
    return updated.data[0] if updated.data else {}


@router.post("/leads/{lead_id}/audit")
async def audit_lead(lead_id: str, admin: dict = Depends(require_admin)):
    """Runs the free website check on a prospect (their website, or their
    Instagram if that's all they have) and attaches it to the lead. Gives the
    human writing the Hook a real, specific finding plus a report link to
    share — it does NOT draft or send anything itself."""
    supabase = get_supabase_admin()
    lead_result = supabase.table("outreach_leads").select("*").eq("id", lead_id).maybe_single().execute()
    lead = lead_result.data if lead_result else None
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    target = lead.get("website_url")
    if not target and lead.get("instagram_handle"):
        target = f"instagram.com/{lead['instagram_handle'].lstrip('@')}"
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lead has no website_url or instagram_handle to check.")

    try:
        result = await run_audit(target, {"business_name": lead["business_name"], "city": "New Orleans"})
    except AuditError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    row = supabase.table("wf_audits").insert({
        "website_url": result["signals"].get("final_url") or target,
        "business_name": lead["business_name"],
        "score": result["score"],
        "signals": result["signals"],
        "report": result["report"],
    }).execute().data[0]
    supabase.table("outreach_leads").update({"audit_id": row["id"]}).eq("id", lead_id).execute()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    opportunities = result["report"].get("opportunities", [])
    return {
        "audit_id": row["id"],
        "report_url": f"{frontend_url}/audit?id={row['id']}",
        "score": result["score"],
        "top_finding": opportunities[0]["why"] if opportunities else None,
        "report": result["report"],
    }


@router.post("/leads/{lead_id}/send-hook")
async def send_hook(lead_id: str, admin: dict = Depends(require_admin)):
    return await _send_stage(lead_id, "hook", "hook_message", "hook_sent", allowed_from={"new"})


@router.post("/leads/{lead_id}/send-pitch")
async def send_pitch(lead_id: str, admin: dict = Depends(require_admin)):
    return await _send_stage(lead_id, "pitch", "pitch_message", "pitched", allowed_from={"replied_positive"})


@router.post("/leads/{lead_id}/send-followup")
async def send_followup(lead_id: str, admin: dict = Depends(require_admin)):
    return await _send_stage(lead_id, "follow-up", "followup_message", "followed_up", allowed_from={"pitched"})


@router.post("/webhook/inbound")
async def inbound_sms_webhook(request: Request):
    """Twilio calls this on every inbound reply (configure the number's
    'A message comes in' webhook to POST here once a real number exists).
    No admin auth — this is a public webhook Twilio itself calls, same
    pattern as /billing/webhook for Stripe — so every request must carry a
    valid X-Twilio-Signature, or a forged POST could opt people out or fake
    replies.

    One Twilio number serves both BFN's own cold outreach and every client
    Workframe, so a reply is routed to whichever (or both) knows the number."""
    form = await request.form()
    params = {k: v for k, v in form.items()}
    url = os.getenv("TWILIO_WEBHOOK_URL") or _public_url(request)
    if not twilio_signature_valid(url, params, request.headers.get("x-twilio-signature")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")

    from_number = form.get("From", "")
    raw_body = (form.get("Body") or "").strip()
    inbound_body = raw_body.lower()

    workframe_result = await handle_inbound_sms(from_number, raw_body)

    supabase = get_supabase_admin()
    lead_result = (
        supabase.table("outreach_leads")
        .select("id, status")
        .eq("phone", from_number)
        .maybe_single()
        .execute()
    )
    lead = lead_result.data if lead_result else None
    if not lead:
        return {"matched": False, "workframe": workframe_result}

    if any(phrase in inbound_body for phrase in OPT_OUT_PHRASES):
        supabase.table("outreach_leads").update({"status": "do_not_contact"}).eq("id", lead["id"]).execute()
        return {"matched": True, "new_status": "do_not_contact"}

    # Any other reply just gets flagged for human triage — automatically
    # guessing "positive" vs "negative" from reply text isn't reliable
    # enough to drive a Pitch send on its own.
    if lead["status"] == "hook_sent":
        supabase.table("outreach_leads").update({"status": "replied_positive"}).eq("id", lead["id"]).execute()
        return {"matched": True, "new_status": "replied_positive", "note": "Review reply manually before sending Pitch."}

    return {"matched": True, "new_status": lead["status"]}
