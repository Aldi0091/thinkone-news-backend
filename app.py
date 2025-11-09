import os
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Query, HTTPException, Path, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl
from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError
from dotenv import load_dotenv
import asyncio
import io
import logging

logger = logging.getLogger("thinkone")
logging.basicConfig(level=logging.INFO)

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
CORS_ORIGINS = (os.getenv("CORS_ORIGINS") or "http://localhost:5174").split(",")

# список каналов по умолчанию (через ENV)
DEFAULT_CHANNELS = [c.strip() for c in (os.getenv("TG_CHANNELS") or "").split(",") if c.strip()]

app = FastAPI(title="ThinkOne Telegram News API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-TG-Errors"],
)

# Глобальный Telethon-клиент, использует ранее созданную сессию "tg.session"
tg_client = TelegramClient("tg", API_ID, API_HASH)

@app.on_event("startup")
async def on_startup():
    await tg_client.connect()
    if not await tg_client.is_user_authorized():
        # если почему-то потеряли авторизацию — явно сказать
        raise RuntimeError("Telegram session is not authorized. Run tg_login.py first.")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_client.disconnect()

# ---------- Схемы ----------
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
    media: Optional[Dict[str, Any]] = None  # { kind: photo|video|document, proxyUrl: str, mime: str|null, size: int|null }

class NewsList(BaseModel):
    total: int
    items: List[NewsItem]
    next_offset: Optional[int] = None 


# ---------- Утилиты ----------
async def resolve_channel(entity: str):
    """
    entity: @username | username | https://t.me/username | numeric id
    """
    e = entity.strip().replace("https://t.me/", "").replace("@", "")
    try:
        return await tg_client.get_entity(e)
    except (UsernameInvalidError, UsernameNotOccupiedError):
        # возможно, это numeric id
        try:
            return await tg_client.get_entity(int(e))
        except Exception as ex:
            raise HTTPException(400, f"Cannot resolve channel '{entity}': {ex}")
    except Exception as ex:
        raise HTTPException(400, f"Cannot resolve channel '{entity}': {ex}")

def pick_title_and_url(msg: Message):
    # у каналов часто вся новость в msg.message; title возьмём из первых 80 символов
    text = (msg.message or "").strip()
    title = text.split("\n", 1)[0][:120] or "Post"
    # если есть web preview, можно вытащить url
    url = None
    if getattr(msg, "media", None) and getattr(msg.media, "webpage", None):
        url = getattr(msg.media.webpage, "url", None)
    return title, text, url

def media_info(msg: Message) -> Optional[Dict[str, Any]]:
    if msg.photo:
        return {"kind": "photo"}
    if msg.video:
        mime = getattr(msg.video, "mime_type", None)
        size = getattr(msg.video, "size", None)
        return {"kind": "video", "mime": mime, "size": size}
    if msg.document:
        mime = getattr(msg.document, "mime_type", None)
        size = getattr(msg.document, "size", None)
        return {"kind": "document", "mime": mime, "size": size}
    return None

# ---------- Эндпоинты ----------
@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/api/tg/channels")
async def list_default_channels():
    return {"channels": DEFAULT_CHANNELS}


@app.get("/api/tg/news", response_model=NewsList)
async def get_news(
    channels: Optional[str] = Query(default=None, description="comma-separated usernames"),
    limit: int = Query(default=30, ge=1, le=200),
    offset_id: int = Query(default=0, ge=0, description="for infinite scroll, Telegram message id offset"),
    sort: str = Query(default="newest", pattern="^(newest|oldest|source)$"),
    response: Response = None,  # <-- получаем Response от FastAPI
):
    chan_list = [c.strip() for c in (channels.split(",") if channels else DEFAULT_CHANNELS) if c.strip()]
    if not chan_list:
        return NewsList(total=0, items=[])

    results: List[NewsItem] = []
    errors: Dict[str, str] = {}

    async def fetch_one(ch):
        try:
            ent = await resolve_channel(ch)
        except HTTPException as ex:
            logger.warning(f"Resolve failed for '{ch}': {ex.detail}")
            errors[ch] = str(ex.detail)
            return []
        except Exception as ex:
            logger.exception(f"Resolve failed for '{ch}': {ex}")
            errors[ch] = f"{type(ex).__name__}: {ex}"
            return []

        msgs = await tg_client.get_messages(ent, limit=limit, offset_id=offset_id)
        out: List[NewsItem] = []
        for m in msgs:
            if not isinstance(m, Message):
                continue
            title, text, url = pick_title_and_url(m)
            if not text:
                continue
            chan_title = getattr(ent, "title", getattr(ent, "first_name", "Channel"))
            username = getattr(ent, "username", None)
            out.append(NewsItem(
                id=str(m.id),
                channel_id=ent.id,
                channel_username=username,
                channel_title=chan_title,
                title=title,
                text=text,
                source=chan_title,
                sourceUrl=f"https://t.me/{username}" if username else None,
                url=url,
                summary=None,
                publishedAt=m.date,
                tags=[],
                media=None
            ))
        return out

    fetched = await asyncio.gather(*(fetch_one(c) for c in chan_list))
    for arr in fetched:
        results.extend(arr)

    if sort == "newest":
        results.sort(key=lambda x: x.publishedAt, reverse=True)
    elif sort == "oldest":
        results.sort(key=lambda x: x.publishedAt)
    else:
        results.sort(key=lambda x: (x.source.lower(), x.publishedAt), reverse=True)

    results = results[:limit]

    next_offset = None
    if results:
        try:
            next_offset = min(int(n.id) for n in results)
        except Exception:
            next_offset = None

    # корректно выставляем заголовок с ошибками (если были)
    if response is not None and errors:
        response.headers["X-TG-Errors"] = "; ".join(f"{k}:{v}" for k, v in errors.items())

    return NewsList(
        total=len(results),
        items=results,
        next_offset=next_offset
    )


@app.get("/api/tg/media/{channel_id}/{message_id}")
async def proxy_media(
    channel_id: int = Path(...),
    message_id: int = Path(...)
):
    """
    Проксируем медиа по запросу фронта (не храним на диске).
    """
    ent = await tg_client.get_entity(channel_id)
    msg: Message = await tg_client.get_messages(ent, ids=message_id)
    if not msg or not (msg.photo or msg.video or msg.document):
        raise HTTPException(404, "Media not found")

    # Сливаем в память буфером, для больших видео лучше писать на диск/в S3
    buf = io.BytesIO()
    await tg_client.download_media(msg, file=buf)
    buf.seek(0)

    mime = "application/octet-stream"
    if msg.video and getattr(msg.video, "mime_type", None):
        mime = msg.video.mime_type
    elif msg.document and getattr(msg.document, "mime_type", None):
        mime = msg.document.mime_type
    elif msg.photo:
        mime = "image/jpeg"

    return StreamingResponse(buf, media_type=mime)
