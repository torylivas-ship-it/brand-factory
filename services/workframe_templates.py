"""Vertical Workframe templates — the "80% standardized" part of every client
install. A new Business Brain starts from its vertical's template; the owner
(or BFN during onboarding) only fills in the ~20% that's actually theirs:
real prices, hours, links, and any FAQ the template doesn't already cover.

Templates never invent facts about a specific business — services here have
no prices, and FAQ answers are written as policy *defaults* the owner confirms
or edits, not claims. Anything left blank is something the Lead Agent will
decline to answer and route to the owner instead of guessing."""

VERTICALS = {
    "barbershop": {
        "label": "Barbershop",
        "tone": "friendly, relaxed, and straight to the point — like the front chair, not a call center",
        "services": [
            {"name": "Haircut", "price": "", "duration": "30-45 min", "description": ""},
            {"name": "Haircut + Beard", "price": "", "duration": "45-60 min", "description": ""},
            {"name": "Beard Trim / Line-up", "price": "", "duration": "15-20 min", "description": ""},
            {"name": "Kids Cut", "price": "", "duration": "30 min", "description": ""},
        ],
        "faqs": [
            {"q": "Do you take walk-ins?", "a": ""},
            {"q": "How do I book?", "a": "Use our booking link — it shows every open slot in real time."},
            {"q": "What's your cancellation policy?", "a": ""},
            {"q": "Is there parking?", "a": ""},
        ],
    },
    "salon": {
        "label": "Hair / Beauty Salon",
        "tone": "warm, upbeat, and welcoming",
        "services": [
            {"name": "Cut & Style", "price": "", "duration": "", "description": ""},
            {"name": "Color", "price": "", "duration": "", "description": "Consultation recommended for major color changes."},
            {"name": "Silk Press / Blowout", "price": "", "duration": "", "description": ""},
            {"name": "Braids / Protective Styles", "price": "", "duration": "", "description": ""},
        ],
        "faqs": [
            {"q": "Do I need a consultation first?", "a": ""},
            {"q": "Do you require a deposit?", "a": ""},
            {"q": "What's your cancellation / late policy?", "a": ""},
        ],
    },
    "restaurant": {
        "label": "Restaurant",
        "tone": "hospitable and upbeat, with local flavor",
        "services": [
            {"name": "Dine-in", "price": "", "duration": "", "description": ""},
            {"name": "Takeout / Online ordering", "price": "", "duration": "", "description": ""},
            {"name": "Catering / Private events", "price": "", "duration": "", "description": ""},
        ],
        "faqs": [
            {"q": "Do you take reservations?", "a": ""},
            {"q": "Do you have vegetarian / vegan options?", "a": ""},
            {"q": "Do you cater?", "a": ""},
        ],
    },
    "auto_detailing": {
        "label": "Auto Detailing",
        "tone": "confident, detail-obsessed, and friendly",
        "services": [
            {"name": "Exterior Wash & Wax", "price": "", "duration": "", "description": ""},
            {"name": "Interior Detail", "price": "", "duration": "", "description": ""},
            {"name": "Full Detail", "price": "", "duration": "", "description": ""},
            {"name": "Ceramic Coating", "price": "", "duration": "", "description": "Quote depends on vehicle size and paint condition."},
        ],
        "faqs": [
            {"q": "Are you mobile — do you come to me?", "a": ""},
            {"q": "How long does a full detail take?", "a": ""},
        ],
    },
    "general": {
        "label": "Local Business",
        "tone": "friendly, professional, and helpful",
        "services": [],
        "faqs": [],
    },
}


def vertical_for(business_type: str | None) -> str:
    """Best-effort mapping from the free-text business_type on an ops order
    to a template key. Falls back to 'general' — never guesses harder."""
    t = (business_type or "").lower()
    if any(k in t for k in ("barber",)):
        return "barbershop"
    if any(k in t for k in ("salon", "hair", "stylist", "braid", "nail", "lash", "beauty", "spa")):
        return "salon"
    if any(k in t for k in ("restaurant", "cafe", "café", "kitchen", "food", "bar", "grill", "bakery", "coffee")):
        return "restaurant"
    if any(k in t for k in ("detail", "car wash", "auto")):
        return "auto_detailing"
    return "general"


def template(vertical: str) -> dict:
    return VERTICALS.get(vertical, VERTICALS["general"])
