"""BFN Workframe API.

  /wf/public/...  — unauthenticated, permissive CORS (the chat widget runs on
                    clients' own websites; the audit runs on BFN's site).
                    Rate-limited, since both spend OpenAI credits.
  /wf/brains/...  — the client dashboard. Access = admin, the brain's owning
                    signed-in user, or the brain's private manage token
                    (X-Manage-Token header) so owners don't need a password.
  /wf/run-due     — scheduler trigger for an external cron (X-Cron-Secret).
"""

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from middleware.auth_guard import get_current_user_optional
from services.supabase_service import get_supabase_admin
from services import workframe_agents as agents
from services import workframe_engine as engine
from services.workframe_brains import create_brain, create_brain_from_ops_order
from services.workframe_templates import VERTICALS
from services.audit_service import run_audit, AuditError
from utils.rate_limit import RateLimiter, client_ip

router = APIRouter()

VALID_AUTOMATIONS = ("booking_reminders", "review_requests", "lead_followup", "restock_nudges")

widget_ip_limiter = RateLimiter(limit=30, window_seconds=300)
widget_brain_limiter = RateLimiter(limit=300, window_seconds=3600)
audit_ip_limiter = RateLimiter(limit=5, window_seconds=3600)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# --- Public: chat widget -----------------------------------------------------

def _brain_by_widget_key(sb, widget_key: str) -> dict:
    res = sb.table("wf_brains").select("*").eq("widget_key", widget_key).maybe_single().execute()
    brain = res.data if res else None
    if not brain or brain["status"] not in ("onboarding", "live"):
        raise HTTPException(status_code=404, detail="Widget not found")
    return brain


@router.get("/public/widget/{widget_key}")
async def widget_config(widget_key: str):
    sb = get_supabase_admin()
    brain = _brain_by_widget_key(sb, widget_key)
    return {
        "business_name": brain["business_name"],
        "greeting": f"Hey! 👋 Questions about {brain['business_name']}? Ask me anything — prices, hours, booking.",
        "booking_url": brain.get("booking_url"),
    }


class WidgetMessageRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


@router.post("/public/widget/{widget_key}/message")
async def widget_message(widget_key: str, body: WidgetMessageRequest, request: Request):
    widget_ip_limiter.check(client_ip(request))
    widget_brain_limiter.check(widget_key)
    sb = get_supabase_admin()
    brain = _brain_by_widget_key(sb, widget_key)
    now_iso = engine.utcnow().isoformat()

    res = (
        sb.table("wf_contacts").select("*")
        .eq("brain_id", brain["id"]).eq("widget_session", body.session_id)
        .maybe_single().execute()
    )
    contact = res.data if res else None
    if not contact:
        contact = sb.table("wf_contacts").insert({
            "brain_id": brain["id"], "source": "widget", "widget_session": body.session_id,
        }).execute().data[0]

    history = (
        sb.table("wf_messages").select("direction, body")
        .eq("contact_id", contact["id"]).order("created_at")
        .limit(40).execute()
    ).data or []

    sb.table("wf_messages").insert({
        "brain_id": brain["id"], "contact_id": contact["id"],
        "direction": "in", "channel": "widget", "body": body.message,
    }).execute()

    result = await agents.lead_agent_reply(brain, history, body.message)

    update = {"last_inbound_at": now_iso, "last_outbound_at": now_iso}
    if contact["status"] == "new":
        update["status"] = "engaged"
    for field in ("name", "phone", "email"):
        if result.get(field) and not contact.get(field):
            update[field] = result[field]
    newly_needs_owner = result["needs_owner"] and not contact.get("needs_owner")
    if result["needs_owner"]:
        update["needs_owner"] = True
    contact = sb.table("wf_contacts").update(update).eq("id", contact["id"]).execute().data[0]

    sb.table("wf_messages").insert({
        "brain_id": brain["id"], "contact_id": contact["id"],
        "direction": "out", "channel": "widget", "agent": "lead", "body": result["reply"],
    }).execute()

    # Once we can reach them, restart the follow-up clock (only fires if
    # they go quiet without booking — see schedule_lead_followups).
    if contact.get("phone") or contact.get("email"):
        engine.schedule_lead_followups(sb, brain, contact)
    # Only the first time this contact needs the owner — not on every message.
    if newly_needs_owner:
        await engine.alert_owner(brain, contact, body.message)

    return {"reply": result["reply"]}


