from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class Project(BaseModel):
    id: str
    user_id: str
    name: str
    type: str  # 'website' | 'social' | 'branding' | 'hashtag'
    status: str = "draft"  # 'draft' | 'preview' | 'exported' | 'deployed'
    content: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
