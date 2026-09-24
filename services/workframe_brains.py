import os
import secrets

from services.email_service import send_email
from services.ops_plans import PLANS
from services.workframe_templates import template, vertical_for


def setup_link(brain: dict) -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return f"{frontend_url}/workframe?brain={brain['id']}#t={brain['manage_token']}"


async def send_setup_email(brain: dict) -> None:
    """Best-effort: emails the owner their private Workframe link. Never
    raises — the success page shows the same link, and an admin can resend."""
    if not brain.get("owner_email"):
        return
    try:
        await send_email(
            brain["owner_email"],
            f"Set up your BFN Ops for {brain['business_name']}",
            (
                f"You're in! Here's your private link to set up and manage BFN Ops for {brain['business_name']}:\n\n"
                f"{setup_link(brain)}\n\n"
                "Keep this link to yourself — anyone with it can manage your settings. "
                "It takes about 10 minutes: add your prices, hours, and booking link, then flip it live.\n\n"
                "Questions? Just reply to this email.\n— The Brand Factory NOLA"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[workframe] setup email not sent for brain {brain['id']}: {exc!r}")


def create_brain_from_ops_order(sb, ops_order: dict) -> dict:
    """Provisions a Business Brain for a paid BFN Ops order, pre-filled from
    its vertical template. Idempotent: returns the existing brain if this ops
    order already has one (Stripe can deliver the same webhook twice)."""
    existing = sb.table("wf_brains").select("*").eq("ops_order_id", ops_order["id"]).maybe_single().execute()
    if existing and existing.data:
        return existing.data

    plan = ops_order.get("plan") or "founding"
    return create_brain(sb, {
        "ops_order_id": ops_order["id"],
        "plan": plan,
        "monthly_text_cap": PLANS.get(plan, PLANS["founding"])["text_cap"],
        "user_id": ops_order.get("user_id"),
        "business_name": ops_order["business_name"],
        "business_type": ops_order["business_type"],
        "city": ops_order["city"],
        "owner_email": ops_order.get("email"),
        "owner_phone": ops_order.get("phone"),
        "automations": ops_order.get("automations") or [],
    })


def create_brain(sb, fields: dict) -> dict:
    vertical = fields.get("vertical") or vertical_for(fields.get("business_type"))
    tpl = template(vertical)
    row = {
        "vertical": vertical,
        "tone": tpl["tone"],
        "services": tpl["services"],
        "faqs": tpl["faqs"],
        **{k: v for k, v in fields.items() if v is not None},
        "widget_key": "wk_" + secrets.token_urlsafe(18),
        "manage_token": "mt_" + secrets.token_urlsafe(32),
    }
    return sb.table("wf_brains").insert(row).execute().data[0]
