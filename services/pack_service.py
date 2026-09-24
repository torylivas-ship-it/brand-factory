import json
import time
import os
import traceback
from openai import AsyncOpenAI

from services.supabase_service import get_supabase_admin
from services.email_service import send_pack_ready_email

_client: AsyncOpenAI | None = None

SYSTEM = (
    "You are a social media strategist specializing in helping small local businesses "
    "and independent professionals in New Orleans and the Gulf Coast region build their "
    "online presence. Your content is authentic, culturally aware, locally specific, and results-driven. "
    "Never refer to the client or owner with gendered pronouns (he/she/his/her) — you don't know their "
    "gender. Use their name, the brand name, or they/their."
)

# Content Packs are also the product for people who don't own a shop — chair/
# booth renters, independent barbers and stylists, freelancers, creators —
# whose "brand" is themselves. Detected from the free-text "What you do" field.
PERSONAL_BRAND_MARKERS = (
    "independent", "booth", "chair", "renter", "rent a", "freelance", "self-employed",
    "personal brand", "creator", "influencer", "solo", "mobile ", "artist", "my own",
)

PERSONAL_BRAND_GUIDANCE = (
    "\nThis is an INDEPENDENT PROFESSIONAL building a personal brand, not a business with its own "
    "storefront. Write captions in their own first-person voice (\"I\", \"my chair\", \"book with me\"), "
    "never \"our shop\" or \"our team\". Don't assume they own or control the location they work from. "
    "Content pillars should center on their craft, their portfolio (before/after, transformations), their "
    "personality and story, and client trust. Calls to action send people to book with them directly "
    "(their booking link or DMs), and the strategy should build a following that stays with them "
    "wherever they work. In the strategy, refer to them by name — never guess their gender or use "
    "he/she/his/her for them.\n"
)


def is_personal_brand(order: dict) -> bool:
    text = f"{order.get('business_type', '')} {order.get('goals', '')}".lower()
    return any(marker in text for marker in PERSONAL_BRAND_MARKERS)

TIER_CONFIG = {
    "starter": {"calendar_days": 14, "caption_count": 14, "hashtag_groups": 3},
    "growth":  {"calendar_days": 30, "caption_count": 20, "hashtag_groups": 5},
    "agency":  {"calendar_days": 30, "caption_count": 30, "hashtag_groups": 8},
    "agency_ongoing": {"calendar_days": 30, "caption_count": 30, "hashtag_groups": 8},
}


def _client_instance() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


