import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from routes import orders, billing

load_dotenv()

app = FastAPI(
    title="The Brand Factory NOLA API",
    description="Backend API for The Brand Factory NOLA",
    version="2.0.0",
)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url, "http://localhost:3000", "http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])


@app.get("/", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "Brand Factory NOLA API"}
