# Deploy Checklist — The Brand Factory NOLA

One-time-purchase SaaS: Starter ($49), Growth ($99), Agency ($149). Backend: FastAPI on Railway; Frontend: Next.js on Vercel; DB: Supabase; Payments: Stripe.

---

## 0. What was fixed (code)

All code fixes in this task are complete. Do not repeat them — they're already merged in the working tree:

| Fix | File | What changed |
|-----|------|--------------|
| `profiles.plan` constraint | `schema.sql` | `('free', 'pro', 'agency')` → `('free', 'starter', 'growth', 'agency')` |
| One-time revenue stats | `routes/admin.py` | Removed `mrr_estimate_usd`; now computes `total_revenue_usd = starter_count×49 + growth_count×99 + agency_count×149` |
| Plan hierarchy | `utils/feature_gate.py` | `PLAN_HIERARCHY = {"free":0,"starter":1,"growth":2,"agency":3}` |
| `check_plan("pro")` → `check_plan("growth")` | `routes/tools.py` | All 4 calls updated; `is_pro` → `is_growth`; watermark logic aligned |
| `orders` + `packs` tables | `schema.sql` | Full DDL + indexes + triggers + RLS policies appended |
| Stripe pricing model | `README.md` | Changed "subscriptions + webhooks" → "one-time purchases (Starter $49, Growth $99, Agency $149)" |

---

## 1. Run SQL on live Supabase DB

These steps must be done on the **live** Supabase project (`wlxjekmxobhbviwmtvrm`) — they update the running database.

### 1a. Run schema.sql in full

- [ ] Go to your Supabase project at https://supabase.com/dashboard/project/wlxjekmxobhbviwmtvrm
- [ ] Open **SQL Editor**
- [ ] Paste the **full contents of `schema.sql`** and click **Run**
- [ ] Confirm no errors

If the script has already been run before (during earlier dev), you may get "table already exists" errors — that's expected for the existing tables (profiles, projects, exports, stripe_events). The important new part is the appended `orders` and `packs` tables. To be safe, you can run just the new tables:

```sql
-- Run this if schema.sql has already been applied before:

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

create trigger set_packs_updated_at
  before update on public.packs
  for each row execute function public.handle_updated_at();

alter table public.orders enable row level security;
alter table public.packs enable row level security;

create policy "Users can view own orders" on public.orders
  for select using (true);

create policy "Users can view own packs" on public.packs
  for select using (exists (select 1 from public.orders o where o.id = packs.order_id));
```

### 1b. Update profiles.plan constraint

- [ ] In Supabase SQL Editor, run the following to align the live constraint:

```sql
alter table public.profiles drop constraint if exists profiles_plan_check;
alter table public.profiles add constraint profiles_plan_check
  check (plan in ('free', 'starter', 'growth', 'agency'));
```

**Note:** If any existing profiles have `plan = 'pro'`, update them first:

```sql
update public.profiles set plan = 'growth' where plan = 'pro';
```

### Checklist after SQL

- [ ] All 6 tables visible: `profiles`, `projects`, `exports`, `stripe_events`, `orders`, `packs`
- [ ] `orders.tier` check constraint = `starter | growth | agency`
- [ ] `profiles.plan` check constraint = `free | starter | growth | agency`
- [ ] `packs.strategy_overview` is `jsonb` (not JSON)

---

## 2. Stripe — One-Time Purchase Setup

### 2a. Verify products in Stripe Dashboard

