# Deploy Checklist — The Brand Factory NOLA

Follow these steps in order. Each section builds on the last.

---

## 1. Supabase Project Setup

- [ ] Go to https://supabase.com and create a new project
- [ ] Set a strong database password and save it
- [ ] Wait for project to finish provisioning (~2 min)
- [ ] Go to **Settings → API** and copy:
  - `Project URL` → this is your `SUPABASE_URL`
  - `anon / public` key → this is your `SUPABASE_ANON_KEY`
  - `service_role` key → this is your `SUPABASE_SERVICE_KEY` (keep secret!)
- [ ] Go to **SQL Editor** and paste the full contents of `schema.sql`
- [ ] Click **Run** — confirm no errors
- [ ] Go to **Authentication → Settings**:
  - Enable Email provider
  - Set Site URL to your Vercel frontend URL (can update later)
  - Optionally enable email confirmation (disable for faster dev testing)

---

## 2. Stripe Products & Prices

- [ ] Go to https://dashboard.stripe.com and sign in
- [ ] Go to **Products** and create 2 products:
  - **Brand Factory NOLA Pro**
  - **Brand Factory NOLA Agency**
- [ ] For each product, add prices:

  **Pro — Monthly**
  - Recurring, monthly
  - Price: $29/month
  - Copy the Price ID → `STRIPE_PRICE_PRO_MONTHLY`

  **Pro — Lifetime**
  - One-time
  - Price: $197
  - Copy the Price ID → `STRIPE_PRICE_PRO_LIFETIME`

  **Agency — Monthly**
  - Recurring, monthly
  - Price: $79/month
  - Copy the Price ID → `STRIPE_PRICE_AGENCY_MONTHLY`

  **Agency — Lifetime**
  - One-time
  - Price: $497
  - Copy the Price ID → `STRIPE_PRICE_AGENCY_LIFETIME`

- [ ] Go to **Developers → Webhooks** and add a new endpoint:
  - URL: `https://YOUR_RAILWAY_URL/billing/webhook`
  - Events to listen for:
    - `checkout.session.completed`
    - `customer.subscription.updated`
    - `customer.subscription.deleted`
    - `invoice.payment_failed`
  - Copy the **Signing secret** → `STRIPE_WEBHOOK_SECRET`
- [ ] Copy your **Secret key** from API Keys → `STRIPE_SECRET_KEY`

---

## 3. Deploy Backend to Railway

- [ ] Push the `brand-factory-backend/` folder to a GitHub repo
- [ ] Go to https://railway.app → New Project → Deploy from GitHub repo
- [ ] Select the repo and let Railway detect the Nixpacks config
- [ ] Go to **Variables** and add all env vars from `.env.example`:
  ```
  SUPABASE_URL=
  SUPABASE_SERVICE_KEY=
  SUPABASE_ANON_KEY=
  STRIPE_SECRET_KEY=
  STRIPE_WEBHOOK_SECRET=
  STRIPE_PRICE_PRO_MONTHLY=
  STRIPE_PRICE_PRO_LIFETIME=
  STRIPE_PRICE_AGENCY_MONTHLY=
  STRIPE_PRICE_AGENCY_LIFETIME=
  OPENAI_API_KEY=
  FRONTEND_URL=https://your-app.vercel.app
  ```
- [ ] Railway deploys automatically — wait for green health check at `GET /`
- [ ] Copy your Railway public URL (e.g. `https://brand-factory-backend.up.railway.app`)
- [ ] Update `FRONTEND_URL` in Railway env to your actual Vercel URL once deployed

---

## 4. Deploy Frontend to Vercel

- [ ] Push the `brand-factory-frontend/` folder to a GitHub repo (can be same monorepo)
- [ ] Go to https://vercel.com → New Project → Import GitHub repo
- [ ] Set root directory to `brand-factory-frontend/` if using monorepo
- [ ] Add Environment Variables in Vercel dashboard:
  ```
  VITE_API_URL=https://your-backend.up.railway.app   (or use window.BFN_CONFIG)
  ```
- [ ] In `auth.html` and your `index.html`, update `window.BFN_CONFIG`:
  ```js
  window.BFN_CONFIG = {
    apiUrl: "https://your-backend.up.railway.app",
    supabaseUrl: "https://your-project.supabase.co",
    supabaseAnonKey: "your-anon-key",
  };
  ```
- [ ] Deploy — Vercel auto-deploys on push
- [ ] Copy your Vercel URL

---

## 5. Wire Everything Together

- [ ] In Railway env vars, update `FRONTEND_URL` to your Vercel URL
- [ ] In Supabase → Auth → Settings, set **Site URL** to your Vercel URL
- [ ] In Supabase → Auth → URL Configuration, add your Vercel URL to **Redirect URLs**
- [ ] In Stripe Webhooks, update the endpoint URL to your Railway URL

---

## 6. Test Stripe Webhooks Locally (Optional but Recommended)

```bash
# Install Stripe CLI
brew install stripe/stripe-cli/stripe

# Login
stripe login

# Forward webhooks to local backend
stripe listen --forward-to localhost:8000/billing/webhook

# In another terminal, trigger a test event
stripe trigger checkout.session.completed
```

- [ ] Confirm the backend logs `processed: True` for the event
- [ ] Confirm the user's plan updates in the Supabase profiles table

---

## 7. Go-Live Checklist

- [ ] Switch Stripe from **Test mode** to **Live mode** (update `STRIPE_SECRET_KEY`)
- [ ] Create live Stripe products/prices (repeat Step 2 in live mode)
- [ ] Update all `STRIPE_PRICE_*` env vars to live Price IDs
- [ ] Update Stripe webhook to use live signing secret
- [ ] Test a real $1 purchase end-to-end (use a real card, then refund)
- [ ] Verify Supabase RLS is enabled on all tables (`schema.sql` does this)
- [ ] Ensure `SUPABASE_SERVICE_KEY` is NEVER exposed to the frontend
- [ ] Set up Supabase backups: **Settings → Database → Backups**
- [ ] Configure a custom domain in Vercel (optional but recommended)
- [ ] Set up Railway custom domain for the API (optional)
- [ ] Create your first admin user:
  ```sql
  -- Run in Supabase SQL Editor after signing up
  UPDATE profiles SET is_admin = true WHERE email = 'your@email.com';
  ```
- [ ] Smoke test every tool (website gen, social, hashtags, branding)
- [ ] Smoke test checkout flow for both Pro and Agency plans
- [ ] Check the admin panel at `/admin/stats`

---

**You're live.** Ship it.
