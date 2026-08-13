-- == OAuth Token Schema Migration for BFN AutoPost
-- Run this in the Supabase SQL Editor (Project: wlxjekmxobhbviwmtvrm)
-- Adds oauth_tokens and app_config tables for IG/TikTok/Google Business OAuth.
--
-- access_token/refresh_token are stored ENCRYPTED (application-level Fernet,
-- see services/oauth_service.py) — the `text` column holds ciphertext, not a
-- usable token, even if RLS is ever misconfigured again.

-- == 1. OAuth Tokens Table ==
create table if not exists public.oauth_tokens (
  id                    uuid primary key default uuid_generate_v4(),
  user_id               uuid not null references public.profiles(id) on delete cascade,
  platform              text not null
                          check (platform in ('instagram', 'tiktok', 'google_business')),
  access_token           text not null,
  refresh_token          text default null,
  scope                  text,
  token_type             text default 'Bearer',
  expires_at             timestamptz default null,
  business_id            text,
  extra_meta             jsonb default '{}',
  is_active              boolean not null default true,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create index if not exists oauth_tokens_user_id_idx
  on public.oauth_tokens(user_id);
create index if not exists oauth_tokens_platform_idx
  on public.oauth_tokens(platform);
create index if not exists oauth_tokens_active_idx
  on public.oauth_tokens(is_active) where is_active = true;

create trigger set_oauth_tokens_updated_at
  before update on public.oauth_tokens
  for each row execute function public.handle_updated_at();

alter table public.oauth_tokens enable row level security;

create policy "Users can view own tokens"
  on public.oauth_tokens for select
  using (auth.uid() = user_id);

create policy "Users can insert own tokens"
  on public.oauth_tokens for insert
  with check (auth.uid() = user_id);

create policy "Users can update own tokens"
  on public.oauth_tokens for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Admins can view all tokens"
  on public.oauth_tokens for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

create policy "Admins can update all tokens"
  on public.oauth_tokens for update
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

create policy "No user deletion of tokens"
  on public.oauth_tokens for delete
  using (false);

-- == 2. App Config Table (admin-only; client_id/secret per platform) ==
create table if not exists public.app_config (
  id                    uuid primary key default uuid_generate_v4(),
  platform              text not null unique
                          check (platform in ('instagram', 'tiktok', 'google_business')),
  client_id             text not null,
  client_secret         text not null,
  redirect_uri          text not null,
  requested_scopes      text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create trigger set_app_config_updated_at
  before update on public.app_config
  for each row execute function public.handle_updated_at();

alter table public.app_config enable row level security;

create policy "Admins only can access app_config"
  on public.app_config for all
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_admin = true
    )
  );

-- == 3. app_config rows — fill in once Tory has created the Meta/TikTok/Google
--       dev apps and has real client_id/client_secret. Redirect URIs point at
--       the live Railway backend (no custom domain purchased yet):
--
-- INSERT INTO public.app_config (platform, client_id, client_secret, redirect_uri, requested_scopes)
-- VALUES
--   ('instagram', 'YOUR_META_APP_ID', 'YOUR_META_APP_SECRET',
--    'https://brand-factory-production-b27f.up.railway.app/auth/instagram/callback',
--    'instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement'),
--   ('tiktok', 'YOUR_TIKTOK_CLIENT_KEY', 'YOUR_TIKTOK_CLIENT_SECRET',
--    'https://brand-factory-production-b27f.up.railway.app/auth/tiktok/callback',
--    'user.info.email,user.info.profile'),
--   ('google_business', 'YOUR_GOOGLE_CLIENT_ID', 'YOUR_GOOGLE_CLIENT_SECRET',
--    'https://brand-factory-production-b27f.up.railway.app/auth/google_business/callback',
--    'https://www.googleapis.com/auth/business.manage');
