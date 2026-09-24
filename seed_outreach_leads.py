"""One-time seed script for the 7 verified outreach candidates into
outreach_leads. Run AFTER cold_outreach_schema_migration.sql has been
applied in the Supabase SQL Editor, and after real Twilio credentials
exist — this only inserts rows, it does not send anything.

Usage: python3 seed_outreach_leads.py

Phone number confidence (checked 2026-09-21 via web search, none called to
independently confirm they're still live/correct — verify before relying on
these for a real send):
- Crescent City Barbershop, The Bearded Lady, Rooster Club, BabyBangz504:
  found with a 504 (New Orleans) area code from business-directory listings
  (Yelp/Fresha/etc.) that also matched the candidate's known address —
  plausible, worth using.
- Studio Esmé: search returned a 360 (Washington state) number — does not
  match a New Orleans business, almost certainly wrong (likely a booking
  platform's generic support line, not the salon's own number). Left blank.
- Fringe Hair Studio: search returned a 510 (Oakland, CA) number — same
  problem, does not fit a New Orleans salon. Left blank.
- Nola Barber (Lew Kaine): no phone surfaced at all — appears to be
  Instagram-DM/booking-link only. Left blank; SMS isn't a usable channel
  for this one unless a real number turns up some other way.

Rows with no phone are still seeded (with drafted messages ready) so they
show up in /outreach/leads for manual completion — they just can't be sent
via send-hook until a real phone number is added.
"""

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

