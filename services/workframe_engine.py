"""Workframe workflow engine: schedules and delivers the automated sends
(booking reminders, review requests, lead follow-ups) and handles inbound SMS
replies from a client's customers.

Design rules, because these messages go to real people with nobody watching:
  - Nothing sends for a brain that isn't 'live' (onboarding = still setting up).
  - Nothing sends to a contact marked do_not_contact, ever.
  - SMS only goes out 9am-8pm local time (TCPA-safe quiet hours).
  - A job that's gone stale (e.g. Twilio wasn't configured yet and it sat)
    is canceled, not sent late — a "reminder" for yesterday's appointment is
    worse than no reminder.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.supabase_service import get_supabase_admin
from services.sms_service import send_sms, SmsNotConfigured
from services.email_service import send_email, EmailNotConfigured
from services import workframe_agents as agents

LOCAL_TZ = ZoneInfo(os.getenv("WF_TIMEZONE", "America/Chicago"))
SEND_WINDOW_START = 9   # local hour, inclusive
SEND_WINDOW_END = 20    # local hour, exclusive
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=15)
STALE_AFTER = timedelta(hours=36)
BATCH_SIZE = 50

LEAD_FOLLOWUP_DELAYS = (timedelta(hours=24), timedelta(hours=72))
REVIEW_REQUEST_DELAY = timedelta(hours=3)          # after the appointment
REMINDER_OFFSETS = (timedelta(hours=24), timedelta(hours=2))  # before it

OPT_OUT_WORDS = ("stop", "stopall", "unsubscribe", "cancel", "end", "quit")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def next_in_window(dt: datetime) -> datetime:
    """Earliest moment >= dt that's inside the local send window."""
    local = dt.astimezone(LOCAL_TZ)
    if local.hour < SEND_WINDOW_START:
        local = local.replace(hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0)
    elif local.hour >= SEND_WINDOW_END:
        local = (local + timedelta(days=1)).replace(hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def latest_in_window_before(dt: datetime) -> datetime:
    """Latest moment <= dt that's inside the local send window — for
    reminders, which must never slide to *after* the appointment."""
    local = dt.astimezone(LOCAL_TZ)
    if local.hour >= SEND_WINDOW_END:
        local = local.replace(hour=SEND_WINDOW_END - 1, minute=30, second=0, microsecond=0)
    elif local.hour < SEND_WINDOW_START:
        local = (local - timedelta(days=1)).replace(hour=SEND_WINDOW_END - 1, minute=30, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def format_local(dt: datetime) -> str:
    local = dt.astimezone(LOCAL_TZ)
    return local.strftime("%a, %b %-d at %-I:%M %p")


def _channel_for(contact: dict) -> str | None:
    if contact.get("phone"):
        return "sms"
    if contact.get("email"):
        return "email"
    return None


def _insert_job(sb, brain: dict, contact: dict, kind: str, send_at: datetime, body: str) -> dict | None:
    channel = _channel_for(contact)
    if not channel:
        return None
    row = sb.table("wf_jobs").insert({
        "brain_id": brain["id"],
        "contact_id": contact["id"],
        "kind": kind,
        "channel": channel,
        "send_at": send_at.isoformat(),
        "body": body,
    }).execute()
    return row.data[0] if row.data else None


def cancel_jobs(sb, contact_id: str, kinds: tuple[str, ...], reason: str) -> None:
    (
        sb.table("wf_jobs")
        .update({"status": "canceled", "last_error": reason})
        .eq("contact_id", contact_id)
        .eq("status", "scheduled")
        .in_("kind", list(kinds))
        .execute()
    )


def schedule_lead_followups(sb, brain: dict, contact: dict, now: datetime | None = None) -> list[dict]:
    """(Re)schedules the follow-up sequence for a lead who gave contact info
    but hasn't booked. Called after every inbound message, so the clock
    restarts whenever they're still talking — follow-ups only fire once
    they've actually gone quiet."""
    if "lead_followup" not in (brain.get("automations") or []):
        return []
    if contact.get("status") in ("booked", "customer", "lost", "do_not_contact"):
        return []
    now = now or utcnow()
    cancel_jobs(sb, contact["id"], ("lead_followup",), "rescheduled after new activity")
    jobs = []
    for step, delay in enumerate(LEAD_FOLLOWUP_DELAYS, start=1):
        job = _insert_job(
            sb, brain, contact, "lead_followup",
            next_in_window(now + delay),
            agents.lead_followup_message(brain, contact, step),
        )
        if job:
            jobs.append(job)
    return jobs


def schedule_appointment(sb, brain: dict, contact: dict, appointment_at: datetime, now: datetime | None = None) -> list[dict]:
    """Books the reminder + review-request sends for one appointment.
    Replaces any previously scheduled ones for this contact (reschedules)."""
    now = now or utcnow()
    automations = brain.get("automations") or []
    cancel_jobs(sb, contact["id"], ("booking_reminder", "review_request", "lead_followup"), "appointment (re)scheduled")
    jobs = []

    if "booking_reminders" in automations:
        seen = set()
        for offset in REMINDER_OFFSETS:
            send_at = latest_in_window_before(appointment_at - offset)
            # Skip reminders already in the past, too close to be useful,
            # or that collapsed onto the same slot as the other reminder.
            if send_at <= now + timedelta(minutes=5) or send_at >= appointment_at or send_at in seen:
                continue
            seen.add(send_at)
            job = _insert_job(
                sb, brain, contact, "booking_reminder", send_at,
                agents.booking_reminder_message(brain, contact, format_local(appointment_at)),
            )
            if job:
                jobs.append(job)

    if "review_requests" in automations:
        job = _insert_job(
            sb, brain, contact, "review_request",
            next_in_window(appointment_at + REVIEW_REQUEST_DELAY),
            agents.review_request_message(brain, contact),
        )
        if job:
            jobs.append(job)
    return jobs


def _skip_reason(job: dict, contact: dict | None, brain: dict | None, now: datetime) -> str | None:
    if not brain or not contact:
        return "brain or contact no longer exists"
    if brain["status"] != "live":
        return f"brain is '{brain['status']}', not live"
    if contact["status"] == "do_not_contact":
        return "contact opted out"
    send_at = parse_ts(job["send_at"])
    if now - send_at > STALE_AFTER:
        return "stale — too late to send"
    if job["kind"] == "lead_followup" and contact["status"] in ("booked", "customer", "lost"):
        return f"contact is already '{contact['status']}'"
    if job["kind"] == "booking_reminder":
        appt = parse_ts(contact.get("appointment_at"))
        if not appt or appt <= now:
            return "appointment has passed or was removed"
    return None


async def _deliver(job: dict, contact: dict, brain: dict) -> None:
    if job["channel"] == "sms":
        if not contact.get("phone"):
            raise ValueError("contact has no phone")
        await send_sms(contact["phone"], job["body"])
    else:
        if not contact.get("email"):
            raise ValueError("contact has no email")
        await send_email(contact["email"], f"A note from {brain['business_name']}", job["body"], from_name=brain["business_name"])


async def run_due_jobs(now: datetime | None = None) -> dict:
    """Sends every scheduled job whose time has come. Safe to call as often
    as you like (the background loop does it every minute)."""
    sb = get_supabase_admin()
    now = now or utcnow()
    due = (
        sb.table("wf_jobs")
        .select("*")
        .eq("status", "scheduled")
        .lte("send_at", now.isoformat())
        .order("send_at")
        .limit(BATCH_SIZE)
        .execute()
    ).data or []

    counts = {"due": len(due), "sent": 0, "canceled": 0, "retrying": 0, "failed": 0, "waiting_config": 0}
    brains: dict[str, dict | None] = {}

    for job in due:
        if job["brain_id"] not in brains:
            res = sb.table("wf_brains").select("*").eq("id", job["brain_id"]).maybe_single().execute()
            brains[job["brain_id"]] = res.data if res else None
        brain = brains[job["brain_id"]]
        res = sb.table("wf_contacts").select("*").eq("id", job["contact_id"]).maybe_single().execute()
        contact = res.data if res else None

        reason = _skip_reason(job, contact, brain, now)
        if reason:
            sb.table("wf_jobs").update({"status": "canceled", "last_error": reason}).eq("id", job["id"]).execute()
            counts["canceled"] += 1
            continue

        # Quiet hours re-check at send time (the job may have been due during
        # a window we couldn't send in, e.g. the loop was down overnight).
        if job["channel"] == "sms" and next_in_window(now) != now:
            sb.table("wf_jobs").update({"send_at": next_in_window(now).isoformat()}).eq("id", job["id"]).execute()
            continue

        try:
            await _deliver(job, contact, brain)
        except (SmsNotConfigured, EmailNotConfigured) as exc:
            # Not the job's fault — leave it scheduled; staleness guard
            # cancels it if config doesn't show up in time.
            sb.table("wf_jobs").update({"last_error": str(exc)}).eq("id", job["id"]).execute()
            counts["waiting_config"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 — record any real send failure on the job
            attempts = job["attempts"] + 1
            update = {"attempts": attempts, "last_error": str(exc)[:500]}
            if attempts >= MAX_ATTEMPTS:
                update["status"] = "failed"
                counts["failed"] += 1
            else:
                update["send_at"] = (now + RETRY_DELAY).isoformat()
                counts["retrying"] += 1
            sb.table("wf_jobs").update(update).eq("id", job["id"]).execute()
            continue

        sent_at = now.isoformat()
        sb.table("wf_jobs").update({"status": "sent", "sent_at": sent_at, "attempts": job["attempts"] + 1, "last_error": None}).eq("id", job["id"]).execute()
        agent = {"booking_reminder": "reminder", "review_request": "reputation", "lead_followup": "followup"}[job["kind"]]
        sb.table("wf_messages").insert({
            "brain_id": brain["id"], "contact_id": contact["id"], "direction": "out",
            "channel": job["channel"], "agent": agent, "body": job["body"],
        }).execute()
        contact_update = {"last_outbound_at": sent_at}
        if job["kind"] == "lead_followup":
            contact_update["followup_step"] = (contact.get("followup_step") or 0) + 1
        sb.table("wf_contacts").update(contact_update).eq("id", contact["id"]).execute()
        counts["sent"] += 1

    return counts


async def alert_owner(brain: dict, contact: dict, last_message: str) -> bool:
    """Best-effort 'needs you' text to the owner. Never raises — a missing
    owner phone or unconfigured Twilio must not break the visitor's chat.
    The dashboard's needs_owner flag is the durable version of this alert."""
    if not brain.get("owner_phone"):
        return False
    try:
        await send_sms(brain["owner_phone"], agents.owner_alert_message(brain, contact, last_message))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[workframe] owner alert not sent for brain {brain['id']}: {exc!r}")
        return False


async def handle_inbound_sms(from_number: str, body: str) -> dict:
    """A client's customer texted back (e.g. replying to a reminder). Logs it,
    honors opt-outs across every brain that has this number, stops their
    follow-up sequence, and flags the owner. Returns {"matched": n}."""
    sb = get_supabase_admin()
    contacts = sb.table("wf_contacts").select("*").eq("phone", from_number).execute().data or []
    if not contacts:
        return {"matched": 0}

    text = body.strip()
    opted_out = text.lower().strip(" .!") in OPT_OUT_WORDS
    now_iso = utcnow().isoformat()

    for contact in contacts:
        sb.table("wf_messages").insert({
            "brain_id": contact["brain_id"], "contact_id": contact["id"],
            "direction": "in", "channel": "sms", "body": text[:2000],
        }).execute()
        # Owner is flagged either way: "CANCEL" is a carrier opt-out keyword
        # (Twilio blocks further texts regardless), but a customer replying
        # "cancel" to a reminder usually means their appointment too.
        update = {"last_inbound_at": now_iso, "needs_owner": True}
        if opted_out:
            update["status"] = "do_not_contact"
            cancel_jobs(sb, contact["id"], ("booking_reminder", "review_request", "lead_followup"), "contact opted out")
        else:
            cancel_jobs(sb, contact["id"], ("lead_followup",), "contact replied")
        sb.table("wf_contacts").update(update).eq("id", contact["id"]).execute()

        brain_res = sb.table("wf_brains").select("*").eq("id", contact["brain_id"]).maybe_single().execute()
        if brain_res and brain_res.data:
            await alert_owner(brain_res.data, contact, text)

    return {"matched": len(contacts), "opted_out": opted_out}


async def scheduler_loop(interval_seconds: int = 60) -> None:
    """In-process scheduler for a single Railway instance. Enabled only when
    WF_SCHEDULER_ENABLED=1, so local dev / tests never send by accident."""
    while True:
        try:
            counts = await run_due_jobs()
            if counts["due"]:
                print(f"[workframe] scheduler run: {counts}")
        except Exception as exc:  # noqa: BLE001 — the loop must survive a bad run
            print(f"[workframe] scheduler run failed: {exc!r}")
        await asyncio.sleep(interval_seconds)
