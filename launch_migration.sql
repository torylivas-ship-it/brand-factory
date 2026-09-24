-- == Cold Outreach (SMS) Schema Migration
-- Run this in the Supabase SQL Editor (Project: wlxjekmxobhbviwmtvrm)
-- Tracks cold-SMS outreach leads for BFN Ops — separate from `profiles`/
-- `orders`/`ops_orders` since these are pre-customer prospects, not accounts.
-- Admin-only table: no anon/authenticated access at all, service role only.

create table if not exists public.outreach_leads (
  id                uuid primary key default uuid_generate_v4(),
  business_name     text not null,
  instagram_handle  text,
  phone             text,                    -- E.164 format, e.g. +15045551234
  cohort            text not null default 'appointment'
                      check (cohort in ('appointment', 'retail')),
  source            text,                    -- e.g. 'ig_candidates_2026-08-27'
  status            text not null default 'new'
                      check (status in (
                        'new', 'hook_sent', 'replied_positive', 'pitched',
                        'followed_up', 'booked', 'declined', 'do_not_contact'
                      )),
  hook_message      text,                    -- exact text sent, for audit
  pitch_message     text,
  followup_message  text,
  last_contacted_at timestamptz,
  notes             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists outreach_leads_status_idx on public.outreach_leads(status);
create index if not exists outreach_leads_phone_idx on public.outreach_leads(phone);

create trigger set_outreach_leads_updated_at
  before update on public.outreach_leads
  for each row execute function public.handle_updated_at();

alter table public.outreach_leads enable row level security;

-- No anon/authenticated policies at all — this table is never read or
-- written by the frontend, only by admin routes using the service key.
create policy "service role full access outreach_leads" on public.outreach_leads
  for all using (auth.role() = 'service_role');

revoke all on public.outreach_leads from anon, authenticated;
-- == BFN Workframe Schema Migration (Phase 1)
-- Run this in the Supabase SQL Editor (Project: wlxjekmxobhbviwmtvrm)
-- AFTER bfn_ops_schema_migration.sql and cold_outreach_schema_migration.sql.
--
-- The Workframe is what a BFN Ops client actually gets once they've paid:
--   wf_brains    — the Business Brain, one per client business (source of truth
--                  every agent reads from)
--   wf_contacts  — that business's own leads/customers
--   wf_messages  — every inbound/outbound message, per contact (conversation log)
--   wf_jobs      — scheduled sends (booking reminders, review requests, follow-ups)
--   wf_audits    — free Automation Opportunity Reports (BFN's own lead magnet)
--
-- Service-role only, same as outreach_leads: the frontend never touches these
-- tables directly — the backend enforces ownership (admin, the owning user, or
-- the brain's private manage token) on every route.

create table if not exists public.wf_brains (
  id                uuid primary key default uuid_generate_v4(),
  ops_order_id      uuid unique references public.ops_orders(id) on delete set null,
  user_id           uuid references public.profiles(id) on delete set null,
  status            text not null default 'onboarding'
                      check (status in ('onboarding', 'live', 'paused', 'canceled')),
  -- Business identity
  business_name     text not null,
  business_type     text not null,
  vertical          text not null default 'general',   -- e.g. 'barbershop' — picks the template
  city              text not null,
  owner_name        text,
  owner_email       text,
  owner_phone       text,                               -- E.164; gets "needs you" alerts
  hours             text,
  service_area      text,
  services          jsonb not null default '[]'::jsonb, -- [{name, price, duration, description}]
  policies          text,                               -- cancellations, deposits, late policy…
  faqs              jsonb not null default '[]'::jsonb, -- [{q, a}]
  -- Brand knowledge
  tone              text,                               -- e.g. "laid-back, friendly, a little funny"
  -- Links the agents hand out
  booking_url       text,
  review_url        text,                               -- Google "write a review" link
  website_url       text,
  -- Which automations are switched on (mirrors ops_orders.automations)
  automations       text[] not null default '{}',
  -- Secrets: widget_key is PUBLIC (embedded in the client's site); manage_token
  -- is PRIVATE (the owner's no-password dashboard link). Never swap them.
  widget_key        text not null unique,
  manage_token      text not null unique,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists wf_brains_user_idx on public.wf_brains(user_id);

create trigger set_wf_brains_updated_at
  before update on public.wf_brains
  for each row execute function public.handle_updated_at();

create table if not exists public.wf_contacts (
  id                 uuid primary key default uuid_generate_v4(),
  brain_id           uuid not null references public.wf_brains(id) on delete cascade,
  name               text,
  phone              text,                              -- E.164
  email              text,
  source             text not null default 'manual',    -- 'widget', 'manual', 'import'
  status             text not null default 'new'
                       check (status in ('new', 'engaged', 'booked', 'customer', 'lost', 'do_not_contact')),
  widget_session     text,                              -- ties anonymous widget chats to a contact
  needs_owner        boolean not null default false,    -- Lead Agent couldn't answer / hot lead
  notes              text,
  appointment_at     timestamptz,
  last_inbound_at    timestamptz,
  last_outbound_at   timestamptz,
  followup_step      integer not null default 0,        -- how many lead follow-ups sent so far
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index if not exists wf_contacts_brain_idx on public.wf_contacts(brain_id, created_at desc);
create index if not exists wf_contacts_phone_idx on public.wf_contacts(phone);
create unique index if not exists wf_contacts_session_idx
  on public.wf_contacts(brain_id, widget_session) where widget_session is not null;

create trigger set_wf_contacts_updated_at
  before update on public.wf_contacts
  for each row execute function public.handle_updated_at();

create table if not exists public.wf_messages (
  id           uuid primary key default uuid_generate_v4(),
  brain_id     uuid not null references public.wf_brains(id) on delete cascade,
  contact_id   uuid references public.wf_contacts(id) on delete cascade,
  direction    text not null check (direction in ('in', 'out')),
  channel      text not null check (channel in ('widget', 'sms', 'email')),
  agent        text,                                    -- 'lead', 'followup', 'reputation', 'reminder', 'owner'
  body         text not null,
  created_at   timestamptz not null default now()
);

create index if not exists wf_messages_contact_idx on public.wf_messages(contact_id, created_at);
create index if not exists wf_messages_brain_idx on public.wf_messages(brain_id, created_at desc);

create table if not exists public.wf_jobs (
  id           uuid primary key default uuid_generate_v4(),
  brain_id     uuid not null references public.wf_brains(id) on delete cascade,
  contact_id   uuid not null references public.wf_contacts(id) on delete cascade,
  kind         text not null check (kind in ('booking_reminder', 'review_request', 'lead_followup')),
  channel      text not null default 'sms' check (channel in ('sms', 'email')),
  send_at      timestamptz not null,
  body         text not null,
  status       text not null default 'scheduled'
                 check (status in ('scheduled', 'sent', 'failed', 'canceled')),
  attempts     integer not null default 0,
  last_error   text,
  sent_at      timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists wf_jobs_due_idx on public.wf_jobs(status, send_at);
create index if not exists wf_jobs_contact_idx on public.wf_jobs(contact_id);

create trigger set_wf_jobs_updated_at
  before update on public.wf_jobs
  for each row execute function public.handle_updated_at();

create table if not exists public.wf_audits (
  id              uuid primary key default uuid_generate_v4(),
  website_url     text not null,
  business_name   text,
  business_type   text,
  city            text,
  email           text,                                 -- optional; a real inbound lead for BFN when present
  score           integer,
  signals         jsonb not null default '{}'::jsonb,
  report          jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

create index if not exists wf_audits_created_idx on public.wf_audits(created_at desc);

alter table public.wf_brains   enable row level security;
alter table public.wf_contacts enable row level security;
alter table public.wf_messages enable row level security;
alter table public.wf_jobs     enable row level security;
alter table public.wf_audits   enable row level security;

create policy "service role full access wf_brains"   on public.wf_brains   for all using (auth.role() = 'service_role');
create policy "service role full access wf_contacts" on public.wf_contacts for all using (auth.role() = 'service_role');
create policy "service role full access wf_messages" on public.wf_messages for all using (auth.role() = 'service_role');
create policy "service role full access wf_jobs"     on public.wf_jobs     for all using (auth.role() = 'service_role');
create policy "service role full access wf_audits"   on public.wf_audits   for all using (auth.role() = 'service_role');

revoke all on public.wf_brains, public.wf_contacts, public.wf_messages, public.wf_jobs, public.wf_audits from anon, authenticated;

-- Audit-led outreach: prospects in outreach_leads get a website + their free
-- audit attached, so the first message can cite a real, specific finding.
alter table public.outreach_leads add column if not exists website_url text;
alter table public.outreach_leads add column if not exists audit_id uuid references public.wf_audits(id) on delete set null;
