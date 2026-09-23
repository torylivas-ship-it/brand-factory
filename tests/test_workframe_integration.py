"""Integration tests for the BFN Workframe against a REAL Postgres + PostgREST
(the same API supabase-py talks to in production). Only the external edges
are faked: Supabase Auth token lookup, OpenAI, Twilio, and Stripe signatures.

Setup (docker):
  docker network create bfn-wf-net
  docker run -d --name bfn-wf-pg --network bfn-wf-net -e POSTGRES_PASSWORD=pgpw \
      -v $PWD:/repo:ro -v $PWD/tests:/sql:ro pgvector/pgvector:pg16
  docker exec bfn-wf-pg psql -U postgres -f /sql/supabase_stub.sql
  docker exec bfn-wf-pg psql -U postgres -f /repo/schema.sql
  docker exec bfn-wf-pg psql -U postgres -f /repo/workframe_schema_migration.sql
  docker run -d --name bfn-wf-rest --network bfn-wf-net -p 127.0.0.1:53000:3000 \
      -e PGRST_DB_URI=postgres://authenticator:authpw@bfn-wf-pg:5432/postgres \
      -e PGRST_DB_SCHEMAS=public -e PGRST_DB_ANON_ROLE=anon \
      -e PGRST_JWT_SECRET=test-secret-test-secret-test-secret-32 postgrest/postgrest:v12.2.3
  WF_TEST_POSTGREST_URL=http://127.0.0.1:53000 python -m pytest tests/ -q
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

POSTGREST_URL = os.getenv("WF_TEST_POSTGREST_URL")
pytestmark = pytest.mark.skipif(not POSTGREST_URL, reason="WF_TEST_POSTGREST_URL not set (needs docker test DB)")

JWT_SECRET = "test-secret-test-secret-test-secret-32"
TWILIO_TOKEN = "twilio-test-token"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- environment + fakes -------------------------------------------------------

@pytest.fixture(scope="module")
def env():
    from jose import jwt

    service_key = jwt.encode({"role": "service_role", "iss": "test"}, JWT_SECRET, algorithm="HS256")
    os.environ.update({
        "SUPABASE_URL": "http://placeholder.invalid",
        "SUPABASE_SERVICE_KEY": service_key,
        "FRONTEND_URL": "https://bfn.test",
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": TWILIO_TOKEN,
        "TWILIO_FROM_NUMBER": "+15045550000",
        "TWILIO_WEBHOOK_URL": "https://api.bfn.test/outreach/webhook/inbound",
        "WF_CRON_SECRET": "cron-secret",
        "OPENAI_API_KEY": "sk-test",
    })
    os.environ.pop("WF_SCHEDULER_ENABLED", None)

    from supabase import create_client
    from services import supabase_service

    client = create_client("http://placeholder.invalid", service_key)
    client.rest_url = POSTGREST_URL  # PostgREST serves at its root, not /rest/v1
    supabase_service._admin_client = client
    return client


class Fakes:
    def __init__(self):
        self.sms = []
        self.llm_queue = []

    async def send_sms(self, to, body):
        self.sms.append((to, body))
        return {"sid": "SMtest"}


@pytest.fixture(scope="module")
def app_client(env):
    from fastapi.testclient import TestClient
    import main
    from middleware import auth_guard
    from services import workframe_engine, workframe_agents

    fakes = Fakes()

    users = {
        "admin-token": {"user_id": str(uuid.uuid4()), "email": "admin@bfn.test", "is_admin": True},
        "stranger-token": {"user_id": str(uuid.uuid4()), "email": "x@x.test", "is_admin": False},
    }
    auth_guard._verify = lambda token: users.get(token)
    workframe_engine.send_sms = fakes.send_sms

    class FakeCompletions:
        async def create(self, **kwargs):
            payload = fakes.llm_queue.pop(0) if fakes.llm_queue else {"reply": "ok", "needs_owner": False}
            if isinstance(payload, Exception):
                raise payload
            msg = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_openai = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    workframe_agents._client = fake_openai

    client = TestClient(main.app)
    client.fakes = fakes
    return client


ADMIN = {"Authorization": "Bearer admin-token"}


def _make_brain(client, **overrides):
    body = {"business_name": "Crescent Test Barbers", "business_type": "Barbershop", "city": "New Orleans",
            "owner_phone": "(504) 555-0199", **overrides}
    r = client.post("/wf/brains", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["brain"]


def _owner(brain):
    return {"X-Manage-Token": brain["manage_token"]}


# --- tests ---------------------------------------------------------------------------

def test_admin_creates_brain_from_vertical_template(app_client):
    brain = _make_brain(app_client)
    assert brain["vertical"] == "barbershop"
    assert brain["status"] == "onboarding"
    assert brain["owner_phone"] == "+15045550199"
    assert any(s["name"] == "Haircut" for s in brain["services"])
    assert brain["widget_key"].startswith("wk_") and brain["manage_token"].startswith("mt_")


def test_brain_access_control(app_client):
    brain = _make_brain(app_client)
    assert app_client.get(f"/wf/brains/{brain['id']}").status_code == 404
    assert app_client.get(f"/wf/brains/{brain['id']}", headers={"X-Manage-Token": "mt_wrong"}).status_code == 404
    assert app_client.get(f"/wf/brains/{brain['id']}", headers={"Authorization": "Bearer stranger-token"}).status_code == 404
    owner_view = app_client.get(f"/wf/brains/{brain['id']}", headers=_owner(brain))
    assert owner_view.status_code == 200
    assert "manage_token" not in owner_view.json()["brain"]
    assert app_client.post("/wf/brains", json={"business_name": "x", "business_type": "y", "city": "z"}, headers=_owner(brain)).status_code == 403
    # Owner can't un-cancel or set onboarding.
    assert app_client.patch(f"/wf/brains/{brain['id']}", json={"status": "onboarding"}, headers=_owner(brain)).status_code == 400


def test_owner_fills_brain_and_goes_live(app_client):
    brain = _make_brain(app_client)
    r = app_client.patch(f"/wf/brains/{brain['id']}", headers=_owner(brain), json={
        "services": [{"name": "Haircut", "price": "$35", "duration": "45 min"}],
        "booking_url": "booksy.com/crescent-test",
        "review_url": "https://g.page/r/test/review",
        "hours": "Tue-Sat 9am-7pm",
        "status": "live",
    })
    assert r.status_code == 200, r.text
    b = r.json()["brain"]
    assert b["status"] == "live" and b["booking_url"] == "https://booksy.com/crescent-test"
    assert b["services"][0]["price"] == "$35"


def test_widget_conversation_captures_lead_and_schedules_followups(app_client, env):
    brain = _make_brain(app_client)
    app_client.patch(f"/wf/brains/{brain['id']}", headers=_owner(brain), json={"status": "live"})
    fakes = app_client.fakes
    fakes.sms.clear()

    cfg = app_client.get(f"/wf/public/widget/{brain['widget_key']}")
    assert cfg.status_code == 200 and cfg.json()["business_name"] == "Crescent Test Barbers"

    session = "sess-" + uuid.uuid4().hex[:12]
    fakes.llm_queue = [
        {"reply": "A cut is $35! Want me to get you booked? What's your name and number?", "intent": "pricing", "needs_owner": False},
        {"reply": "Thanks Marcus! I'll have the owner text you.", "name": "Marcus", "phone": "504.555.0123", "intent": "booking", "needs_owner": True},
        {"reply": "The owner will confirm shortly.", "intent": "booking", "needs_owner": True},
    ]
    url = f"/wf/public/widget/{brain['widget_key']}/message"
    r1 = app_client.post(url, json={"session_id": session, "message": "how much is a cut"})
    assert r1.status_code == 200 and "$35" in r1.json()["reply"]
    r2 = app_client.post(url, json={"session_id": session, "message": "Marcus, 504.555.0123, can I come saturday"})
    assert r2.status_code == 200
    r3 = app_client.post(url, json={"session_id": session, "message": "saturday at 2?"})
    assert r3.status_code == 200

    contacts = app_client.get(f"/wf/brains/{brain['id']}/contacts", headers=_owner(brain)).json()["contacts"]
    assert len(contacts) == 1
    c = contacts[0]
    assert c["name"] == "Marcus" and c["phone"] == "+15045550123" and c["needs_owner"] is True
    assert c["status"] == "engaged"

    # Owner alerted exactly once, even though needs_owner came back twice.
    owner_texts = [s for s in fakes.sms if s[0] == "+15045550199"]
    assert len(owner_texts) == 1 and "Marcus" in owner_texts[0][1]

    detail = app_client.get(f"/wf/brains/{brain['id']}/contacts/{c['id']}/messages", headers=_owner(brain)).json()
    assert [m["direction"] for m in detail["messages"]] == ["in", "out", "in", "out", "in", "out"]
    scheduled = [j for j in detail["jobs"] if j["status"] == "scheduled"]
    assert sorted(j["kind"] for j in scheduled) == ["lead_followup", "lead_followup"]
    # Re-scheduling on each message must not pile up duplicates.
    assert len([j for j in detail["jobs"] if j["status"] == "canceled"]) >= 2


def test_llm_failure_degrades_to_safe_handoff(app_client):
    brain = _make_brain(app_client)
    app_client.fakes.llm_queue = [RuntimeError("openai down")]
    r = app_client.post(f"/wf/public/widget/{brain['widget_key']}/message",
                        json={"session_id": "sess-fail-" + uuid.uuid4().hex[:6], "message": "hello?"})
    assert r.status_code == 200
    assert "best number" in r.json()["reply"]


def test_appointment_schedules_reminders_in_send_window(app_client):
    from services.workframe_engine import LOCAL_TZ
    brain = _make_brain(app_client)
    now_local = datetime.now(LOCAL_TZ)
    appt_local = (now_local + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    r = app_client.post(f"/wf/brains/{brain['id']}/contacts", headers=_owner(brain), json={
        "name": "Dana", "phone": "5045550150", "appointment_at": appt_local.isoformat(),
    })
    assert r.status_code == 200, r.text
    assert r.json()["contact"]["status"] == "booked"
    jobs = r.json()["scheduled"]
    kinds = sorted(j["kind"] for j in jobs)
    assert kinds == ["booking_reminder", "booking_reminder", "review_request"]
    for j in jobs:
        local = datetime.fromisoformat(j["send_at"]).astimezone(LOCAL_TZ)
        assert 9 <= local.hour < 20, j
        if j["kind"] == "booking_reminder":
            assert datetime.fromisoformat(j["send_at"]) < appt_local
            assert "Dana" in j["body"] and "2:00 PM" in j["body"]
        else:
            assert "g.page" not in j["body"]  # no review_url set on this brain → no link invented

    # Early-morning appointment: reminders must still land before it, never after.
    early = (now_local + timedelta(days=3)).replace(hour=8, minute=0, second=0, microsecond=0)
    r = app_client.patch(f"/wf/brains/{brain['id']}/contacts/{r.json()['contact']['id']}", headers=_owner(brain),
                         json={"appointment_at": early.isoformat()})
    reminders = [j for j in r.json()["scheduled"] if j["kind"] == "booking_reminder"]
    assert reminders and all(datetime.fromisoformat(j["send_at"]) < early for j in reminders)
    assert all(9 <= datetime.fromisoformat(j["send_at"]).astimezone(LOCAL_TZ).hour < 20 for j in reminders)


def test_scheduler_only_sends_for_live_brains_and_respects_opt_out(app_client, env):
    import asyncio
    from services import workframe_engine as engine

    brain = _make_brain(app_client)
    owner = _owner(brain)
    c = app_client.post(f"/wf/brains/{brain['id']}/contacts", headers=owner, json={"name": "Lee", "phone": "5045550177"}).json()["contact"]
    visit = app_client.post(f"/wf/brains/{brain['id']}/contacts/{c['id']}/visit", headers=owner).json()
    job = visit["scheduled"][0]
    assert job["kind"] == "review_request"

    # A moment inside the send window, after the job is due.
    due_at = datetime.fromisoformat(job["send_at"]) + timedelta(minutes=1)
    app_client.fakes.sms.clear()

    # Brain still onboarding → canceled, nothing sent.
    counts = asyncio.run(engine.run_due_jobs(now=due_at))
    assert counts["canceled"] >= 1
    assert not [s for s in app_client.fakes.sms if s[0] == "+15045550177"]

    # Go live, visit again → sends.
    app_client.patch(f"/wf/brains/{brain['id']}", headers=owner, json={"status": "live"})
    job2 = app_client.post(f"/wf/brains/{brain['id']}/contacts/{c['id']}/visit", headers=owner).json()["scheduled"][0]
    due2 = datetime.fromisoformat(job2["send_at"]) + timedelta(minutes=1)
    asyncio.run(engine.run_due_jobs(now=due2))
    sent = [s for s in app_client.fakes.sms if s[0] == "+15045550177"]
    assert len(sent) == 1 and "Thanks for coming in to Crescent Test Barbers, Lee!" in sent[0][1]
    detail = app_client.get(f"/wf/brains/{brain['id']}/contacts/{c['id']}/messages", headers=owner).json()
    assert any(m["agent"] == "reputation" and m["direction"] == "out" for m in detail["messages"])

    # Opt-out → future jobs never send.
    app_client.patch(f"/wf/brains/{brain['id']}/contacts/{c['id']}", headers=owner, json={"status": "do_not_contact"})
    blocked = app_client.post(f"/wf/brains/{brain['id']}/contacts/{c['id']}/visit", headers=owner)
    assert blocked.status_code == 404


def test_stale_and_quiet_hours(app_client, env):
    import asyncio
    from services import workframe_engine as engine
    from services.workframe_engine import LOCAL_TZ

    brain = _make_brain(app_client)
    owner = _owner(brain)
    app_client.patch(f"/wf/brains/{brain['id']}", headers=owner, json={"status": "live"})
    c = app_client.post(f"/wf/brains/{brain['id']}/contacts", headers=owner, json={"name": "Q", "phone": "5045550188"}).json()["contact"]
    job = app_client.post(f"/wf/brains/{brain['id']}/contacts/{c['id']}/visit", headers=owner).json()["scheduled"][0]
    send_at = datetime.fromisoformat(job["send_at"])

    # Due, but "now" is 11pm local → pushed to next morning, not sent.
    night = send_at.astimezone(LOCAL_TZ).replace(hour=23, minute=0) + timedelta(days=0)
    if night < send_at:
        night += timedelta(days=1)
    app_client.fakes.sms.clear()
    asyncio.run(engine.run_due_jobs(now=night.astimezone(timezone.utc)))
    assert not [s for s in app_client.fakes.sms if s[0] == "+15045550188"]

    # Two days late → canceled as stale, never sent.
    asyncio.run(engine.run_due_jobs(now=(night + timedelta(days=2)).astimezone(timezone.utc)))
    jobs = app_client.get(f"/wf/brains/{brain['id']}/contacts/{c['id']}/messages", headers=owner).json()["jobs"]
    assert jobs[-1]["status"] == "canceled" and "stale" in jobs[-1]["last_error"]
    assert not [s for s in app_client.fakes.sms if s[0] == "+15045550188"]


def _twilio_sig(params):
    url = os.environ["TWILIO_WEBHOOK_URL"]
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(TWILIO_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def test_inbound_sms_requires_signature_and_honors_stop(app_client):
    brain = _make_brain(app_client)
    owner = _owner(brain)
    app_client.patch(f"/wf/brains/{brain['id']}", headers=owner, json={"status": "live"})
    appt = datetime.now(timezone.utc) + timedelta(days=4)
    c = app_client.post(f"/wf/brains/{brain['id']}/contacts", headers=owner,
                        json={"name": "Stopper", "phone": "5045550166", "appointment_at": appt.isoformat()}).json()["contact"]

    params = {"From": "+15045550166", "Body": "STOP"}
    forged = app_client.post("/outreach/webhook/inbound", data=params, headers={"X-Twilio-Signature": "bogus"})
    assert forged.status_code == 403

    ok = app_client.post("/outreach/webhook/inbound", data=params, headers={"X-Twilio-Signature": _twilio_sig(params)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["workframe"]["opted_out"] is True

    detail = app_client.get(f"/wf/brains/{brain['id']}/contacts/{c['id']}/messages", headers=owner).json()
    assert all(j["status"] == "canceled" for j in detail["jobs"])
    contact = [x for x in app_client.get(f"/wf/brains/{brain['id']}/contacts", headers=owner).json()["contacts"] if x["id"] == c["id"]][0]
    assert contact["status"] == "do_not_contact" and contact["needs_owner"] is True


def test_summary_counts(app_client):
    brain = _make_brain(app_client)
    owner = _owner(brain)
    app_client.fakes.llm_queue = [{"reply": "hi!", "phone": "5045550111", "needs_owner": False}]
    app_client.post(f"/wf/public/widget/{brain['widget_key']}/message", json={"session_id": "sess-sum-" + uuid.uuid4().hex[:6], "message": "yo"})
    s = app_client.get(f"/wf/brains/{brain['id']}/summary", headers=owner).json()
    assert s["conversations"] == 1 and s["leads_captured"] == 1
    assert s["median_response_seconds"] is not None and s["median_response_seconds"] >= 0
    assert s["scheduled_upcoming"] == 2


def test_paid_ops_order_provisions_brain_and_setup_link(app_client, env, monkeypatch):
    import stripe
    ops_id = str(uuid.uuid4())
    env.table("ops_orders").insert({
        "id": ops_id, "email": "owner@shop.test", "business_name": "Fade Factory", "business_type": "barber shop",
        "city": "New Orleans", "phone": "+15045550142", "automations": ["review_requests", "booking_reminders"],
        "status": "pending", "stripe_session_id": "cs_test_" + ops_id[:8],
    }).execute()
    event = {"id": "evt_" + uuid.uuid4().hex, "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_" + ops_id[:8], "subscription": "sub_test", "amount_total": 4900,
        "metadata": {"product": "bfn_ops"}, "payment_intent": None,
    }}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: event)
    r = app_client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t"})
    assert r.status_code == 200, r.text

    # Duplicate delivery of a *new* event id for the same session must not create a second brain.
    event["id"] = "evt_" + uuid.uuid4().hex
    app_client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t"})

    brains = env.table("wf_brains").select("*").eq("ops_order_id", ops_id).execute().data
    assert len(brains) == 1
    assert brains[0]["vertical"] == "barbershop" and brains[0]["automations"] == ["review_requests", "booking_reminders"]

    ops = app_client.get(f"/ops/{ops_id}").json()
    assert ops["workframe"]["brain_id"] == brains[0]["id"]
    assert ops["workframe"]["manage_token"] == brains[0]["manage_token"]

    # Cancel subscription → brain canceled.
    event.update({"id": "evt_" + uuid.uuid4().hex, "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_test"}}})
    app_client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t"})
    assert env.table("wf_brains").select("status").eq("ops_order_id", ops_id).execute().data[0]["status"] == "canceled"
    assert app_client.get(f"/wf/public/widget/{brains[0]['widget_key']}").status_code == 404


def test_setup_link_expires(app_client, env):
    ops_id = str(uuid.uuid4())
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    env.table("ops_orders").insert({
        "id": ops_id, "email": "o@x.test", "business_name": "Old", "business_type": "salon", "city": "NOLA",
        "automations": ["review_requests"], "status": "active", "paid_at": old,
    }).execute()
    from services.workframe_brains import create_brain_from_ops_order
    create_brain_from_ops_order(env, env.table("ops_orders").select("*").eq("id", ops_id).execute().data[0])
    assert app_client.get(f"/ops/{ops_id}").json()["workframe"] is None


def test_cors_public_vs_private(app_client):
    pre = {"Origin": "https://somebarber.com", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    assert app_client.options("/wf/public/widget/wk_x/message", headers=pre).headers.get("access-control-allow-origin") == "*"
    assert app_client.options("/wf/brains", headers=pre).status_code == 400
    ok = app_client.options("/wf/brains", headers={**pre, "Origin": "https://bfn.test"})
    assert ok.headers.get("access-control-allow-origin") == "https://bfn.test"


def test_cron_trigger_auth(app_client):
    assert app_client.post("/wf/run-due").status_code == 403
    assert app_client.post("/wf/run-due", headers={"X-Cron-Secret": "nope"}).status_code == 403
    assert app_client.post("/wf/run-due", headers={"X-Cron-Secret": "cron-secret"}).status_code == 200


def test_audit_blocks_private_addresses_and_handles_social_only(app_client):
    for bad in ("http://127.0.0.1:53000", "http://localhost", "http://169.254.169.254/latest", "ftp://x.com", "notaurl"):
        r = app_client.post("/wf/public/audit", json={"website_url": bad}, headers={"X-Forwarded-For": f"10.0.0.{abs(hash(bad)) % 250}"})
        assert r.status_code == 400, (bad, r.text)

    app_client.fakes.llm_queue = [RuntimeError("no model")]  # rule-based fallback path
    r = app_client.post("/wf/public/audit", json={"website_url": "instagram.com/somebarber", "business_name": "Some Barber", "email": "OWNER@Shop.test"},
                        headers={"X-Forwarded-For": "10.1.1.1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signals"]["own_website"] is False and body["score"] == 20
    assert len(body["report"]["opportunities"]) == 4 and body["report"]["opportunities"][0]["impact"] == "high"
    stored = app_client.get(f"/wf/public/audit/{body['audit_id']}").json()
    assert stored["score"] == 20


def test_audit_rate_limit(app_client):
    ip = {"X-Forwarded-For": "10.9.9.9"}
    codes = [app_client.post("/wf/public/audit", json={"website_url": "instagram.com/a"}, headers=ip).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
