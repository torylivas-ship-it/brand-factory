-- == Employee Referral Tracking Schema Migration
-- Run this in the Supabase SQL Editor (Project: wlxjekmxobhbviwmtvrm)
-- Adds is_employee/referral_code to profiles and referred_by_employee_id to
-- orders — the minimum needed to attribute a paid order to the employee who
-- referred it. Commission math (25% of the upfront sale) lives in
-- routes/admin.py, not the DB — this migration only adds the attribution link.

-- == 1. profiles: employee flag + referral code ==
alter table public.profiles
  add column if not exists is_employee boolean not null default false;

alter table public.profiles
  add column if not exists referral_code text unique;

create index if not exists profiles_referral_code_idx
  on public.profiles(referral_code) where referral_code is not null;

-- == 2. orders: which employee (if any) referred this sale ==
alter table public.orders
  add column if not exists referred_by_employee_id uuid references public.profiles(id) on delete set null;

create index if not exists orders_referred_by_employee_idx
  on public.orders(referred_by_employee_id) where referred_by_employee_id is not null;

-- No new RLS policies needed: orders/profiles are already locked down to
-- service-role-only writes (see schema.sql) and admin routes go through the
-- backend's service key, not a direct client-side query.