LEADS = [
    {
        "business_name": "Crescent City Barbershop",
        "instagram_handle": "@crescentcitybarbershop",
        "phone": "+15043721673",
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — noticed y'all are open 6 days a week but "
            "the feed doesn't really show a first-time walk-in what to expect. I put together "
            "content calendars for shops like yours if that's ever useful. No worries either way!"
        ),
        "pitch_message": (
            "Glad that landed! Separate from content — I also run something called BFN Ops "
            "for shops as busy as y'all: automatic reminder texts before appointments (fewer "
            "no-shows) and review requests right after, without anyone having to remember either "
            "one. $49 to set up, $29/mo after. Want me to send the page?"
        ),
        "followup_message": (
            "No worries if it's not the right time — didn't want it to get buried. Here's the "
            "link if useful later: https://brand-factory-frontend.vercel.app/ops"
        ),
    },
    {
        "business_name": "The Bearded Lady Barbershop",
        "instagram_handle": "@the_bearded_lady_barbershop",
        "phone": "+15043100202",
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — \"neighborhood barbershop\" is a great one-line "
            "identity and I don't think the page fully cashes that in yet. I help shops match "
            "their online presence to their actual vibe, if that's ever useful."
        ),
        "pitch_message": (
            "Appreciate you saying that! So separate from content — I also run BFN Ops, which "
            "automates reminder texts before appointments and review requests after, so that "
            "part runs itself. $49 setup, $29/mo after. Want the page?"
        ),
        "followup_message": (
            "All good either way — just didn't want this to get lost. Link if you ever want it: "
            "https://brand-factory-frontend.vercel.app/ops"
        ),
    },
    {
        "business_name": "Rooster Club Barbers",
        "instagram_handle": "@roosterclubbarbers",
        "phone": "+15046032435",
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — 3 spots and still reads as independent, not a "
            "chain, that's harder to pull off than people think. Curious if anyone's dedicated "
            "to content across all three, or if it's whoever's got a minute. I help shops in "
            "that exact spot if worth a conversation."
        ),
        "pitch_message": (
            "That's usually the exact spot where it gets hard to stay consistent across "
            "locations. Separate from content — I also run BFN Ops: automatic reminder texts "
            "and review requests, same experience at all three spots without manual upkeep per "
            "location. $49 setup, $29/mo. Want the link?"
        ),
        "followup_message": (
            "No pressure — leaving the link here in case it's useful, especially if the newest "
            "spot's still finding its rhythm: https://brand-factory-frontend.vercel.app/ops"
        ),
    },
    {
        "business_name": "Nola Barber (Lew Kaine)",
        "instagram_handle": "@lewkainemane",
        "phone": None,  # no number found — Instagram-only contact, verify before this can be an SMS lead
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — appointment-only means your Instagram is doing "
            "double duty as your booking funnel, not just your portfolio. I work with solo "
            "barbers on exactly that setup. Not trying to sell you anything cold, just flagging "
            "it in case it's useful."
        ),
        "pitch_message": (
            "Right, exactly that — and since it's just you running it, no-shows probably hurt "
            "more than for a shop with a full chair rotation. That's the other thing I do, BFN "
            "Ops: automatic reminder texts before appointments plus a review request after, "
            "without you texting either one yourself. $49 setup, $29/mo. Want the page?"
        ),
        "followup_message": (
            "No worries if now's not it — here's the link whenever: "
            "https://brand-factory-frontend.vercel.app/ops"
        ),
        "notes": "No phone found via web search (checked 2026-09-21) — Instagram DM/booking-link only. Not usable as an SMS lead until a real number is found.",
    },
    {
        "business_name": "Studio Esmé New Orleans",
        "instagram_handle": "@studioesmeneworleans",
        "phone": None,  # search returned a 360 (WA) number — does not fit a New Orleans business, discarded as unreliable
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — \"effortless hair for a life in the Big Easy\" is "
            "a genuinely good line, better than most of what I see from bigger salons. I help "
            "small studios turn lines like that into an actual content rhythm, if that's ever "
            "something you're missing time for."
        ),
        "pitch_message": (
            "Thank you, that means a lot! Separate from content — I also run BFN Ops: reminder "
            "texts before appointments and review requests right after, both automatic. The "
            "stuff that's easy to fall behind on between clients. $49 setup, $29/mo. Want the page?"
        ),
        "followup_message": (
            "Totally fine if it's not a priority right now — leaving the link somewhere "
            "findable: https://brand-factory-frontend.vercel.app/ops"
        ),
        "notes": "Web search returned a 360 (Washington state) phone number for this business — doesn't fit a New Orleans salon, discarded as unreliable rather than seeded. Needs a real number sourced manually (call the shop, check their own site/booking page) before this can be an SMS lead.",
    },
    {
        "business_name": "Fringe Hair Studio (Stephanie Henry)",
        "instagram_handle": "@stephaniehenryhaircare",
        "phone": None,  # search returned a 510 (Oakland, CA) number — does not fit a New Orleans business, discarded as unreliable
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — saw the blonde specialty work and the "
            "GlossGenius booking setup, you've clearly got the technical side figured out. "
            "The content side is usually what falls off for solo stylists, just from lack of "
            "hours. That's what I help with, if relevant."
        ),
        "pitch_message": (
            "Makes sense, and GlossGenius already has booking handled well. What usually still "
            "falls through even with good booking is the follow-up around it — review requests "
            "automatically after an appointment, and a nudge to anyone who hasn't rebooked in a "
            "while. That's BFN Ops, $49 setup, $29/mo. Only makes sense if it's an actual gap "
            "for you — want me to send the page?"
        ),
        "followup_message": (
            "No worries either way — didn't want it to get buried. Link if useful: "
            "https://brand-factory-frontend.vercel.app/ops"
        ),
        "notes": "Web search returned a 510 (Oakland, CA) phone number — doesn't fit a New Orleans salon, discarded as unreliable rather than seeded. Her real booking page is stephaniehenry1.glossgenius.com; a real number may be listed there directly, worth checking manually. Also: don't repitch booking reminders to her, GlossGenius already sends those automatically (confirmed via GlossGenius's own docs) — pitch is already written to route around this.",
    },
    {
        "business_name": "BabyBangz504",
        "instagram_handle": "@babybangz504",
        "phone": "+15045090000",
        "cohort": "appointment",
        "source": "ig_candidates_2026-08-27",
        "hook_message": (
            "Hey, this is Brand Factory NOLA — you're bigger than most of the shops I usually "
            "reach out to, so feel free to ignore this if content's already handled. If not, "
            "I work with natural hair brands on keeping a posting rhythm going without it "
            "eating your week. Love what's on the Frenchman St page either way."
        ),
        "pitch_message": (
            "Appreciate that! Separate from content — I also run BFN Ops: automatic reminder "
            "texts before appointments (fewer no-shows) and review requests right after, "
            "without you having to send either yourself. $49 to set up, $29/mo after. Want "
            "me to send the page?"
        ),
        "followup_message": (
            "No worries if this isn't the right time — figured I'd leave the link in case it's "
            "useful: https://brand-factory-frontend.vercel.app/ops"
        ),
        "notes": "Confirmed 2026-09-21 (two independent web searches) as Babybangz Salon & Spa, 3833 Frenchman St — a service/appointment business, not retail, hence cohort=appointment. Phone found consistently across two separate searches (504 area code) — plausible but not independently called to confirm.",
    },
]

if __name__ == "__main__":
    inserted = supabase.table("outreach_leads").insert(LEADS).execute()
    print(f"Seeded {len(inserted.data)} leads.")
    for row in inserted.data:
        phone_status = row["phone"] or "NO PHONE — needs manual lookup"
        print(f"  {row['business_name']}: {phone_status}")
