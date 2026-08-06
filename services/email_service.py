import os
import httpx

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"

TIER_LABELS = {
    "starter": "Starter Pack",
    "growth": "Growth Pack",
    "agency": "Agency Pack",
    "agency_ongoing": "Agency + Ongoing Pack",
}


async def send_pack_ready_email(order: dict, is_renewal: bool = False) -> None:
    """Best-effort notification email. Never raises — a failed send should
    not affect order/pack status, since the pack itself already generated fine."""
    api_key = os.getenv("SENDGRID_API_KEY")
    if not api_key:
        return

    from_email = os.getenv("EMAIL_FROM", "thebrandfactorynola@gmail.com")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    pack_url = f"{frontend_url}/success?order_id={order['id']}"
    tier_label = TIER_LABELS.get(order["tier"], "Brand Pack")

    if is_renewal:
        subject = f"Your fresh {tier_label} for {order['business_name']} is ready"
        heading = "Your monthly refresh is ready! 🎉"
        body_line = "Here's this month's new content calendar, captions, and hashtags."
    else:
        subject = f"Your {tier_label} for {order['business_name']} is ready"
        heading = "Your brand pack is ready! 🎉"
        body_line = "Your AI-generated strategy, content calendar, captions, and hashtags are ready to view."

    html_content = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px; background: #0d0d0d; color: #f5f0e8;">
      <p style="color:#c9a84c; font-weight:700; letter-spacing:1px; text-transform:uppercase; font-size:12px;">The Brand Factory NOLA</p>
      <h1 style="font-size: 22px; margin: 12px 0;">{heading}</h1>
      <p style="color:#ccc; line-height:1.6;">{body_line}</p>
      <a href="{pack_url}" style="display:inline-block; margin-top:20px; background:#c9a84c; color:#0d0d0d; font-weight:700; text-decoration:none; padding:12px 24px; border-radius:6px;">View Your Pack →</a>
      <p style="color:#666; font-size:12px; margin-top:32px;">{order['business_name']} · {order['city']}</p>
    </div>
    """

    payload = {
        "personalizations": [{"to": [{"email": order["email"]}], "subject": subject}],
        "from": {"email": from_email, "name": "The Brand Factory NOLA"},
        "content": [{"type": "text/html", "value": html_content}],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                SENDGRID_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except Exception:
        pass
