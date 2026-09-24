"""Creates (or re-prints) the BFN demo Workframe — a clearly fictional shop
used in sales conversations and content ("text this shop at 2am and watch
what happens"). Never name it after a real business.

Run after workframe_schema_migration.sql, with the production env loaded:
    railway run python seed_demo_workframe.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from services.supabase_service import get_supabase_admin
from services.workframe_brains import create_brain, setup_link

DEMO_NAME = "BFN Demo Barbershop"

sb = get_supabase_admin()
existing = sb.table("wf_brains").select("*").eq("business_name", DEMO_NAME).execute().data
if existing:
    brain = existing[0]
    print("Demo already exists.")
else:
    brain = create_brain(sb, {
        "business_name": DEMO_NAME,
        "business_type": "Barbershop",
        "vertical": "barbershop",
        "city": "New Orleans",
        "owner_email": "thebrandfactorynola@gmail.com",
        "automations": ["booking_reminders", "review_requests", "lead_followup"],
    })
    brain = sb.table("wf_brains").update({
        "status": "live",
        "hours": "Tue–Sat 9am–7pm, Sun 10am–3pm, closed Mon",
        "service_area": "Magazine St, New Orleans (demo — not a real shop)",
        "services": [
            {"name": "Haircut", "price": "$35", "duration": "45 min", "description": ""},
            {"name": "Haircut + Beard", "price": "$45", "duration": "60 min", "description": ""},
            {"name": "Beard Trim / Line-up", "price": "$20", "duration": "20 min", "description": ""},
            {"name": "Kids Cut (12 & under)", "price": "$25", "duration": "30 min", "description": ""},
            {"name": "Hot Towel Shave", "price": "$40", "duration": "45 min", "description": ""},
        ],
        "faqs": [
            {"q": "Do you take walk-ins?", "a": "Yes, when a chair's open — booking online guarantees your spot."},
            {"q": "How do I book?", "a": "Use our booking link — it shows every open slot in real time."},
            {"q": "What's your cancellation policy?", "a": "Cancel or reschedule at least 4 hours ahead, no charge."},
            {"q": "Is there parking?", "a": "Street parking on Magazine and free parking on the side streets."},
            {"q": "Do you take cards?", "a": "Cards, Apple Pay, Cash App, and cash."},
        ],
        "policies": "Late more than 15 minutes may need to reschedule. No deposit required.",
        "booking_url": "https://brand-factory-frontend.vercel.app/ops?from=demo",
    }).eq("id", brain["id"]).execute().data[0]
    print("Demo created.")

frontend = os.getenv("FRONTEND_URL", "https://brand-factory-frontend.vercel.app")
print(f"Public chat (send to prospects): {frontend}/chat?key={brain['widget_key']}")
print(f"Private dashboard (yours only):  {setup_link(brain)}")
