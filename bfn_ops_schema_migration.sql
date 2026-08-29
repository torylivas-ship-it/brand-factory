-- == BFN Ops Schema Migration
-- Run this in the Supabase SQL Editor (Project: wlxjekmxobhbviwmtvrm)
-- Separate table from `orders` — BFN Ops is a distinct product (automation
-- setup + monthly service) with its own intake fields, not another tier of
-- the content-pack product.

create table if not exists public.ops_orders (
  id                      uuid primary key default uuid_generate_v4(),
  user_id                 uuid references public.profiles(id) on delete set null,
  referred_by_employee_id uuid references public.profiles(id) on delete set null,
  email                   text not null,
  business_name           text not null,
  business_type           text not null,
  city                    text not null,
  phone                   text,
  booking_system          text,
  automations             text[] not null,
  notes                   text,
  status                  text not null default 'pending'
                            check (status in ('pending', 'active', 'canceled')),
  stripe_session_id       text unique,
  stripe_subscription_id  text,
  amount_paid             integer,
  paid_at                 timestamptz,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);

create index if not exists ops_orders_email_idx on public.ops_orders(email);
create index if not exists ops_orders_stripe_session_idx on public.ops_orders(stripe_session_id);
create index if not exists ops_orders_referred_by_employee_idx
  on public.ops_orders(referred_by_employee_id) where referred_by_employee_id is not null;

create trigger set_ops_orders_updated_at
  before update on public.ops_orders
  for each row execute function public.handle_updated_at();

alter table public.ops_orders enable row level security;

create policy "users read own ops orders" on public.ops_orders
  for select using (auth.uid() = user_id);

create policy "service role full access ops_orders" on public.ops_orders
  for all using (auth.role() = 'service_role');

revoke insert, update, delete, truncate on public.ops_orders from anon, authenticated;
revoke select on public.ops_orders from anon;