- [ ] Go to [Stripe Dashboard > Products](https://dashboard.stripe.com/products)
- [ ] Confirm these three products exist:
  - **Brand Factory Starter** — one-time, $49
  - **Brand Factory Growth** — one-time, $99
  - **Brand Factory Agency** — one-time, $149
- [ ] Confirm Price IDs match what's in `.env` (currently pointing to test-mode `price_` keys from the prior run)

### 2b. Stripe Webhooks

- [ ] Go to **Developers → Webhooks** → Add endpoint
- [ ] URL: `https://brand-factory-production-b27f.up.railway.app/billing/webhook`
- [ ] Events to listen for:
  - `checkout.session.completed`
  - `customer.subscription.created` (handle gracefully in case Stripe creates one anyway)
- [ ] Copy the **Signing secret** → update `STRIPE_WEBHOOK_SECRET` on Railway
- [ ] Copy the **Secret key** → update `STRIPE_SECRET_KEY` on Railway

### 2c. When ready for production (live mode)

- [ ] Switch Stripe from **Test mode** to **Live mode**
- [ ] Create live Price IDs for the three tiers
- [ ] Update `STRIPE_SECRET_KEY` to live key
- [ ] Update `STRIPE_PRICE_STARTER/GROWTH/AGENCY` to live Price IDs
- [ ] Update webhook endpoint to the live Railway URL
- [ ] Test a real $1 purchase end-to-end
- [ ] Run a refund after test

---

## 3. Deploy Backend to Railway

### 3a. Railway project (already linked)

- [ ] Railway project **already linked**: "loving-truth" / service "brand-factory" / env "production"
- [ ] Current status: **Offline** (needs deployment)

### 3b. Set environment variables in Railway

Go to **Railway Dashboard > Project > Variables** and ensure all these are set:

```
SUPABASE_URL=https://wlxjekmxobhbviwmtvrm.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>          # keep secret
SUPABASE_ANON_KEY=<anon-key>
STRIPE_SECRET_KEY=<sk_live_ or sk_test_>         # sk_test_ OK for staging
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_...
STRIPE_PRICE_GROWTH=price_...
STRIPE_PRICE_AGENCY=price_...
OPENAI_API_KEY=sk-...
FRONTEND_URL=https://brand-factory-nola.vercel.app   # ← UPDATE to your Vercel URL
```

### 3c. Deploy

- [ ] Commit/push all changes:
  ```
  git add -A
  git commit -m "Fix plan hierarchy, add orders/packs tables, one-time pricing"
  git push
  ```
- [ ] Railway auto-deploys on push
- [ ] Verify green health check at `GET /` → should return:
  ```json
  {"status": "ok", "service": "Brand Factory NOLA API"}
  ```
- [ ] Confirm `POST /orders/create` returns a Stripe checkout URL (not a 500)
- [ ] Confirm `POST /billing/webhook` responds `{ "status": "ok" }` with test payload

### 3d. Verify on Railway URL

Visit: `https://brand-factory-production-b27f.up.railway.app/docs`

Ensure the API docs appear and all router groups are listed:
- `/orders` (create, get, generate)
- `/billing` (webhook)
- `/auth` (/me)
- `/tools` (website, social, hashtag, branding)
- `/admin` (users, stats, projects)

---

## 4. Deploy Frontend to Vercel

- [ ] Push `brand-factory-frontend/` to your repo (or monorepo) on Vercel
- [ ] Add the actual Railway backend URL as env var `VITE_API_URL`
- [ ] Update `window.BFN_CONFIG` in `index.html` / `auth.html` with:
  ```js
  window.BFN_CONFIG = {
    apiUrl: "https://brand-factory-production-b27f.up.railway.app",
    supabaseUrl: "https://wlxjekmxobhbviwmtvrm.supabase.co",
    supabaseAnonKey: "<anon-key-from-supabase>",
  };
  ```
- [ ] Deploy — Vercel auto-deploys on push
- [ ] Copy your Vercel URL

---

## 5. Wire Everything Together

- [ ] Update Railway env var `FRONTEND_URL` to your Vercel URL (if not done in Step 3b)
- [ ] In Supabase → Auth → Settings, set **Site URL** to your Vercel URL
- [ ] In Supabase → Auth → URL Configuration, add your Vercel URL to **Redirect URLs**
- [ ] In Stripe Dashboard → Webhooks, update the endpoint URL to your Railway URL
- [ ] Test the full flow: signup → create order → Stripe checkout → webhook → pack generation → view order

---

## 6. Go-Live Final Checklist

- [ ] All code changes pushed and deployed
- [ ] `profiles.plan` constraint allows `starter | growth | agency` on live DB
- [ ] `orders` and `packs` tables exist on production Supabase
- [ ] Stripe keys set to **live** (not test mode) — if ready
- [ ] Live Stripe Price IDs set
- [ ] Stripe webhook uses **live** signing secret
- [ ] FRONTEND_URL env var points to live Vercel URL
- [ ] Supabase Auth redirect URLs include Vercel URL
- [ ] Test one real $1 purchase end-to-end (then refund)
- [ ] Verify `SUPABASE_SERVICE_KEY` is never exposed to frontend
- [ ] Set up Supabase backups: **Settings → Database → Backups**
- [ ] Create your first admin user:
  ```sql
  UPDATE profiles SET is_admin = true WHERE email = 'thebrandfactorynola@gmail.com';
  ```
- [ ] Smoke test every tool: website gen, social, hashtags, branding
- [ ] Smoke test checkout for Starter, Growth, Agency tiers
- [ ] Check admin panel at `/admin/stats` → confirms `total_revenue_usd` shows correctly

**You're live.**
