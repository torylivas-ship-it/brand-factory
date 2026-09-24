import asyncio
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from routes import orders, billing, account, social_oauth, admin, auth, ops, outreach, workframe
from services.workframe_engine import scheduler_loop

load_dotenv()

app = FastAPI(
    title="The Brand Factory NOLA API",
    description="Backend API for The Brand Factory NOLA",
    version="2.0.0",
)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")


class PathScopedCORS:
    """Strict CORS for the app (BFN's own frontend only, with credentials),
    but open CORS for /wf/public/* — the Workframe chat widget runs on each
    client's own website, whatever domain that is. Those public routes take
    no cookies/credentials, so allowing any origin there is safe."""

    def __init__(self, app):
        self.strict = CORSMiddleware(
            app,
            allow_origins=[frontend_url, "http://localhost:3000", "http://127.0.0.1:5500"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.public = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/wf/public/"):
            await self.public(scope, receive, send)
        else:
            await self.strict(scope, receive, send)


app.add_middleware(PathScopedCORS)

app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
app.include_router(account.router, prefix="/account", tags=["account"])
app.include_router(social_oauth.router, prefix="/auth", tags=["oauth"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(ops.router, prefix="/ops", tags=["ops"])
app.include_router(outreach.router, prefix="/outreach", tags=["outreach"])
app.include_router(workframe.router, prefix="/wf", tags=["workframe"])


@app.on_event("startup")
async def start_workframe_scheduler():
    # Off unless explicitly enabled, so local dev and tests never text anyone.
    if os.getenv("WF_SCHEDULER_ENABLED") == "1":
        asyncio.create_task(scheduler_loop())


@app.get("/", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "Brand Factory NOLA API"}