async def _generate_main(order: dict, cfg: dict) -> dict:
    platforms = ", ".join(order.get("platforms") or ["Instagram", "Facebook"])
    neighborhood = f", {order['neighborhood']}" if order.get("neighborhood") else ""

    personal = is_personal_brand(order)
    subject = "independent professional's personal brand" if personal else "local business"
    prompt = f"""Generate a complete social media content pack for this {subject}:
{PERSONAL_BRAND_GUIDANCE if personal else ""}
{"Name / brand" if personal else "Business"}: {order['business_name']} ({order['business_type']})
Location: {order['city']}{neighborhood}
Target Audience: {order['target_audience']}
Platforms: {platforms}
Tone: {order['tone']}
Special Offers: {order.get('special_offers') or 'None'}
Goals: {order.get('goals') or 'Grow social presence and attract local customers'}

Return a JSON object with these exact keys:
- strategy_overview (string): 3–4 paragraphs covering brand positioning, content pillars, and growth strategy
- content_calendar (array of {cfg['calendar_days']} objects): each has {{day, platform, theme, post_type, hook}}
  post_type is one of: photo, reel, story, carousel
- captions (array of {cfg['caption_count']} objects): each has {{day, platform, caption, image_suggestion}}
- hashtag_groups (array of {cfg['hashtag_groups']} objects): each has {{theme, hashtags}} where hashtags is a list of 15 tags starting with #
- posting_schedule (object): {{platforms: [{{name, posts_per_week, best_times, content_mix}}]}}

Make all content specific to {order['city']} and the local community. Return only valid JSON."""

    response = await _client_instance().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        # Confirmed live: 4000 was too tight for the Agency/Agency Ongoing
        # tier's full 30-day calendar + 30 captions + 8x15 hashtags — a real
        # order was truncated mid-JSON-string, causing json.loads to fail
        # with "Unterminated string". 8000 gives real headroom (gpt-4o
        # supports well beyond this in output tokens) rather than a marginal
        # bump that could still clip the largest tier occasionally.
        max_tokens=8000,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def _generate_website(order: dict) -> str:
    neighborhood = f", {order['neighborhood']}" if order.get("neighborhood") else ""
    response = await _client_instance().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Create a complete single-page HTML website for {order['business_name']}, "
                    f"a {order['business_type']} in {order['city']}{neighborhood}. "
                    f"Target audience: {order['target_audience']}. "
                    + (
                        "This is a personal brand for an independent professional: write in first person, "
                        "use a portfolio section instead of 'our team', and make the CTA 'Book with me'. "
                        if is_personal_brand(order) else ""
                    ) +
                    "Use embedded CSS with a dark gold theme (background #0d0d0d, accent #c9a84c). "
                    "Sections: header/nav, hero with CTA, about, services (3 cards), contact form, footer. "
                    "Make it mobile-responsive and locally authentic. "
                    "Return only the complete HTML document, no commentary."
                ),
            },
        ],
        temperature=0.7,
        max_tokens=3000,
    )
    return _strip_markdown_fence(response.choices[0].message.content.strip())


def _strip_markdown_fence(text: str) -> str:
    """GPT-4o sometimes wraps the HTML in a ```html ... ``` code fence despite
    being told to return only the document — this is a real, observed
    behavior (confirmed via a live generation), not a hypothetical. Strip it
    so the stored website_html is always valid standalone HTML, not text a
    browser would render literally."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


async def generate_pack(order_id: str, billing_period: int = 0) -> None:
    supabase = get_supabase_admin()
    start = time.time()

    result = supabase.table("orders").select("*").eq("id", order_id).maybe_single().execute()
    if not result or not result.data:
        return

    order = result.data
    cfg = TIER_CONFIG.get(order["tier"], TIER_CONFIG["starter"])
    is_recurring_renewal = order["tier"] == "agency_ongoing" and billing_period > 0

    try:
        main = await _generate_main(order, cfg)

        # Website is generated once, on the initial pack, not on every monthly renewal.
        website_html = None
        if order["tier"] in ("agency", "agency_ongoing") and billing_period == 0:
            website_html = await _generate_website(order)

        elapsed = int(time.time() - start)

        supabase.table("packs").insert({
            "order_id": order_id,
            "billing_period": billing_period,
            "strategy_overview": main.get("strategy_overview"),
            "content_calendar": main.get("content_calendar"),
            "captions": main.get("captions"),
            "hashtag_groups": main.get("hashtag_groups"),
            "posting_schedule": main.get("posting_schedule"),
            "website_html": website_html,
            "generation_time_seconds": elapsed,
        }).execute()

        # A recurring renewal shouldn't downgrade the order's overall status if it's
        # already complete from the initial delivery; only the first pack sets it.
        if not is_recurring_renewal:
            supabase.table("orders").update({"status": "complete"}).eq("id", order_id).execute()

        await send_pack_ready_email(order, is_renewal=is_recurring_renewal)

    except Exception:
        # Background task exceptions in FastAPI/Starlette don't surface
        # anywhere on their own — confirmed live, a real failed order left
        # zero trace in the logs. Print explicitly so a failure is ever
        # diagnosable instead of just a silent status flip to "failed".
        print(f"generate_pack FAILED for order_id={order_id} billing_period={billing_period}")
        print(traceback.format_exc())
        if not is_recurring_renewal:
            supabase.table("orders").update({"status": "failed"}).eq("id", order_id).execute()
        raise
