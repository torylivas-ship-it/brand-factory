import os
import json
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None

NOLA_SYSTEM_CONTEXT = (
    "You are a creative marketing AI specializing in helping small businesses "
    "in New Orleans and the Gulf Coast region build standout brands. "
    "Your outputs are punchy, culturally resonant, and professionally polished. "
    "Focus on authenticity, local flavor where appropriate, and results-driven copy."
)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


async def generate_social_post(platform: str, topic: str, tone: str, business_name: str) -> str:
    client = _get_client()

    platform_hints = {
        "instagram": "Include 3-5 relevant emojis. Keep it under 150 words. End with a CTA.",
        "facebook": "Conversational and community-focused. Up to 200 words. Ask an engaging question.",
        "twitter": "Under 280 characters. Sharp, punchy, shareable.",
        "linkedin": "Professional but warm. 150-250 words. Focus on value and credibility.",
        "tiktok": "High energy, trend-aware. Short hook in first line. Casual voice.",
    }
    hint = platform_hints.get(platform.lower(), "Write an engaging social media post.")

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": NOLA_SYSTEM_CONTEXT},
            {
                "role": "user",
                "content": (
                    f"Write a {tone} {platform} post for {business_name} about: {topic}.\n"
                    f"Platform guidance: {hint}\n"
                    "Return only the post text, no commentary."
                ),
            },
        ],
        temperature=0.8,
        max_tokens=400,
    )
    return response.choices[0].message.content.strip()


async def generate_hashtags(niche: str, topic: str, count: int) -> list[str]:
    client = _get_client()
    count = min(max(count, 5), 30)

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": NOLA_SYSTEM_CONTEXT},
            {
                "role": "user",
                "content": (
                    f"Generate exactly {count} relevant hashtags for a {niche} business posting about {topic}. "
                    "Mix high-volume and niche-specific tags. Include some NOLA/Louisiana tags if locally relevant. "
                    "Return as a JSON array of strings, each starting with #. No commentary."
                ),
            },
        ],
        temperature=0.7,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content.strip()
    parsed = json.loads(content)
    # Handle various json shapes: {hashtags: [...]} or {tags: [...]} or [...]
    if isinstance(parsed, list):
        return parsed[:count]
    for key in ("hashtags", "tags", "result", "items"):
        if key in parsed and isinstance(parsed[key], list):
            return parsed[key][:count]
    return list(parsed.values())[0][:count] if parsed else []


async def generate_branding(category: str, keywords: str, vibe: str) -> dict:
    client = _get_client()

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": NOLA_SYSTEM_CONTEXT},
            {
                "role": "user",
                "content": (
                    f"Create a complete brand identity for a {category} business.\n"
                    f"Keywords: {keywords}\n"
                    f"Brand vibe: {vibe}\n\n"
                    "Return a JSON object with these exact keys:\n"
                    "- brand_name (string): a memorable, original business name\n"
                    "- tagline (string): a punchy 6-10 word tagline\n"
                    "- color_palette (array of 3 hex codes): primary, secondary, accent\n"
                    "- font_pairing (object): {heading: string, body: string} — Google Fonts names\n"
                    "- brand_voice (string): 2-sentence description of tone and communication style\n"
                    "- elevator_pitch (string): 3-sentence business description\n"
                    "No commentary, return only valid JSON."
                ),
            },
        ],
        temperature=0.85,
        max_tokens=600,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content.strip()
    return json.loads(content)
