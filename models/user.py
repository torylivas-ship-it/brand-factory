from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UserProfile(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    plan: str = "free"
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
    is_lifetime: bool = False
    is_admin: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