# --- Public: free audit --------------------------------------------------------

class AuditRequest(BaseModel):
    website_url: str = Field(min_length=3, max_length=300)
    business_name: Optional[str] = Field(default=None, max_length=120)
    business_type: Optional[str] = Field(default=None, max_length=80)
    city: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=200)


@router.post("/public/audit")
async def create_audit(body: AuditRequest, request: Request):
    audit_ip_limiter.check(client_ip(request))
    business = body.model_dump()
    try:
        result = await run_audit(body.website_url, business)
    except AuditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sb = get_supabase_admin()
    row = sb.table("wf_audits").insert({
        "website_url": result["signals"].get("final_url") or body.website_url,
        "business_name": body.business_name,
        "business_type": body.business_type,
        "city": body.city,
        "email": agents._clean_email(body.email),
        "score": result["score"],
        "signals": result["signals"],
        "report": result["report"],
    }).execute().data[0]
    return {"audit_id": row["id"], **result}


@router.get("/public/audit/{audit_id}")
async def get_audit(audit_id: str):
    if not _valid_uuid(audit_id):
        raise HTTPException(status_code=404, detail="Audit not found")
    sb = get_supabase_admin()
    res = sb.table("wf_audits").select("id, website_url, business_name, score, signals, report, created_at").eq("id", audit_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Audit not found")
    return res.data


# --- Dashboard access ------------------------------------------------------------

def _load_brain_with_access(
    brain_id: str,
    current_user: dict | None,
    manage_token: str | None,
) -> tuple[dict, str]:
    """Returns (brain, role) where role is 'admin' or 'owner'. 404 (not 403)
    on any mismatch so brain ids can't be probed."""
    if not _valid_uuid(brain_id):
        raise HTTPException(status_code=404, detail="Workframe not found")
    sb = get_supabase_admin()
    res = sb.table("wf_brains").select("*").eq("id", brain_id).maybe_single().execute()
    brain = res.data if res else None
    if not brain:
        raise HTTPException(status_code=404, detail="Workframe not found")
    if current_user and current_user.get("is_admin"):
        return brain, "admin"
    if current_user and brain.get("user_id") and current_user["user_id"] == brain["user_id"]:
        return brain, "owner"
    if manage_token and secrets.compare_digest(manage_token, brain["manage_token"]):
        return brain, "owner"
    raise HTTPException(status_code=404, detail="Workframe not found")


def brain_access(
    brain_id: str,
    current_user: dict | None = Depends(get_current_user_optional),
    x_manage_token: str | None = Header(default=None),
) -> tuple[dict, str]:
    return _load_brain_with_access(brain_id, current_user, x_manage_token)


def _public_brain(brain: dict, role: str) -> dict:
    out = dict(brain)
    if role != "admin":
        out.pop("manage_token", None)
    return out


def _require_admin(current_user: dict | None) -> dict:
    if not current_user or not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# --- Brains ----------------------------------------------------------------------

@router.get("/brains")
async def list_brains(current_user: dict | None = Depends(get_current_user_optional)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Sign in required")
    sb = get_supabase_admin()
    query = sb.table("wf_brains").select("id, business_name, business_type, vertical, city, status, automations, created_at, ops_order_id")
    if not current_user.get("is_admin"):
        query = query.eq("user_id", current_user["user_id"])
    return {"brains": query.order("created_at", desc=True).execute().data or []}


class CreateBrainRequest(BaseModel):
    ops_order_id: Optional[str] = None
    business_name: Optional[str] = None
    business_type: Optional[str] = None
    city: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None
    vertical: Optional[str] = None
    automations: List[str] = ["booking_reminders", "review_requests", "lead_followup"]


@router.post("/brains")
async def create_brain_route(body: CreateBrainRequest, current_user: dict | None = Depends(get_current_user_optional)):
    """Admin: provision a Workframe — from a paid ops order (normal path; the
    Stripe webhook does this automatically too) or manually (demo brains,
    clients onboarded in person)."""
    _require_admin(current_user)
    sb = get_supabase_admin()
    if body.ops_order_id:
        if not _valid_uuid(body.ops_order_id):
            raise HTTPException(status_code=404, detail="Ops order not found")
        res = sb.table("ops_orders").select("*").eq("id", body.ops_order_id).maybe_single().execute()
        if not res or not res.data:
            raise HTTPException(status_code=404, detail="Ops order not found")
        return {"brain": create_brain_from_ops_order(sb, res.data)}

    if not (body.business_name and body.business_type and body.city):
        raise HTTPException(status_code=400, detail="business_name, business_type, and city are required")
    if body.vertical and body.vertical not in VERTICALS:
        raise HTTPException(status_code=400, detail=f"vertical must be one of {sorted(VERTICALS)}")
    bad = [a for a in body.automations if a not in VALID_AUTOMATIONS]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown automation(s): {', '.join(bad)}")
    brain = create_brain(sb, {
        "business_name": body.business_name, "business_type": body.business_type, "city": body.city,
        "owner_email": body.owner_email, "owner_phone": agents.normalize_us_phone(body.owner_phone),
        "vertical": body.vertical, "automations": body.automations,
    })
    return {"brain": brain}


@router.get("/brains/{brain_id}")
async def get_brain(access: tuple = Depends(brain_access)):
    brain, role = access
    # Lets the dashboard be honest about what can actually go out right now
    # (e.g. "texts start once your text line is active") instead of implying
    # reminders are running when Twilio isn't configured yet.
    channels = {
        "sms": all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")),
        "email": bool(os.getenv("SENDGRID_API_KEY")),
    }
    return {
        "brain": _public_brain(brain, role), "role": role, "channels": channels,
        "verticals": {k: v["label"] for k, v in VERTICALS.items()},
    }


class ServiceItem(BaseModel):
    name: str = Field(max_length=80)
    price: str = Field(default="", max_length=40)
    duration: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=300)


class FaqItem(BaseModel):
    q: str = Field(max_length=200)
    a: str = Field(default="", max_length=600)


class UpdateBrainRequest(BaseModel):
    business_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    business_type: Optional[str] = Field(default=None, min_length=1, max_length=80)
    city: Optional[str] = Field(default=None, min_length=1, max_length=80)
    owner_name: Optional[str] = Field(default=None, max_length=80)
    owner_email: Optional[str] = Field(default=None, max_length=200)
    owner_phone: Optional[str] = Field(default=None, max_length=30)
    hours: Optional[str] = Field(default=None, max_length=400)
    service_area: Optional[str] = Field(default=None, max_length=200)
    services: Optional[List[ServiceItem]] = Field(default=None, max_length=40)
    policies: Optional[str] = Field(default=None, max_length=1500)
    faqs: Optional[List[FaqItem]] = Field(default=None, max_length=40)
    tone: Optional[str] = Field(default=None, max_length=200)
    booking_url: Optional[str] = Field(default=None, max_length=300)
    review_url: Optional[str] = Field(default=None, max_length=300)
    website_url: Optional[str] = Field(default=None, max_length=300)
    automations: Optional[List[str]] = None
    status: Optional[str] = None


@router.patch("/brains/{brain_id}")
async def update_brain(body: UpdateBrainRequest, access: tuple = Depends(brain_access)):
    brain, role = access
    update = body.model_dump(exclude_none=True)

    if "owner_phone" in update:
        if update["owner_phone"] == "":
            update["owner_phone"] = None
        else:
            normalized = agents.normalize_us_phone(update["owner_phone"])
            if not normalized:
                raise HTTPException(status_code=400, detail="Owner phone must be a valid US number")
            update["owner_phone"] = normalized
    for url_field in ("booking_url", "review_url", "website_url"):
        value = update.get(url_field)
        if value and not value.startswith(("https://", "http://")):
            update[url_field] = "https://" + value
    if "automations" in update:
        bad = [a for a in update["automations"] if a not in VALID_AUTOMATIONS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown automation(s): {', '.join(bad)}")
    if "status" in update:
        allowed = {"onboarding", "live", "paused", "canceled"} if role == "admin" else {"live", "paused"}
        if update["status"] not in allowed:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed)}")
        if brain["status"] == "canceled" and role != "admin":
            raise HTTPException(status_code=400, detail="This Workframe's subscription is canceled")

    if not update:
        return {"brain": _public_brain(brain, role)}
    sb = get_supabase_admin()
    updated = sb.table("wf_brains").update(update).eq("id", brain["id"]).execute().data[0]
    return {"brain": _public_brain(updated, role)}


