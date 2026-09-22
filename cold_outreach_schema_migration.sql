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
