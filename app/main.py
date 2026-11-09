"""
FastAPI Server for Social Video Automation Studio.
Exposes REST endpoints for Scraping, Card Previews, Audio Synthesis, and Full Video Rendering.
"""

import asyncio
import base64
import json
import os
import re
import sys
import time
import uuid
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure root package import works
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

load_dotenv(os.path.join(root_dir, ".env"))
_cloud_dat = os.path.join(root_dir, "cloud_env.dat")
if not os.getenv("TELEGRAM_BOT_TOKEN") and os.path.exists(_cloud_dat):
    try:
        _raw = base64.b64decode(Path(_cloud_dat).read_text(encoding="ascii").strip())
        _key = b"SOCIAL_VIDEO_CLOUD_VPS_KEY_2026"
        _dec = json.loads(bytes(b ^ _key[i % len(_key)] for i, b in enumerate(_raw)).decode("utf-8"))
        for _k, _v in _dec.items():
            if _k and _v is not None and not os.getenv(_k):
                os.environ[_k] = str(_v)
    except Exception:
        pass

from app.scraper import SocialScraper, CommentFilter, detect_platform
from app.renderer import CardRenderer
from app.tts_engine import TTSEngine, VIETNAMESE_VOICES, VOICE_CATALOG
from app.compositor import VideoCompositor
from app.sfx_generator import ensure_default_assets
from app.meme_engine import MemeEngine
from app.key_manager import KeyManager, ADMIN_USERNAME
from app.image_chat_engine import ImageChatEngine
from app.telegram_bot import TelegramVideoBotService

app = FastAPI(title="Social Video Automation Studio", version="1.0.0")

# Enable CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(root_dir)
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

for _req_dir in (
    OUTPUT_DIR,
    OUTPUT_DIR / "cards",
    OUTPUT_DIR / "tts",
    OUTPUT_DIR / "videos",
    OUTPUT_DIR / "uploads",
    ASSETS_DIR,
    STATIC_DIR,
    BASE_DIR / "data",
):
    _req_dir.mkdir(parents=True, exist_ok=True)

# Ensure asset directories and default media exist
default_assets = ensure_default_assets(str(BASE_DIR))

# Mount static and output folders
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# Global instances
scraper = SocialScraper()
renderer = CardRenderer(templates_dir=str(TEMPLATES_DIR), output_dir=str(OUTPUT_DIR / "cards"))
tts_engine = TTSEngine(output_dir=str(OUTPUT_DIR / "tts"))
compositor = VideoCompositor(output_dir=str(OUTPUT_DIR / "videos"))
meme_engine = MemeEngine(assets_dir=str(ASSETS_DIR))
image_chat_engine = ImageChatEngine(output_dir=str(OUTPUT_DIR / "cards"))
key_manager = KeyManager(db_path=str(BASE_DIR / "data" / "bot_auth.db"))
tg_bot_service = TelegramVideoBotService(
    key_manager=key_manager,
    scraper=scraper,
    renderer=renderer,
    tts_engine=tts_engine,
    compositor=compositor,
    meme_engine=meme_engine,
    default_assets=default_assets,
    image_chat_engine=image_chat_engine,
)

# Request Models
class ScrapeRequest(BaseModel):
    url: str
    min_likes: int = 0
    blocked_keywords: Optional[List[str]] = None

class CommentPayload(BaseModel):
    id: str
    username: str
    speaker: Optional[str] = None
    align: Optional[str] = "left"
    handle: Optional[str] = None
    avatar_url: Optional[str] = None
    image_url: Optional[str] = None
    card_image_path: Optional[str] = None
    card_url: Optional[str] = None
    is_chat_strip: bool = False
    content: str
    likes: str = "0"
    replies: int = 0
    reposts: str = "0"
    shares: str = "0"
    time_ago: str = ""
    is_op: bool = False
    selected: bool = True
    meme_reaction: Optional[str] = None
    sfx_reaction: Optional[str] = None

class RenderRequest(BaseModel):
    platform: str = "threads"
    dark_mode: bool = False
    voice: str = "vi-VN-HoaiMyNeural"
    speech_rate: str = "+0%"
    post: Optional[CommentPayload] = None
    comments: List[CommentPayload]
    background_file: Optional[str] = None
    bgm_file: Optional[str] = "meme_tiktok_viral.wav"
    bgm_volume: float = 0.12
    sfx_volume: float = 0.70
    running_text: Optional[str] = "@Vietcaigido?"
    running_text_mode: str = "top_left"
    channel_badge: bool = False