# --- Contacts ----------------------------------------------------------------------

@router.get("/brains/{brain_id}/contacts")
async def list_contacts(access: tuple = Depends(brain_access)):
    brain, _ = access
    sb = get_supabase_admin()
    contacts = (
        sb.table("wf_contacts").select("*").eq("brain_id", brain["id"])
        .order("needs_owner", desc=True).order("updated_at", desc=True).limit(500).execute()
    ).data or []
    # Anonymous widget visitors who never said anything useful are noise.
    return {"contacts": contacts}


class ContactRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)
    appointment_at: Optional[datetime] = None
    status: Optional[str] = None
    needs_owner: Optional[bool] = None


CONTACT_STATUSES = {"new", "engaged", "booked", "customer", "lost", "do_not_contact"}


def _prepare_contact_fields(body: ContactRequest) -> dict:
    fields = body.model_dump(exclude_none=True, exclude={"appointment_at"})
    if "phone" in fields:
        if fields["phone"] == "":
            fields["phone"] = None
        else:
            normalized = agents.normalize_us_phone(fields["phone"])
            if not normalized:
                raise HTTPException(status_code=400, detail="Phone must be a valid US number")
            fields["phone"] = normalized
    if "email" in fields:
        fields["email"] = agents._clean_email(fields["email"]) if fields["email"] else None
    if "status" in fields and fields["status"] not in CONTACT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(CONTACT_STATUSES)}")
    return fields


