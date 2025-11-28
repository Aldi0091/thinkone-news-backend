from datetime import datetime
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any


class NewsItem(BaseModel):
    id: str
    channel_id: int
    channel_username: Optional[str] = None
    channel_title: str
    title: str
    text: str
    source: str
    sourceUrl: Optional[HttpUrl] = None
    url: Optional[HttpUrl] = None
    summary: Optional[str] = None
    publishedAt: datetime
    tags: List[str] = []
    media: Optional[Dict[str, Any]] = None

class NewsList(BaseModel):
    total: int
    items: List[NewsItem]
    next_offset: Optional[int] = None 
