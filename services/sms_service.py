import os
import httpx

TWILIO_API_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

OPT_OUT_SUFFIX = " Reply STOP to opt out."


class SmsNotConfigured(Exception):
    """Raised when Twilio credentials aren't set yet — distinct from a real
    send failure so callers (routes) can tell 'not set up' apart from
    'Twilio rejected this send'."""


def _credentials() -> tuple[str, str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")
    if not sid or not token or not from_number:
        raise SmsNotConfigured(
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER must all be set. "
            "Cold SMS is scaffolded but not wired to a real Twilio account yet."
        )
    return sid, token, from_number


async def send_sms(to: str, body: str) -> dict:
    """Sends a single SMS via Twilio's REST API. Unlike send_pack_ready_email
    (best-effort, swallows errors), this DOES raise on failure — cold
    outreach sends are few and deliberate, not a background side-effect, so
    the caller (an admin route) needs to know if it actually went out.

    Every message gets a legally-required opt-out line appended if the
    caller didn't already include one — this is a compliance floor
    (CTIA/TCPA), not optional, for any US business SMS outreach."""
    sid, token, from_number = _credentials()

    if "stop" not in body.lower():
        body = body.rstrip() + OPT_OUT_SUFFIX

    url = TWILIO_API_URL.format(sid=sid)
    async with httpx.AsyncClient(timeout=15, auth=(sid, token)) as client:
        response = await client.post(
            url,
            data={"To": to, "From": from_number, "Body": body},
        )
    response.raise_for_status()
    return response.json()