def _apply_contact_side_effects(sb, brain: dict, contact: dict, appointment_at: datetime | None) -> list[dict]:
    if contact["status"] == "do_not_contact":
        engine.cancel_jobs(sb, contact["id"], ("booking_reminder", "review_request", "lead_followup"), "marked do_not_contact")
        return []
    if appointment_at:
        return engine.schedule_appointment(sb, brain, contact, appointment_at)
    if contact["status"] in ("booked", "customer", "lost"):
        engine.cancel_jobs(sb, contact["id"], ("lead_followup",), f"marked {contact['status']}")
    return []


@router.post("/brains/{brain_id}/contacts")
async def add_contact(body: ContactRequest, access: tuple = Depends(brain_access)):
    brain, _ = access
    fields = _prepare_contact_fields(body)
    if not (fields.get("phone") or fields.get("email") or fields.get("name")):
        raise HTTPException(status_code=400, detail="Give at least a name, phone, or email")
    appointment_at = body.appointment_at.astimezone(timezone.utc) if body.appointment_at else None
    if appointment_at:
        fields["appointment_at"] = appointment_at.isoformat()
        fields.setdefault("status", "booked")
    sb = get_supabase_admin()
    contact = sb.table("wf_contacts").insert({"brain_id": brain["id"], "source": "manual", **fields}).execute().data[0]
    jobs = _apply_contact_side_effects(sb, brain, contact, appointment_at)
    return {"contact": contact, "scheduled": jobs}


