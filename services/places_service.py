import os
import re
import httpx

PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# Heuristic guardrails for "small independent local business" fit — not a
# hard science, just filters out obvious mismatches (dead listings, chains
# big enough to already have marketing) before a human reviews the list.
MIN_REVIEW_COUNT = 3
MAX_REVIEW_COUNT = 500


class PlacesNotConfigured(Exception):
    pass


def _api_key() -> str:
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        raise PlacesNotConfigured(
            "GOOGLE_PLACES_API_KEY is not set. Lead discovery is scaffolded but not wired to a "
            "real Google Cloud project yet — create one, enable the Places API, and set this env var."
        )
    return key


def _normalize_e164(phone: str | None) -> str | None:
    """Google's international_phone_number comes back as e.g. '+1 504-372-1673'
    — valid to dial but not strict E.164 (no spaces/dashes allowed), which is
    what Twilio and outreach_leads.phone expect. Strips to digits + leading +."""
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    return digits or None


def _extract_instagram_handle(website: str | None) -> str | None:
    """Some small businesses list their Instagram as their Google Business
    'website' field instead of a real site — worth capturing when present,
    otherwise Instagram handle isn't something Places data can give us."""
    if not website or "instagram.com" not in website.lower():
        return None
    match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", website)
    return f"@{match.group(1)}" if match else None


async def discover_candidates(query: str, location: str, max_results: int = 20) -> list[dict]:
    """Text-searches Google Places for `query` near `location` (e.g.
    query="barbershop", location="New Orleans, LA"), then fetches phone
    number details for each result. Returns a list of dicts shaped to drop
    straight into outreach_leads rows — caller is responsible for dedup
    against existing leads and for actually inserting them (this function
    never writes to the database, just discovers candidates)."""
    api_key = _api_key()

    async with httpx.AsyncClient(timeout=15) as client:
        search_response = await client.get(
            PLACES_TEXT_SEARCH_URL,
            params={"query": f"{query} in {location}", "key": api_key},
        )
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data.get("status") not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"Places Text Search failed: {search_data.get('status')} — {search_data.get('error_message', '')}")

        candidates = []
        for place in search_data.get("results", [])[:max_results]:
            if place.get("business_status") != "OPERATIONAL":
                continue
            review_count = place.get("user_ratings_total", 0)
            if review_count < MIN_REVIEW_COUNT or review_count > MAX_REVIEW_COUNT:
                continue

            details_response = await client.get(
                PLACES_DETAILS_URL,
                params={
                    "place_id": place["place_id"],
                    "fields": "name,formatted_phone_number,international_phone_number,formatted_address,website,rating,user_ratings_total",
                    "key": api_key,
                },
            )
            details_response.raise_for_status()
            details = details_response.json().get("result", {})

            phone = _normalize_e164(details.get("international_phone_number") or details.get("formatted_phone_number"))
            website = details.get("website")

            candidates.append({
                "business_name": details.get("name") or place.get("name"),
                "phone": phone,  # already E.164-ish from international_phone_number when available
                "instagram_handle": _extract_instagram_handle(website),
                "address": details.get("formatted_address"),
                "website": website,
                "rating": details.get("rating"),
                "review_count": details.get("user_ratings_total", review_count),
            })

    return candidates
