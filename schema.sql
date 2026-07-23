-- ============================================================
-- The Brand Factory NOLA — Supabase SQL Schema
-- Paste this into the Supabase SQL Editor and run it.
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── profiles ────────────────────────────────────────────────
create table if not exists public.profiles (
  id                    uuid primary key references auth.users(id) on delete cascade,
  email                 text not null,
  full_name             text,
  plan                  text not null default 'free'
                          check (plan in ('free', 'starter', 'growth', 'agency')),
  stripe_customer_id    text unique,
  stripe_subscription_id text,
  plan_expires_at       timestamptz,
  is_lifetime           boolean not null default false,
  is_admin              boolean not null default false,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- ── projects ────────────────────────────────────────────────
create table if not exists public.projects (
  id          uuid primary key default uuid_generate_v4(),
  user_id     uuid not null references public.profiles(id) on delete cascade,
  name        text not null,
  type        text not null
                check (type in ('website', 'social', 'branding', 'hashtag')),
  status      text not null default 'draft'
                check (status in ('draft', 'preview', 'exported', 'deployed')),
  content     jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists projects_user_id_idx on public.projects(user_id);

-- ── exports ─────────────────────────────────────────────────
create table if not exists public.exports (
  id            uuid primary key default uuid_generate_v4(),
  user_id       uuid not null references public.profiles(id) on delete cascade,
  project_id    uuid references public.projects(id) on delete set null,
  export_type   text not null
                  check (export_type in ('zip', 'pdf', 'deploy')),
  export_url    text,
  created_at    timestamptz not null default now()
);

create index if not exists exports_user_id_idx on public.exports(user_id);

-- ── stripe_events ────────────────────────────────────────────
create table if not exists public.stripe_events (
  id               uuid primary key default uuid_generate_v4(),
  stripe_event_id  text not null unique,
  event_type       text not null,
  payload          jsonb not null,
  processed        boolean not null default false,
  created_at       timestamptz not null default now()
);

create index if not exists stripe_events_event_id_idx on public.stripe_events(stripe_event_id);

-- ── updated_at trigger ───────────────────────────────────────
create or replace function public.handle_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger set_profiles_updated_at
  before update on public.profiles
  for each row execute function public.handle_updated_at();

create trigger set_projects_updated_at
  before update on public.projects
  for each row execute function public.handle_updated_at();

-- ── Auto-create profile on sign-up ──────────────────────────
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, full_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', '')
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ── Row Level Security ───────────────────────────────────────
alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.exports enable row level security;
alter table public.stripe_events enable row level security;

-- profiles RLS
create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

create policy "Admins can view all profiles"
  on public.profiles for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

create policy "Admins can update all profiles"
  on public.profiles for update
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

-- projects RLS
create policy "Users can manage own projects"
  on public.projects for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Admins can view all projects"
  on public.projects for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

-- exports RLS
create policy "Users can manage own exports"
  on public.exports for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Admins can view all exports"
  on public.exports for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

-- stripe_events: only service role (backend) should read/write; no auth.uid() policies needed
-- The backend uses the service role key which bypasses RLS entirely.
-- Grant nothing to authenticated users.
create policy "No direct user access to stripe_events"
  on public.stripe_events for all
  using (false);

-- ── orders ──
create table if not exists public.orders (
  id                    uuid primary key default uuid_generate_v4(),
  email                 text not null,
  business_name         text not null,
  business_type         text not null,
  city                  text not null,
  neighborhood          text,
  target_audience       text not null,
  platforms             text[] not null,
  tone                  text not null,
  special_offers        text,
  goals                 text,
  tier                  text not null check (tier in ('starter', 'growth', 'agency')),
  status                text not null default 'pending' check (status in ('pending', 'generating', 'complete', 'failed')),
  stripe_session_id     text unique,
  stripe_payment_intent text,
  amount_paid           integer,
  paid_at               timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index if not exists orders_user_id_idx on public.orders(email);
create index if not exists orders_stripe_session_idx on public.orders(stripe_session_id);

-- ── packs ──
create table if not exists public.packs (
  id                        uuid primary key default uuid_generate_v4(),
  order_id                  uuid not null references public.orders(id) on delete cascade,
  strategy_overview         text,
  content_calendar          jsonb,
  captions                  jsonb,
  hashtag_groups            jsonb,
  posting_schedule          jsonb,
  website_html              text,
  generation_time_seconds   integer,
  created_at                timestamptz not null default now()
);
create index if not exists packs_order_id_idx on public.packs(order_id);

-- Auto-update packs.updated_at
create trigger set_packs_updated_at
  before update on public.packs
  for each row execute function public.handle_updated_at();

-- ── Row Level Security ──
alter table public.orders enable row level security;
alter table public.packs enable row level security;

-- orders: public-facing, allow viewing own orders
create policy "Users can view own orders" on public.orders
  for select using (true); -- public-facing, backend uses service_role to bypass

-- packs: only accessible via owner's order
create policy "Users can view own packs" on public.packs
  for select using (
    exists (select 1 from public.orders o where o.id = packs.order_id)
  );

-- Backend uses service role key — bypasses all RLS
