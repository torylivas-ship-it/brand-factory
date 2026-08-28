import json
import time
import os
from openai import AsyncOpenAI

from services.supabase_service import get_supabase_admin
from services.email_service import send_pack_ready_email

_client: AsyncOpenAI | None = None

SYSTEM = (
    "You are a social media strategist specializing in helping small local businesses "
    "in New Orleans and the Gulf Coast region build their online presence. "
    "Your content is authentic, culturally aware, locally specific, and results-driven."
)

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

    prompt = f"""Generate a complete social media content pack for this local business:

Business: {order['business_name']} ({order['business_type']})
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
        max_tokens=4000,
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
    return response.choices[0].message.content.strip()


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
        if not is_recurring_renewal:
            supabase.table("orders").update({"status": "failed"}).eq("id", order_id).execute()
        raise
