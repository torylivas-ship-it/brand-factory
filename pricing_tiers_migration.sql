-- == BFN Ops pricing tiers (2026-09-23): founding / standard / pro
-- Run in the Supabase SQL Editor BEFORE deploying the code that uses it.
-- Existing rows become 'founding' (they were all sold at the $49 + $29 price).

alter table public.ops_orders
  add column if not exists plan text not null default 'founding';
alter table public.ops_orders drop constraint if exists ops_orders_plan_check;
alter table public.ops_orders
  add constraint ops_orders_plan_check check (plan in ('founding', 'standard', 'pro'));

alter table public.wf_brains
  add column if not exists plan text not null default 'founding';
alter table public.wf_brains
  add column if not exists monthly_text_cap integer not null default 500;

create index if not exists ops_orders_plan_status_idx on public.ops_orders(plan, status);