@router.patch("/brains/{brain_id}/contacts/{contact_id}")
async def update_contact(contact_id: str, body: ContactRequest, access: tuple = Depends(brain_access)):
    brain, _ = access
    if not _valid_uuid(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    fields = _prepare_contact_fields(body)
    appointment_at = body.appointment_at.astimezone(timezone.utc) if body.appointment_at else None
    if appointment_at:
        fields["appointment_at"] = appointment_at.isoformat()
        fields.setdefault("status", "booked")
    sb = get_supabase_admin()
    res = sb.table("wf_contacts").update(fields).eq("id", contact_id).eq("brain_id", brain["id"]).execute() if fields else \
        sb.table("wf_contacts").select("*").eq("id", contact_id).eq("brain_id", brain["id"]).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact = res.data[0]
    jobs = _apply_contact_side_effects(sb, brain, contact, appointment_at)
    return {"contact": contact, "scheduled": jobs}


@router.post("/brains/{brain_id}/contacts/{contact_id}/visit")
async def record_visit(contact_id: str, access: tuple = Depends(brain_access)):
    """Walk-in / completed visit with no appointment on file: marks them a
    customer and queues the review request."""
    brain, _ = access
    if not _valid_uuid(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    sb = get_supabase_admin()
    res = sb.table("wf_contacts").update({"status": "customer"}).eq("id", contact_id).eq("brain_id", brain["id"]).neq("status", "do_not_contact").execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Contact not found or opted out")
    contact = res.data[0]
    engine.cancel_jobs(sb, contact["id"], ("lead_followup", "review_request"), "visit recorded")
    jobs = []
    if "review_requests" in (brain.get("automations") or []):
        job = engine._insert_job(
            sb, brain, contact, "review_request",
            engine.next_in_window(engine.utcnow() + engine.REVIEW_REQUEST_DELAY),
            agents.review_request_message(brain, contact),
        )
        jobs = [job] if job else []
    return {"contact": contact, "scheduled": jobs}


@router.get("/brains/{brain_id}/contacts/{contact_id}/messages")
async def contact_messages(contact_id: str, access: tuple = Depends(brain_access)):
    brain, _ = access
    if not _valid_uuid(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    sb = get_supabase_admin()
    messages = (
        sb.table("wf_messages").select("*").eq("brain_id", brain["id"]).eq("contact_id", contact_id)
        .order("created_at").limit(200).execute()
    ).data or []
    jobs = (
        sb.table("wf_jobs").select("*").eq("brain_id", brain["id"]).eq("contact_id", contact_id)
        .order("send_at").execute()
    ).data or []
    return {"messages": messages, "jobs": jobs}


# --- CEO summary ----------------------------------------------------------------------

@router.get("/brains/{brain_id}/summary")
async def brain_summary(days: int = 30, access: tuple = Depends(brain_access)):
    """The CEO Agent's numbers: what the Workframe did for this business
    over the window. Computed straight from the logs — no estimates."""
    brain, _ = access
    days = max(1, min(days, 90))
    since = (engine.utcnow() - timedelta(days=days)).isoformat()
    sb = get_supabase_admin()

    contacts = (sb.table("wf_contacts").select("id, status, source, phone, email, needs_owner, created_at")
                .eq("brain_id", brain["id"]).gte("created_at", since).execute()).data or []
    messages = (sb.table("wf_messages").select("contact_id, direction, channel, agent, created_at")
                .eq("brain_id", brain["id"]).gte("created_at", since).order("created_at").execute()).data or []
    jobs = (sb.table("wf_jobs").select("kind, status, send_at")
            .eq("brain_id", brain["id"]).gte("send_at", since).execute()).data or []
    needs_owner = (sb.table("wf_contacts").select("id", count="exact")
                   .eq("brain_id", brain["id"]).eq("needs_owner", True).execute()).count or 0

    talked = {m["contact_id"] for m in messages if m["direction"] == "in"}
    widget_leads = [c for c in contacts if c["source"] == "widget" and c["id"] in talked]
    captured = [c for c in widget_leads if c.get("phone") or c.get("email")]

    # Median first-response time for widget chats (seconds): first inbound
    # → first outbound per contact.
    firsts: dict[str, dict] = {}
    for m in messages:
        if m["channel"] != "widget":
            continue
        f = firsts.setdefault(m["contact_id"], {})
        key = "in" if m["direction"] == "in" else "out"
        f.setdefault(key, engine.parse_ts(m["created_at"]))
    gaps = sorted((f["out"] - f["in"]).total_seconds() for f in firsts.values() if "in" in f and "out" in f and f["out"] >= f["in"])
    median_response = gaps[len(gaps) // 2] if gaps else None

    after_hours = 0
    for m in messages:
        if m["direction"] == "in" and m["channel"] == "widget":
            local_hour = engine.parse_ts(m["created_at"]).astimezone(engine.LOCAL_TZ).hour
            if local_hour < 9 or local_hour >= 19:
                after_hours += 1

    def sent(kind):
        return sum(1 for j in jobs if j["kind"] == kind and j["status"] == "sent")

    return {
        "days": days,
        "conversations": len(widget_leads),
        "leads_captured": len(captured),
        "after_hours_messages": after_hours,
        "median_response_seconds": median_response,
        "booked": sum(1 for c in contacts if c["status"] in ("booked", "customer")),
        "needs_owner": needs_owner,
        "reminders_sent": sent("booking_reminder"),
        "review_requests_sent": sent("review_request"),
        "followups_sent": sent("lead_followup"),
        "scheduled_upcoming": sum(1 for j in jobs if j["status"] == "scheduled"),
        "failed_sends": sum(1 for j in jobs if j["status"] == "failed"),
    }


# --- Scheduler trigger -------------------------------------------------------------------

@router.post("/run-due")
async def run_due(
    x_cron_secret: str | None = Header(default=None),
    current_user: dict | None = Depends(get_current_user_optional),
):
    secret = os.getenv("WF_CRON_SECRET")
    authorized = (secret and x_cron_secret and secrets.compare_digest(x_cron_secret, secret)) or (
        current_user and current_user.get("is_admin")
    )
    if not authorized:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await engine.run_due_jobs()
