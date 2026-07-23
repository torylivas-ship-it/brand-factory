# Brand Factory NOLA — Backend API

FastAPI backend powering The Brand Factory NOLA SaaS platform.

## Stack
- **FastAPI** + Uvicorn
- **Supabase** — auth, database, RLS
- **Stripe** — one-time purchases (Starter $49, Growth $99, Agency $149)
- **OpenAI** (gpt-4o-mini) — AI tools

## Quick Start

```bash
cp .env.example .env
# Fill in your .env values

pip install -r requirements.txt
uvicorn main:app --reload
```

API docs: http://localhost:8000/docs

## Structure

```
main.py                  — App entrypoint, CORS, router mounts
routes/
  auth.py                — /auth/me (JWT verification)
  tools.py               — /tools/* (website, social, hashtags, branding)
  billing.py             — /billing/* (Stripe checkout, webhook, portal)
  admin.py               — /admin/* (admin-only stats + user management)
middleware/
  auth_guard.py          — JWT dependency, injects {user_id, plan, is_admin}
services/
  supabase_service.py    — Supabase admin client singleton
  openai_service.py      — OpenAI generation functions
  stripe_service.py      — Stripe helpers (customer, price resolution)
utils/
  feature_gate.py        — Plan hierarchy enforcement (free < pro < agency)
  zip_builder.py         — In-memory ZIP builder for website export
models/
  user.py                — UserProfile Pydantic model
  project.py             — Project Pydantic model
schema.sql               — Full Supabase schema + RLS policies
deploy-checklist.md      — Step-by-step deployment guide
```

## Deployment

Deploy to Railway — see `deploy-checklist.md` for full instructions.