RENDER_JOBS: Dict[str, Dict[str, Any]] = {}
WEB_RENDER_SEM = asyncio.Semaphore(3)

@app.get("/")
def get_index():
    """Serves the main application page with no-cache headers so updates appear immediately."""
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )

@app.get("/api/voices")
def get_voices():
    """Returns supported Vietnamese Neural, Youthful, Dialogue & TikTok Viral voices."""
    return VOICE_CATALOG

@app.get("/api/assets")
def list_available_assets():
    """Lists available background videos, SFX, BGM music, and reaction memes."""
    bgs = sorted(
        [
            f.name
            for f in (ASSETS_DIR / "backgrounds").iterdir()
            if f.is_file() and f.suffix.lower() in (".mp4", ".mov")
        ]
    )
    if "minecraft_parkour.mp4" in bgs:
        bgs.remove("minecraft_parkour.mp4")
        bgs.insert(0, "minecraft_parkour.mp4")

    bgms = [f.name for f in (ASSETS_DIR / "bgm").glob("*.wav")] + [f.name for f in (ASSETS_DIR / "bgm").glob("*.mp3")]
    if "meme_tiktok_viral.wav" in bgms:
        bgms.remove("meme_tiktok_viral.wav")
        bgms.insert(0, "meme_tiktok_viral.wav")

    sfxs = [f.name for f in (ASSETS_DIR / "sfx").glob("*.wav")]
    memes = meme_engine.get_available_memes()
    
    return {
        "backgrounds": bgs,
        "bgm": bgms,
        "sfx": sfxs,
        "memes": memes
    }

@app.post("/api/scrape")
async def scrape_post_and_comments(req: ScrapeRequest):
    """Scrapes or parses post & comments asynchronously across worker threads."""
    post_data = await asyncio.to_thread(scraper.scrape, req.url)
    
    if req.min_likes > 0 or req.blocked_keywords:
        c_filter = CommentFilter(
            min_likes=req.min_likes,
            blocked_keywords=req.blocked_keywords
        )
        post_data.comments = c_filter.filter_comments(post_data.comments)

    # Default sticker to 'none' (clean @Vietcaigido? style) while keeping automatic funny SFX
    for comment in post_data.comments:
        rec = meme_engine.detect_comment_reaction(comment.content)
        comment.meme_reaction = "none"
        comment.sfx_reaction = rec.get("sfx_file") or "pop.wav"

    rec_bgm, reason = meme_engine.auto_select_bgm(post_data.content, [c.content for c in post_data.comments])

    res = post_data.model_dump()
    res["recommended_bgm"] = rec_bgm
    res["bgm_reason"] = reason
    return res

@app.post("/api/preview-card")
def preview_card(
    platform: str = "threads",
    dark_mode: bool = True,
    username: str = "nguoiquaduong",
    content: str = "Nội dung bình luận mẫu...",
    likes: str = "1.5K",
    replies: int = 10,
    is_op: bool = False
):
    """Generates an instant high-resolution PNG card for live UI preview."""
    item_id = f"prev_{uuid.uuid4().hex[:8]}"
    card_path = renderer.render_card(
        item_id=item_id,
        platform=platform,
        username=username,
        content=content,
        likes=likes,
        replies=replies,
        is_op=is_op,
        dark_mode=dark_mode
    )
    rel_path = Path(card_path).relative_to(BASE_DIR).as_posix()
    return {"card_url": f"/{rel_path}"}

@app.post("/api/preview-tts")
async def preview_tts(
    text: str,
    voice: str = "vi-VN-HoaiMyNeural",
    rate: str = "+0%"
):
    """Synthesizes a short TTS sample for audio testing."""
    item_id = f"sample_{uuid.uuid4().hex[:8]}"
    effective_v = "tiktok-BV075" if voice == "dialogue-auto" else voice
    result = await tts_engine.synthesize_item(
        item_id=item_id,
        text=text,
        voice=effective_v,
        rate=rate
    )
    rel_path = Path(result["audio_path"]).relative_to(BASE_DIR).as_posix()
    return {
        "audio_url": f"/{rel_path}",
        "duration": result["duration"],
        "tts_text": result["tts_text"]
    }

@app.post("/api/upload-chat-images")
async def upload_chat_images(files: List[UploadFile] = File(...)):
    """
    Accepts 1 or multiple chat screenshots (single-line strips or full conversations)
    in exact user-selected order, automatically slices bubbles top-to-bottom, and OCRs spoken text.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất 1 ảnh tin nhắn.")

    upload_dir = OUTPUT_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: List[str] = []

    try:
        for idx, up in enumerate(files):
            ext = ".png"
            if up.filename and "." in up.filename:
                ext = "." + up.filename.rsplit(".", 1)[-1].lower()
            dest = upload_dir / f"web_chat_{idx:03d}_{uuid.uuid4().hex[:6]}{ext}"
            data = await up.read()
            dest.write_bytes(data)
            saved_paths.append(str(dest))

        slices = await asyncio.to_thread(
            image_chat_engine.process_batch_images, saved_paths
        )
        if not slices:
            raise HTTPException(
                status_code=422,
                detail="Không nhận diện được dòng tin nhắn rõ nét nào trong ảnh tải lên.",
            )

        items = []
        for s in slices:
            rec = meme_engine.detect_comment_reaction(s.spoken_text)
            rel_card = Path(s.image_path).relative_to(BASE_DIR).as_posix()
            disp_speaker = s.speaker or ("Tin nhắn Phải" if s.align == "right" else "Tin nhắn Trái")
            items.append({
                "id": s.slice_id,
                "username": disp_speaker,
                "speaker": s.speaker or "",
                "align": s.align,
                "card_image_path": s.image_path,
                "card_url": f"/{rel_card}",
                "is_chat_strip": True,
                "content": s.spoken_text,
                "likes": "0",
                "replies": 0,
                "reposts": "0",
                "shares": "0",
                "selected": True,
                "meme_reaction": "none",
                "sfx_reaction": rec.get("sfx_file") or "pop.wav",
            })
        return {"status": "success", "count": len(items), "items": items}
    finally:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass

async def _run_render_pipeline(
    payload: RenderRequest,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    selected_comments = [c for c in payload.comments if c.selected]
    if not selected_comments and not payload.post:
        raise ValueError("Vui lòng chọn ít nhất bài đăng hoặc 1 bình luận/tin nhắn.")

    def _emit(pct: int, stage: str) -> None:
        if on_progress:
            on_progress(max(0, min(100, int(pct))), stage)

    _emit(3, "Đang khởi tạo luồng xử lý đa nhân...")

    async with WEB_RENDER_SEM:
        tts_input_items = []
        if payload.post and payload.post.content.strip():
            tts_input_items.append({
                "id": f"p_{payload.post.id}_{uuid.uuid4().hex[:4]}",
                "text": payload.post.content,
                "speaker": payload.post.speaker or payload.post.username,
                "align": payload.post.align or "left",
                "raw_item": payload.post,
                "is_op": True,
            })
        for c in selected_comments:
            tts_input_items.append({
                "id": f"c_{c.id}_{uuid.uuid4().hex[:4]}",
                "text": c.content,
                "speaker": c.speaker or c.username,
                "align": c.align or "left",
                "raw_item": c,
                "is_op": False,
            })

        _emit(6, f"1/3 Đang tạo giọng đọc AI ({len(tts_input_items)} cảnh)...")

        def _tts_cb(done: int, total: int) -> None:
            _emit(6 + int((done / max(1, total)) * 26), f"1/3 Đang tạo giọng đọc AI ({done}/{total})...")

        timeline_items = await tts_engine.batch_synthesize(
            items=tts_input_items,
            voice=payload.voice,
            rate=payload.speech_rate,
            progress_callback=_tts_cb,
        )

        ordered_card_paths: List[Optional[str]] = [None] * len(timeline_items)
        card_specs = []
        card_spec_positions: List[int] = []
        for idx_t, item_meta in enumerate(timeline_items):
            matched = next(x for x in tts_input_items if x["id"] == item_meta["id"])
            raw = matched["raw_item"]
            is_post_item = matched["is_op"]

            if raw.card_image_path and os.path.exists(raw.card_image_path):
                ordered_card_paths[idx_t] = raw.card_image_path
                item_meta["is_chat_strip"] = bool(raw.is_chat_strip or True)
            else:
                card_spec_positions.append(idx_t)
                card_specs.append({
                    "item_id": item_meta["id"],
                    "platform": payload.platform,
                    "username": raw.username,
                    "content": raw.content,
                    "likes": raw.likes,
                    "replies": raw.replies,
                    "reposts": raw.reposts,
                    "shares": raw.shares,
                    "is_op": is_post_item,
                    "dark_mode": payload.dark_mode,
                    "avatar_url": raw.avatar_url,
                    "image_url": raw.image_url,
                })

            if raw.meme_reaction and raw.meme_reaction != "none":
                m_path = ASSETS_DIR / "memes" / raw.meme_reaction
                if m_path.exists():
                    item_meta["meme_image_path"] = str(m_path)

            if raw.sfx_reaction:
                s_path = ASSETS_DIR / "sfx" / raw.sfx_reaction
                if s_path.exists():
                    item_meta["sfx_path"] = str(s_path)

        if card_specs:
            _emit(34, f"2/3 Đang chụp Cap bình luận chuẩn Threads (0/{len(card_specs)})...")

            def _card_cb(done: int, total: int) -> None:
                _emit(34 + int((done / max(1, total)) * 20), f"2/3 Đang chụp Cap bình luận ({done}/{total})...")

            rendered_list = await asyncio.to_thread(
                renderer.render_cards_parallel, card_specs, _card_cb
            )
            for pos, r_path in zip(card_spec_positions, rendered_list):
                ordered_card_paths[pos] = r_path
        else:
            _emit(52, f"2/3 Đã sẵn sàng {len(ordered_card_paths)} ảnh Cap tin nhắn theo đúng thứ tự!")

        card_image_paths: List[str] = [str(p) for p in ordered_card_paths if p]

        _emit(55, "3/3 Đang ghép Video MP4 9:16 + Nhạc nền + SFX...")

        mc_bg = ASSETS_DIR / "backgrounds" / "minecraft_parkour.mp4"
        bg_path = str(mc_bg) if mc_bg.exists() else default_assets["background"]
        if payload.background_file:
            custom_bg = ASSETS_DIR / "backgrounds" / payload.background_file
            if custom_bg.exists():
                bg_path = str(custom_bg)

        sfx_path = default_assets["pop"]

        bgm_path = None
        if payload.bgm_file and payload.bgm_file != "none":
            custom_bgm = ASSETS_DIR / "bgm" / payload.bgm_file
            bgm_path = str(custom_bgm) if custom_bgm.exists() else default_assets["bgm"]

        badge_path = None
        if payload.channel_badge:
            custom_badge = ASSETS_DIR / "badges" / "drama_badge.png"
            if custom_badge.exists():
                badge_path = str(custom_badge)

        def _ffmpeg_cb(ratio: float) -> None:
            pct = 55 + int(min(1.0, max(0.0, ratio)) * 44)
            _emit(pct, f"3/3 Đang xuất Video MP4 ({int(ratio * 100)}% FFmpeg)...")

        video_filename = f"video_{uuid.uuid4().hex[:8]}.mp4"
        final_video_path = await asyncio.to_thread(
            compositor.compose_video,
            timeline_items=timeline_items,
            card_image_paths=card_image_paths,
            background_video_path=bg_path,
            bgm_path=bgm_path,
            default_sfx_path=sfx_path,
            channel_badge_path=badge_path,
            output_filename=video_filename,
            bgm_volume=payload.bgm_volume,
            sfx_volume=payload.sfx_volume,
            running_text=payload.running_text,
            running_text_mode=payload.running_text_mode,
            progress_callback=_ffmpeg_cb,
        )

    _emit(100, "Hoàn tất 100%!")
    rel_video_path = Path(final_video_path).relative_to(BASE_DIR).as_posix()
    return {
        "status": "success",
        "video_url": f"/{rel_video_path}",
        "filename": video_filename,
        "total_duration": timeline_items[-1]["end_time"],
        "items_count": len(timeline_items),
    }


@app.post("/api/render-jobs")
async def start_render_job(payload: RenderRequest):
    """Starts an asynchronous multi-threaded render job and returns job_id for live % polling."""
    selected_comments = [c for c in payload.comments if c.selected]
    if not selected_comments and not payload.post:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất bài đăng hoặc 1 bình luận.")

    job_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    RENDER_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "percent": 1,
        "stage": "Đang khởi tạo tiến trình Render...",
        "started_at": t0,
        "elapsed_sec": 0.0,
        "result": None,
        "error": None,
    }

    def _update(pct: int, stage: str) -> None:
        job = RENDER_JOBS.get(job_id)
        if job:
            job["percent"] = pct
            job["stage"] = stage
            job["elapsed_sec"] = round(time.monotonic() - t0, 1)

    async def _worker() -> None:
        try:
            res = await _run_render_pipeline(payload, on_progress=_update)
            job = RENDER_JOBS[job_id]
            job["status"] = "completed"
            job["percent"] = 100
            job["stage"] = "Hoàn tất 100%!"
            job["elapsed_sec"] = round(time.monotonic() - t0, 1)
            job["result"] = res
        except Exception as exc:
            job = RENDER_JOBS[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
            job["elapsed_sec"] = round(time.monotonic() - t0, 1)

    asyncio.create_task(_worker())
    return {"job_id": job_id, "status": "running"}


@app.get("/api/render-jobs/{job_id}")
def get_render_job_status(job_id: str):
    job = RENDER_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình render.")
    if job["status"] == "running":
        job["elapsed_sec"] = round(time.monotonic() - job["started_at"], 1)
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "percent": job["percent"],
        "stage": job["stage"],
        "elapsed_sec": job["elapsed_sec"],
        "result": job["result"],
        "error": job["error"],
    }


@app.post("/api/render-video")
async def render_full_video(payload: RenderRequest):
    """Direct synchronous-compatible render endpoint backed by the fast multi-threaded pipeline."""
    try:
        return await _run_render_pipeline(payload)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/upload-background")
async def upload_background_video(file: UploadFile = File(...)):
    """Allows uploading custom background clips (gameplay, ASMR, chill videos)."""
    bg_dir = ASSETS_DIR / "backgrounds"
    clean_name = re.sub(r"[^\w\-_.]", "_", file.filename)
    dest_path = bg_dir / clean_name
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"filename": clean_name, "status": "uploaded"}

# ------------------------------------------------------------------
# Telegram Bot & Admin (@citywin2026) Key Management APIs
# ------------------------------------------------------------------
class BotTokenRequest(BaseModel):
    token: str

class CreateKeyRequest(BaseModel):
    note: str = ""
    count: int = 1
    custom_key: Optional[str] = None

@app.on_event("startup")
async def startup_event():
    """Automatically starts Telegram Bot polling if a Bot Token is configured."""
    saved_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or key_manager.get_config("telegram_bot_token", "")
    # Ensure at least one initial demo key exists if DB is brand new
    if not key_manager.list_keys():
        key_manager.create_key(note="Key mặc định đầu tiên", created_by=f"@{ADMIN_USERNAME}")
    if saved_token:
        await tg_bot_service.start_polling(saved_token)

@app.on_event("shutdown")
async def shutdown_event():
    await tg_bot_service.stop_polling()
    renderer.close()

@app.get("/api/bot/status")
def get_bot_status():
    saved_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or key_manager.get_config("telegram_bot_token", "")
    masked_token = (saved_token[:10] + "..." + saved_token[-5:]) if len(saved_token) > 16 else ""
    return {
        "is_running": tg_bot_service.is_running,
        "bot_username": tg_bot_service.bot_username,
        "admin_username": f"@{ADMIN_USERNAME}",
        "has_token": bool(saved_token),
        "masked_token": masked_token,
        "keys": key_manager.list_keys(),
    }

@app.post("/api/bot/start")
async def api_start_bot(req: BotTokenRequest):
    ok, msg = await tg_bot_service.start_polling(req.token)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {
        "status": "running",
        "message": msg,
        "bot_username": tg_bot_service.bot_username,
    }

@app.post("/api/bot/stop")
async def api_stop_bot():
    await tg_bot_service.stop_polling()
    return {"status": "stopped", "message": "Đã dừng Telegram Bot."}

@app.post("/api/bot/keys")
def api_create_keys(req: CreateKeyRequest):
    count = max(1, min(req.count, 50))
    created = []
    for i in range(count):
        c_key = req.custom_key if (count == 1 and req.custom_key) else None
        created.append(
            key_manager.create_key(
                note=req.note,
                created_by=f"@{ADMIN_USERNAME}",
                custom_key=c_key,
            )
        )
    return {"created": created, "keys": key_manager.list_keys()}

@app.delete("/api/bot/keys/{key_code}")
async def api_delete_key(key_code: str):
    deleted, info = key_manager.delete_key(key_code)
    if not deleted or not info:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã Key này.")
    bound_uid = info.get("bound_user_id")
    if bound_uid and int(bound_uid) in tg_bot_service.user_sessions:
        del tg_bot_service.user_sessions[int(bound_uid)]
    if bound_uid and tg_bot_service.is_running and tg_bot_service.app:
        try:
            await tg_bot_service.app.bot.send_message(
                chat_id=int(bound_uid),
                text=(
                    f"🚫 <b>THÔNG BÁO THU HỒI KEY</b>\n\n"
                    f"Mã Key <code>{key_code}</code> của bạn vừa bị Admin (@{ADMIN_USERNAME}) xóa.\n"
                    f"Quyền sử dụng bot đã bị vô hiệu hóa ngay lập tức."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass
    return {"status": "deleted", "key_code": key_code, "keys": key_manager.list_keys()}

if __name__ == "__main__":
    import uvicorn
    print("Starting Social Video Automation Studio on http://localhost:8000 ...")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
