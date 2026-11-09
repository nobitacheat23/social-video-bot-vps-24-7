"""
Telegram Bot for Social Video Automation Studio (Shorts/Reels/TikTok).
Provides 100% feature parity with the Web Studio:
- Paste link (Threads, TikTok, Facebook) -> Scrape -> Interactive Comment/Sticker/SFX Selector -> Render & Send MP4
- Full Settings Menu: Voice (Hoài My / Nam Minh), Speech Rate, Card Template, Light/Dark Mode,
  Background Video (select or upload MP4), BGM Music, Floating Watermark, Channel Badge
- Strict 1-Key-1-User License System managed by Admin @citywin2026
- Real-time Key Revocation: Deleting a key immediately blocks the user from all bot interactions
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple, Union
import httpx

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    ContextTypes,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.key_manager import KeyManager, ADMIN_USERNAME
from app.scraper import SocialScraper, CommentFilter, detect_platform
from app.renderer import CardRenderer
from app.tts_engine import TTSEngine, VOICE_CATALOG, get_voice_label
from app.compositor import VideoCompositor
from app.meme_engine import MemeEngine
from app.image_chat_engine import ImageChatEngine, is_conversational_chat_message

logger = logging.getLogger("TelegramVideoBot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

NAME_COLOR_PALETTE: Dict[str, Tuple[str, str]] = {
    "red": ("🔴 Đỏ Coral", "#E11D48"),
    "purple": ("🟣 Tím Lavender", "#7C3AED"),
    "blue": ("🔵 Xanh Dương", "#2563EB"),
    "green": ("🟢 Xanh Lục", "#059669"),
    "orange": ("🟠 Cam Cháy", "#EA580C"),
    "amber": ("🟡 Vàng Nghệ", "#D97706"),
    "pink": ("💖 Hồng Baby", "#EC4899"),
    "dark": ("⚫ Đen / Than", "#1F2937"),
    "cyan": ("⚪ Xanh Biển", "#0284C7"),
}


def h(val: Any) -> str:
    """Safely escapes any dynamic text for Telegram HTML parse_mode."""
    return html.escape(str(val or ""))


class TelegramVideoBotService:
    """
    Encapsulates the Telegram Bot instance, state management, rendering pipeline,
    and real-time license enforcement.
    """

    def __init__(
        self,
        key_manager: KeyManager,
        scraper: SocialScraper,
        renderer: CardRenderer,
        tts_engine: TTSEngine,
        compositor: VideoCompositor,
        meme_engine: MemeEngine,
        default_assets: Dict[str, str],
        image_chat_engine: Optional[ImageChatEngine] = None,
    ):
        self.key_manager = key_manager
        self.scraper = scraper
        self.renderer = renderer
        self.tts_engine = tts_engine
        self.compositor = compositor
        self.meme_engine = meme_engine
        self.default_assets = default_assets
        self.image_chat_engine = image_chat_engine or ImageChatEngine(
            output_dir=str(OUTPUT_DIR / "cards")
        )

        self.user_sessions: Dict[int, Dict[str, Any]] = {}
        self._chat_message_history: Dict[int, Set[int]] = {}
        self._pending_photos: Dict[int, List[Tuple[int, Any, Any]]] = {}
        self._photo_last_ts: Dict[int, float] = {}
        self._photo_first_ts: Dict[int, float] = {}
        self._photo_flush_tasks: Dict[int, asyncio.Task] = {}
        self._user_photo_locks: Dict[int, asyncio.Lock] = {}
        self.render_semaphore = asyncio.Semaphore(3)
        self.bg_tasks: Set[asyncio.Task] = set()
        self.app: Optional[Application] = None
        self.is_running: bool = False
        self.bot_username: str = ""
        self._bot_token: str = ""

    def _get_photo_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_photo_locks:
            self._user_photo_locks[user_id] = asyncio.Lock()
        return self._user_photo_locks[user_id]

    def _spawn_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)

    def _record_msg_id(self, chat_id: Optional[int], message_id: Optional[int]) -> None:
        if not chat_id or not message_id:
            return
        if chat_id not in self._chat_message_history:
            self._chat_message_history[chat_id] = set()
        self._chat_message_history[chat_id].add(message_id)
        if len(self._chat_message_history[chat_id]) > 2000:
            min_ids = sorted(self._chat_message_history[chat_id])[:-1500]
            for mid in min_ids:
                self._chat_message_history[chat_id].discard(mid)

    async def _track_update_messages(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            if update.effective_chat and update.effective_message:
                self._record_msg_id(
                    update.effective_chat.id, update.effective_message.message_id
                )
            if update.effective_user and update.effective_user.id in self.user_sessions:
                self.user_sessions[update.effective_user.id]["last_activity"] = time.time()
        except Exception:
            pass

    async def _periodic_cleanup_loop(self) -> None:
        """
        Background daemon loop to prevent VPS disk and memory exhaustion:
        - Runs every 600s (10 minutes)
        - Prunes old rendered videos (> 1 hour old in output/ and output/videos)
        - Prunes old cards, uploads, tts (> 2 hours old in output/cards, output/uploads, output/tts)
        - Prunes inactive user sessions (> 6 hours without activity)
        - Invokes garbage collector gc.collect() to keep RAM usage lean (< 250MB)
        """
        while self.is_running:
            try:
                await asyncio.sleep(600)
                now = time.time()
                clean_dirs = [
                    (OUTPUT_DIR / "cards", 7200),
                    (OUTPUT_DIR / "uploads", 7200),
                    (OUTPUT_DIR / "tts", 7200),
                    (OUTPUT_DIR / "videos", 3600),
                    (OUTPUT_DIR, 3600),
                ]
                deleted_files = 0
                for c_dir, max_age in clean_dirs:
                    if c_dir.exists():
                        for p in c_dir.glob("*"):
                            if p.is_file() and not p.name.startswith("."):
                                try:
                                    if now - p.stat().st_mtime > max_age:
                                        p.unlink(missing_ok=True)
                                        deleted_files += 1
                                except Exception:
                                    pass

                inactive_users = []
                for uid, s in list(self.user_sessions.items()):
                    last_act = s.get("last_activity") or s.get("created_at") or 0
                    if isinstance(last_act, (int, float)) and now - last_act > 21600:
                        inactive_users.append(uid)
                for uid in inactive_users:
                    self.user_sessions.pop(uid, None)

                import gc
                gc.collect()

                if deleted_files > 0:
                    logger.info(f"Periodic cleanup pruned {deleted_files} files, {len(inactive_users)} inactive sessions.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in periodic cleanup loop: {e}")

    async def purge_chat_messages(
        self, bot: Any, chat_id: int, latest_msg_id: Optional[int] = None, max_scan: int = 600
    ) -> int:
        """
        Deletes all bot and user messages in the specified chat.
        Combines recorded message IDs with a backward scan from latest_msg_id.
        Uses bulk delete_messages (chunks of 100) with fallback to single delete.
        Returns the number of messages successfully deleted.
        """
        tracked = set(self._chat_message_history.get(chat_id, set()))
        if not latest_msg_id:
            try:
                probe = await bot.send_message(
                    chat_id=chat_id,
                    text="🧹 <i>Dọn dẹp tin nhắn...</i>",
                    parse_mode=ParseMode.HTML,
                )
                latest_msg_id = probe.message_id
                tracked.add(latest_msg_id)
            except Exception:
                if tracked:
                    latest_msg_id = max(tracked)

        if latest_msg_id:
            tracked.add(latest_msg_id)
            start_id = max(1, latest_msg_id - max_scan)
            for mid in range(start_id, latest_msg_id + 1):
                tracked.add(mid)

        if not tracked:
            return 0

        all_ids = sorted(list(tracked), reverse=True)
        deleted_count = 0
        chunk_size = 100

        for i in range(0, len(all_ids), chunk_size):
            chunk = all_ids[i : i + chunk_size]
            try:
                await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
                deleted_count += len(chunk)
            except Exception:
                for mid in chunk:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=mid)
                        deleted_count += 1
                    except Exception:
                        pass
            await asyncio.sleep(0.08)

        if chat_id in self._chat_message_history:
            self._chat_message_history[chat_id].clear()

        return deleted_count

    @staticmethod
    def _make_progress_bar(pct: int, elapsed: float, detail: str) -> str:
        pct = max(0, min(100, int(pct)))
        filled = round(pct / 100 * 14)
        bar = "█" * filled + "░" * (14 - filled)
        return (
            "🎬 <b>TIẾN TRÌNH TẠO VIDEO ĐA LUỒNG (9:16)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Trạng thái:</b> {h(detail)}\n\n"
            f"📊 <b>Tiến độ:</b> <code>[{bar}] {pct}%</code>\n"
            f"⏱ <b>Thời gian chạy:</b> <code>{elapsed:.1f}s</code> <i>(Mục tiêu: 30s - 2p)</i>"
        )

    async def _safe_edit(
        self, message: Any, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> None:
        try:
            if hasattr(message, "chat_id") and hasattr(message, "message_id"):
                self._record_msg_id(message.chat_id, message.message_id)
            await message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            logger.warning(f"Telegram edit_text warning: {e}")
        except Exception as e:
            logger.warning(f"Transient Telegram edit error ignored: {e}")

    # ------------------------------------------------------------------
    # Access Control & Real-Time Key Verification
    # ------------------------------------------------------------------
    async def check_access(self, update: Update) -> bool:
        """
        Real-time guard executed on every incoming message or button click.
        If a user's key was deleted by @citywin2026, immediately revokes session and blocks.
        """
        user = update.effective_user
        if not user:
            return False

        allowed, _ = self.key_manager.verify_user_access(user.id, user.username)
        if allowed:
            return True

        if user.id in self.user_sessions:
            del self.user_sessions[user.id]

        deny_msg = (
            "🔒 <b>YÊU CẦU KÍCH HOẠT KEY BẢN QUYỀN</b>\n\n"
            "Tài khoản của bạn hiện chưa có Key hoặc Key đã bị Admin vô hiệu hóa/xóa.\n\n"
            "🔑 <b>Cách kích hoạt:</b>\n"
            "• Nhập lệnh: <code>/key MÃ_KEY</code> (Ví dụ: <code>/key CW26-ABCD-1234</code>)\n"
            "• Hoặc dán trực tiếp mã Key vào khung chat này.\n\n"
            f"📩 Liên hệ Admin: @{h(ADMIN_USERNAME)} để được cấp Key (1 Key = 1 Người dùng duy nhất)."
        )

        if update.callback_query:
            await update.callback_query.answer(
                "🚫 Key của bạn không hợp lệ hoặc đã bị Admin xóa!", show_alert=True
            )
            try:
                await update.callback_query.message.reply_text(
                    deny_msg, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(deny_msg, parse_mode=ParseMode.HTML)

        return False

    # ------------------------------------------------------------------
    # Main Reply Keyboard
    # ------------------------------------------------------------------
    def get_main_reply_keyboard(
        self, user_id: int, username: Optional[str]
    ) -> ReplyKeyboardMarkup:
        rows = [
            [
                KeyboardButton("📸 Đọc Ảnh Tin Nhắn"),
                KeyboardButton("🔥 Mẫu Demo Threads Viral"),
            ],
            [
                KeyboardButton("🎬 Phiên làm việc hiện tại"),
                KeyboardButton("🎙 Chọn Giọng Đọc"),
            ],
            [
                KeyboardButton("⚙️ Cài đặt Video"),
                KeyboardButton("🖼 Chọn Video Nền"),
            ],
            [
                KeyboardButton("🔑 Thông tin Key"),
            ],
        ]
        if self.key_manager.is_admin(user_id, username):
            rows[-1].append(KeyboardButton(f"👑 Quản Trị Admin (@{ADMIN_USERNAME})"))
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)

    # ------------------------------------------------------------------
    # Preset & Session Helpers
    # ------------------------------------------------------------------
    def init_image_chat_session(self, user_id: int) -> Dict[str, Any]:
        session = {
            "mode": "image_chat",
            "url": "image_chat_upload",
            "platform": "chat",
            "post": None,
            "comments": [],
            "images_count": 0,
            "rendered_completed": False,
            "awaiting_input": None,
            "created_at": time.time(),
            "last_activity": time.time(),
            "user_a_name": "Người dùng A",
            "user_b_name": "Người dùng B",
            "user_a_color": "red",
            "user_b_color": "purple",
            "user_a_voice": "vclip-adam-beta-3",
            "user_b_voice": "vclip-adam-beta-2",
            "user_a_avatar": None,
            "user_b_avatar": None,
        }
        self.user_sessions[user_id] = session
        return session

    def format_script_studio_message(
        self, user_id: int, username: str = ""
    ) -> Tuple[str, InlineKeyboardMarkup]:
        session = self.user_sessions.get(user_id)
        if not session:
            session = self.init_image_chat_session(user_id)

        name_a = session.get("user_a_name") or "Người dùng A"
        name_b = session.get("user_b_name") or "Người dùng B"
        col_a_k = session.get("user_a_color") or "red"
        col_b_k = session.get("user_b_color") or "purple"
        col_a_lbl = NAME_COLOR_PALETTE.get(col_a_k, ("🔴 Đỏ Coral", "#E11D48"))[0]
        col_b_lbl = NAME_COLOR_PALETTE.get(col_b_k, ("🟣 Tím Lavender", "#7C3AED"))[0]
        voice_a = session.get("user_a_voice") or "vclip-adam-beta-3"
        voice_b = session.get("user_b_voice") or "vclip-adam-beta-2"
        voice_a_lbl = get_voice_label(voice_a)
        voice_b_lbl = get_voice_label(voice_b)
        av_a = "✅ Đã tải ảnh riêng" if session.get("user_a_avatar") else "⚪ Mặc định (Avatar chữ A)"
        av_b = "✅ Đã tải ảnh riêng" if session.get("user_b_avatar") else "⚪ Mặc định (Avatar chữ B)"

        lines = [
            "🎭 <b>STUDIO KỊCH BẢN HỘI THOẠI (CHAT MAKER 2 VAI)</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            "👥 <b>THIẾT LẬP 2 NHÂN VẬT HỘI THOẠI:</b>",
            "",
            f"🔴 <b>Nhân Vật A (Bên Trái):</b>",
            f"• Tên hiển thị: <b>{h(name_a)}</b>",
            f"• Màu tên: <b>{col_a_lbl}</b>",
            f"• Giọng đọc: <b>{h(voice_a_lbl)}</b>",
            f"• Ảnh đại diện: <i>{av_a}</i>",
            "",
            f"🟣 <b>Nhân Vật B (Bên Phải):</b>",
            f"• Tên hiển thị: <b>{h(name_b)}</b>",
            f"• Màu tên: <b>{col_b_lbl}</b>",
            f"• Giọng đọc: <b>{h(voice_b_lbl)}</b>",
            f"• Ảnh đại diện: <i>{av_b}</i>",
            "",
            "🖼 <b>Khung hiển thị:</b> Bo tròn viền mượt, Avatar tròn & Tên đổi màu nổi bật chuẩn TikTok!",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <b>Cú pháp nhập kịch bản:</b>",
            f"• <code>A: Lời thoại của {h(name_a)}</code>",
            f"• <code>B: Lời thoại của {h(name_b)}</code>",
            "• Hoặc chỉ cần dán các câu chat (Bot tự động đảo lượt xen kẽ A - B)!",
        ]

        kb = [
            [
                InlineKeyboardButton(f"✏️ Tên A", callback_data="ws:set_name_a"),
                InlineKeyboardButton(f"🎨 Màu A", callback_data="ws:set_color_a"),
                InlineKeyboardButton(f"🎙 Giọng A", callback_data="ws:set_voice_a"),
                InlineKeyboardButton("📸 Avatar A", callback_data="ws:upload_av_a"),
            ],
            [
                InlineKeyboardButton(f"✏️ Tên B", callback_data="ws:set_name_b"),
                InlineKeyboardButton(f"🎨 Màu B", callback_data="ws:set_color_b"),
                InlineKeyboardButton(f"🎙 Giọng B", callback_data="ws:set_voice_b"),
                InlineKeyboardButton("📸 Avatar B", callback_data="ws:upload_av_b"),
            ],
            [
                InlineKeyboardButton("✍️ Bắt Đầu Nhập / Dán Kịch Bản", callback_data="ws:input_script_now"),
            ],
            [
                InlineKeyboardButton("🔙 Quay Lại Bảng Điều Khiển", callback_data="ws:show"),
            ],
        ]
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    def format_script_color_menu(
        self, user_id: int, target: str = "a"
    ) -> Tuple[str, InlineKeyboardMarkup]:
        session = self.user_sessions.get(user_id) or self.init_image_chat_session(user_id)
        name = session.get(f"user_{target}_name") or ("Người dùng A" if target == "a" else "Người dùng B")
        cur_col = session.get(f"user_{target}_color") or ("red" if target == "a" else "purple")
        cur_lbl = NAME_COLOR_PALETTE.get(cur_col, ("🔴 Đỏ", ""))[0]

        lines = [
            f"🎨 <b>CHỌN MÀU TÊN CHO NHÂN VẬT {target.upper()} ({h(name)})</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Màu đang dùng: <b>{cur_lbl}</b>",
            "",
            "👉 Bấm vào màu bạn muốn chọn để áp dụng ngay vào thẻ tin nhắn:",
        ]

        kb = []
        row = []
        for col_k, (col_name, _) in NAME_COLOR_PALETTE.items():
            mark = "✅ " if col_k == cur_col else ""
            row.append(
                InlineKeyboardButton(f"{mark}{col_name}", callback_data=f"set_col_{target}:{col_k}")
            )
            if len(row) == 3:
                kb.append(row)
                row = []
        if row:
            kb.append(row)

        kb.append([InlineKeyboardButton("🔙 Quay Lại Studio Kịch Bản", callback_data="ws:manual_script_menu")])
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    def format_script_voice_menu(
        self, user_id: int, target: str = "a"
    ) -> Tuple[str, InlineKeyboardMarkup]:
        session = self.user_sessions.get(user_id) or self.init_image_chat_session(user_id)
        name = session.get(f"user_{target}_name") or ("Người dùng A" if target == "a" else "Người dùng B")
        default_v = "vclip-adam-beta-3" if target == "a" else "vclip-adam-beta-2"
        cur_v = session.get(f"user_{target}_voice") or default_v
        cur_lbl = get_voice_label(cur_v)

        lines = [
            f"🎙 <b>CHỌN GIỌNG ĐỌC CHO NHÂN VẬT {target.upper()} ({h(name)})</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Giọng hiện tại: <b>{h(cur_lbl)}</b>",
            "",
            "👉 Bấm vào giọng bạn muốn gán cho nhân vật này:",
        ]

        kb = []
        for v in VOICE_CATALOG:
            mark = "✅ " if v["id"] == cur_v else ""
            kb.append(
                [InlineKeyboardButton(f"{mark}{v['name']}", callback_data=f"set_spk_v_{target}:{v['id']}")]
            )
        kb.append([InlineKeyboardButton("🔙 Quay Lại Studio Kịch Bản", callback_data="ws:manual_script_menu")])
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    def format_script_review_message(
        self, user_id: int, username: str = ""
    ) -> Tuple[str, InlineKeyboardMarkup]:
        session = self.user_sessions.get(user_id)
        if not session or not session.get("comments"):
            return self.format_workspace_message(user_id, username)

        comments = session["comments"]
        c_len = len(comments)

        lines = [
            f"🎬 <b>KỊCH BẢN ĐÃ TẠO XONG ({c_len} DÒNG TIN NHẮN)</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <i>Bạn có thể gắn ảnh Meme hoặc đổi tiếng Sound hiệu ứng cho từng dòng bên dưới trước khi tạo video:</i>",
            "",
        ]

        display_limit = 12
        for idx, c in enumerate(comments[:display_limit]):
            spk = c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
            has_m = "🖼️ [Có Meme]" if (c.get("custom_meme_path") or c.get("meme_image_path")) else "⚪"
            cur_sfx = c.get("sfx_reaction", "pop.wav")
            sfx_name = (
                self.meme_engine.get_sfx_name(cur_sfx)
                if hasattr(self.meme_engine, "get_sfx_name")
                else cur_sfx
            )
            sfx_str = "🔇" if cur_sfx == "none" else f"{sfx_name.split(' (')[0]}"
            snip = c.get("content", "").replace("\n", " ")
            if len(snip) > 45:
                snip = snip[:42] + "..."
            lines.append(
                f"<b>#{idx + 1} [{h(spk)}]</b> | {has_m} | <code>{h(sfx_str)}</code>\n"
                f"   └ <i>{h(snip)}</i>"
            )

        if c_len > display_limit:
            lines.append(f"<i>... và {c_len - display_limit} dòng tin nhắn tiếp theo.</i>")

        lines.extend([
            "",
            "👇 <b>HÃY CHỌN BƯỚC TIẾP THEO:</b>",
        ])

        kb = [
            [
                InlineKeyboardButton("🖼️ Thêm / Đổi Ảnh Meme Cho Dòng", callback_data="ws:attach_meme_menu"),
                InlineKeyboardButton("🔊 Đổi Hiệu Ứng Sound Meme", callback_data="ws:sfx_menu"),
            ],
            [
                InlineKeyboardButton("🎨 Đổi Tên & Màu 2 Nhân Vật", callback_data="ws:manual_script_menu"),
                InlineKeyboardButton("📝 Sửa Lại Lời Thoại", callback_data="ws:batch_text_menu"),
            ],
            [
                InlineKeyboardButton("🚀 BẮT ĐẦU TẠO VIDEO (9:16)", callback_data="ws:render_now"),
            ],
            [
                InlineKeyboardButton("⚙️ Bảng Điều Khiển Chi Tiết", callback_data="ws:show"),
            ],
        ]
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    def _re_render_script_comments(self, user_id: int) -> None:
        session = self.user_sessions.get(user_id)
        if not session or not session.get("comments"):
            return
        name_a = session.get("user_a_name") or "Người dùng A"
        name_b = session.get("user_b_name") or "Người dùng B"
        col_a = session.get("user_a_color") or "red"
        col_b = session.get("user_b_color") or "purple"
        av_a = session.get("user_a_avatar")
        av_b = session.get("user_b_avatar")

        for idx, c in enumerate(session["comments"]):
            card_p = c.get("card_image_path", "")
            if "chat_manual_" in card_p or c.get("is_manual_script"):
                c["is_manual_script"] = True
                align = c.get("align", "left")
                speaker = name_a if align == "left" else name_b
                av_p = av_a if align == "left" else av_b
                spk_col = col_a if align == "left" else col_b
                s_slice = self.image_chat_engine.create_chat_strip_from_text(
                    text=c["content"],
                    speaker=speaker,
                    align=align,
                    start_order_idx=idx,
                    avatar_path=av_p,
                    speaker_color=spk_col,
                )
                c["speaker"] = speaker
                c["username"] = speaker
                c["handle"] = speaker
                c["card_image_path"] = s_slice.image_path

    def _parse_and_append_script_lines(
        self, session: Dict[str, Any], raw_lines: List[str]
    ) -> List[Any]:
        name_a = session.get("user_a_name") or "Người dùng A"
        name_b = session.get("user_b_name") or "Người dùng B"
        col_a = session.get("user_a_color") or "red"
        col_b = session.get("user_b_color") or "purple"
        av_a = session.get("user_a_avatar")
        av_b = session.get("user_b_avatar")

        new_slices = []
        cur_align = "left"

        for idx, line in enumerate(raw_lines):
            line = line.strip()
            if not line:
                continue

            align = cur_align
            speaker = name_a if align == "left" else name_b
            av_p = av_a if align == "left" else av_b
            clean_line = line

            m_ab = re.match(r"^([AB12])\s*[:\-\.]\s*(.*)$", line, re.IGNORECASE)
            m_side = re.match(
                r"^\[\s*(trái|phải|t|p|left|right)\s*\]\s*(.*)$",
                line,
                re.IGNORECASE,
            )
            if not m_side:
                m_side = re.match(
                    r"^(trái|phải|left|right)\s*:\s*(.*)$",
                    line,
                    re.IGNORECASE,
                )

            if m_ab:
                tag = m_ab.group(1).upper()
                clean_line = m_ab.group(2).strip()
                if tag in ("A", "1"):
                    align = "left"
                    speaker = name_a
                    av_p = av_a
                    cur_align = "right"
                else:
                    align = "right"
                    speaker = name_b
                    av_p = av_b
                    cur_align = "left"
            elif m_side:
                tag = m_side.group(1).lower()
                clean_line = m_side.group(2).strip()
                if tag in ("phải", "p", "right"):
                    align = "right"
                    speaker = name_b
                    av_p = av_b
                    cur_align = "left"
                else:
                    align = "left"
                    speaker = name_a
                    av_p = av_a
                    cur_align = "right"
            else:
                m_spk = re.match(
                    r"^([A-ZÀ-Ỹa-zà-ỹ0-9_\s]{2,20})\s*:\s*(.*)$", line
                )
                if m_spk and not is_conversational_chat_message(m_spk.group(1)):
                    spk_cand = m_spk.group(1).strip()
                    clean_line = m_spk.group(2).strip()
                    if spk_cand.lower() == name_b.lower():
                        align = "right"
                        speaker = name_b
                        av_p = av_b
                        cur_align = "left"
                    elif spk_cand.lower() == name_a.lower():
                        align = "left"
                        speaker = name_a
                        av_p = av_a
                        cur_align = "right"
                    else:
                        speaker = spk_cand
                        align = cur_align
                        av_p = av_a if align == "left" else av_b
                        cur_align = "right" if cur_align == "left" else "left"
                else:
                    align = cur_align
                    speaker = name_a if align == "left" else name_b
                    av_p = av_a if align == "left" else av_b
                    cur_align = "right" if cur_align == "left" else "left"

            start_idx = len(session["comments"]) + len(new_slices)
            spk_col = col_a if align == "left" else col_b
            s_slice = self.image_chat_engine.create_chat_strip_from_text(
                text=clean_line,
                speaker=speaker,
                align=align,
                start_order_idx=start_idx,
                avatar_path=av_p,
                speaker_color=spk_col,
            )
            new_slices.append(s_slice)

        for s in new_slices:
            spk_label = s.speaker or ("Phải" if s.align == "right" else "Trái")
            session["comments"].append(
                {
                    "id": f"chat_{s.slice_id}",
                    "username": spk_label,
                    "speaker": spk_label,
                    "handle": spk_label,
                    "avatar_url": None,
                    "image_url": None,
                    "align": s.align,
                    "content": s.spoken_text,
                    "likes": "0",
                    "replies": 0,
                    "reposts": "0",
                    "shares": "0",
                    "time_ago": "",
                    "card_image_path": s.image_path,
                    "meme_image_path": s.meme_image_path,
                    "custom_meme_path": None,
                    "selected": True,
                    "meme_reaction": "none",
                    "sfx_reaction": "pop.wav",
                    "is_manual_script": True,
                    "is_chat_strip": True,
                }
            )
        session["images_count"] = session.get("images_count", 0) + 1
        return new_slices

    def load_motvongthread_preset(self, user_id: int) -> Dict[str, Any]:
        sample = self.scraper.get_sample_post("threads")
        session = {
            "mode": "social",
            "url": sample.url,
            "platform": "threads",
            "post": {
                "id": sample.post_id,
                "username": sample.author,
                "handle": sample.author_handle,
                "avatar_url": sample.avatar_url,
                "image_url": sample.image_url,
                "content": sample.content,
                "likes": sample.likes,
                "replies": len(sample.comments),
                "reposts": sample.reposts,
                "shares": sample.shares,
                "time_ago": "",
                "is_op": True,
            },
            "comments": [
                {
                    "id": c.id,
                    "username": c.username,
                    "handle": c.handle or c.username,
                    "avatar_url": c.avatar_url,
                    "image_url": c.image_url,
                    "content": c.content,
                    "likes": c.likes,
                    "replies": c.replies,
                    "reposts": c.reposts,
                    "shares": c.shares,
                    "time_ago": "",
                    "selected": True,
                    "meme_reaction": "none",
                    "sfx_reaction": "pop.wav",
                }
                for c in sample.comments
            ],
            "awaiting_input": None,
        }
        self.user_sessions[user_id] = session
        return session

    def format_workspace_message(
        self, user_id: int, username: str = ""
    ) -> Tuple[str, InlineKeyboardMarkup]:
        session = self.user_sessions.get(user_id)
        if not session:
            session = self.load_motvongthread_preset(user_id)

        cfg = self.key_manager.get_user_settings(user_id, username)
        voice_label = get_voice_label(cfg["voice"])
        comments = session["comments"]
        selected_count = sum(1 for c in comments if c.get("selected", True))

        # --- IMAGE CHAT OCR & AUTO-CAP MODE ---
        if session.get("mode") == "image_chat":
            img_cnt = session.get("images_count", 0)
            lines = [
                "📸 <b>BẢNG ĐIỀU KHIỂN ĐỌC ẢNH TIN NHẮN (AUTO-CAP)</b>",
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            if not comments:
                lines.extend(
                    [
                        "📥 <b>Trạng thái:</b> <i>Đang chờ bạn gửi ảnh tin nhắn...</i>",
                        "",
                        "💡 <b>Cách dùng siêu tốc (Giống Video TikTok mẫu):</b>",
                        "• <b>Cách 1:</b> Gửi <b>1 hoặc nhiều ảnh đoạn chat dài</b> (Messenger, Zalo, Telegram, SMS...) — Bot tự động cắt (<code>cap</code>) từng dòng tin nhắn từ trên xuống dưới và chỉ đọc nội dung bong bóng chat!",
                        "• <b>Cách 2:</b> Gửi <b>nhiều ảnh từng câu lẻ</b> cùng lúc (Album) hoặc gửi lần lượt — Bot tự động ghép & đọc <b>đúng theo thứ tự ảnh bạn gửi</b>!",
                        "• <b>Cách 3:</b> Bấm <code>[✍️ Nhập Kịch Bản Bằng Chữ]</code> hoặc dán trực tiếp đoạn chat (mỗi dòng 1 câu, có thể ghi <code>[Trái]...</code> hoặc <code>[Phải]...</code>) — Tool tự động vẽ thẻ chat bo tròn đẹp mắt và đọc chuẩn 100%!",
                        "",
                        f"🎙 <b>Giọng hiện tại:</b> <code>{h(voice_label)}</code> ({h(cfg['speech_rate'])})",
                        f"🖼 <b>Video nền:</b> <code>{h(cfg['background_file'])}</code> | 🎵 <b>BGM:</b> <code>{h(cfg['bgm_file'])}</code>",
                    ]
                )
                kb_empty = [
                    [
                        InlineKeyboardButton(
                            "✍️ Nhập Kịch Bản Bằng Chữ (Không Cần Ảnh)",
                            callback_data="ws:manual_script_menu",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🎙 Giọng Adam AI",
                            callback_data="set_v_ws:tiktok-adam",
                        ),
                        InlineKeyboardButton(
                            "🤖 Giọng Chị Google",
                            callback_data="set_v_ws:google-chigoogle",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🔥 Giọng Nam TikTok BV075",
                            callback_data="set_v_ws:tiktok-BV075",
                        ),
                        InlineKeyboardButton(
                            "🎭 Đối Thoại Đa Vai",
                            callback_data="set_v_ws:dialogue-auto",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🎙 Tất Cả Giọng", callback_data="cfg:voice_menu"
                        ),
                        InlineKeyboardButton(
                            "⚙️ Cài đặt Video", callback_data="menu:settings"
                        ),
                    ],
                ]
                return "\n".join(lines), InlineKeyboardMarkup(kb_empty)

            lines.append(
                f"🖼 <b>Đã quét:</b> <code>{img_cnt} ảnh</code> ➡️ Tách thành <b>{selected_count}/{len(comments)} dòng tin nhắn</b> (theo đúng thứ tự từ trên xuống):"
            )
            lines.append("")

            display_limit = 18
            for idx, c in enumerate(comments[:display_limit]):
                chk = "✅" if c.get("selected", True) else "⬜"
                spk = c.get("speaker") or (
                    "Phải" if c.get("align") == "right" else "Trái"
                )
                has_custom_m = bool(c.get("custom_meme_path") or c.get("meme_image_path"))
                meme_tag = (
                    " | 🖼️ <code>[Meme Kèm Dưới]</code>"
                    if has_custom_m
                    else (
                        f" | 🎭 <code>{h(c['meme_reaction'])}</code>"
                        if c.get("meme_reaction") and c["meme_reaction"] != "none"
                        else ""
                    )
                )
                sfx_tag = f" | 🔊 <code>{h(c.get('sfx_reaction', 'pop.wav'))}</code>"
                snippet = c["content"].replace("\n", " ")
                if len(snippet) > 68:
                    snippet = snippet[:65] + "..."
                lines.append(
                    f"{chk} <b>#{idx + 1} [{h(spk)}]</b>{meme_tag}{sfx_tag}\n   └ <i>{h(snippet)}</i>"
                )
            if len(comments) > display_limit:
                lines.append(
                    f"<i>... và {len(comments) - display_limit} dòng tin nhắn tiếp theo.</i>"
                )

            lines.extend(
                [
                    "",
                    "⚙️ <b>Cấu hình xuất video hiện tại:</b>",
                    f"• 🎙 Giọng: <code>{h(voice_label)}</code> ({h(cfg['speech_rate'])})",
                    f"• 🖼 Nền: <code>{h(cfg['background_file'])}</code> | 🎵 BGM: <code>{h(cfg['bgm_file'])}</code>",
                    f"• 🏷 Watermark: <code>{h(cfg['running_text'])}</code> (<code>{h(cfg['running_text_mode'])}</code>)",
                    "💡 <i>Gửi thêm ảnh vào khung chat bất kỳ lúc nào để nối tiếp vào cuối danh sách!</i>",
                ]
            )

            kb_chat: List[List[InlineKeyboardButton]] = []
            toggle_row: List[InlineKeyboardButton] = []
            for idx, c in enumerate(comments[:24]):
                chk = "✅" if c.get("selected", True) else "⬜"
                toggle_row.append(
                    InlineKeyboardButton(f"{chk} #{idx + 1}", callback_data=f"tgl_c:{idx}")
                )
                if len(toggle_row) == 4:
                    kb_chat.append(toggle_row)
                    toggle_row = []
            if toggle_row:
                kb_chat.append(toggle_row)

            kb_chat.append(
                [
                    InlineKeyboardButton(
                        "🎙 Giọng Adam AI", callback_data="set_v_ws:tiktok-adam"
                    ),
                    InlineKeyboardButton(
                        "🤖 Giọng Chị Google", callback_data="set_v_ws:google-chigoogle"
                    ),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton(
                        "🔥 Giọng TikTok BV075", callback_data="set_v_ws:tiktok-BV075"
                    ),
                    InlineKeyboardButton(
                        "🎭 Đối Thoại Đa Vai", callback_data="set_v_ws:dialogue-auto"
                    ),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton(
                        "🎭 Kịch Bản 2 Vai (Set Tên & Avatar)", callback_data="ws:manual_script_menu"
                    ),
                    InlineKeyboardButton(
                        "📝 Sửa Lại Toàn Bộ Lời Thoại", callback_data="ws:batch_text_menu"
                    ),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton(
                        "✏️ Sửa Chữ Dòng", callback_data="ws:edit_line_menu"
                    ),
                    InlineKeyboardButton(
                        "🔊 Đổi SFX Âm Thanh", callback_data="ws:sfx_menu"
                    ),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton(
                        "🖼 Gắn / Đổi Meme Dòng", callback_data="ws:attach_meme_menu"
                    ),
                    InlineKeyboardButton(
                        "🗑 Làm bộ mới", callback_data="ws:clear_chat"
                    ),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton("🎙 Chọn Giọng", callback_data="cfg:voice_menu"),
                    InlineKeyboardButton("⚙️ Cài đặt Video", callback_data="menu:settings"),
                ]
            )
            kb_chat.append(
                [
                    InlineKeyboardButton(
                        f"🚀 RENDER VIDEO NGAY ({selected_count} tin nhắn)",
                        callback_data="ws:render_now",
                    )
                ]
            )
            return "\n".join(lines), InlineKeyboardMarkup(kb_chat)

        # --- STANDARD SOCIAL POST + COMMENTS MODE ---
        post = session["post"]
        theme_label = "🌙 Dark Mode" if cfg["dark_mode"] else "☀️ Light Mode (Chuẩn)"
        tpl_label = (
            session.get("platform", "threads").upper()
            if cfg["platform"] == "auto"
            else cfg["platform"].upper()
        )

        lines = [
            "🎬 <b>BẢNG ĐIỀU KHIỂN DỰ ÁN VIDEO</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>Tác giả bài đăng:</b> <code>{h(post['username'])}</code> (❤️ {h(post['likes'])})",
            f"📝 <b>Nội dung gốc:</b>\n<i>{h(post['content'][:220])}</i>",
            "",
            f"💬 <b>DANH SÁCH BÌNH LUẬN ({selected_count}/{len(comments)} đã chọn):</b>",
        ]

        for idx, c in enumerate(comments[:18]):
            chk = "✅" if c.get("selected", True) else "⬜"
            meme_tag = (
                f" | 🎭 <code>{h(c['meme_reaction'])}</code>"
                if c.get("meme_reaction") and c["meme_reaction"] != "none"
                else ""
            )
            sfx_tag = f" | 🔊 <code>{h(c.get('sfx_reaction', 'pop.wav'))}</code>"
            snippet = c["content"].replace("\n", " ")
            if len(snippet) > 65:
                snippet = snippet[:62] + "..."
            u_lbl = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
            u_likes = c.get("likes", "0")
            lines.append(
                f"{chk} <b>#{idx + 1} {h(u_lbl)}</b> (❤️ {h(u_likes)}){meme_tag}{sfx_tag}\n   └ <i>{h(snippet)}</i>"
            )
        if len(comments) > 18:
            lines.append(f"<i>... và {len(comments) - 18} bình luận tiếp theo.</i>")

        lines.extend(
            [
                "",
                "⚙️ <b>Cấu hình xuất video hiện tại:</b>",
                f"• 🎙 Giọng: <code>{h(voice_label)}</code> ({h(cfg['speech_rate'])}) | 🎨 Thẻ: <code>{h(tpl_label)}</code> ({h(theme_label)})",
                f"• 🖼 Nền: <code>{h(cfg['background_file'])}</code> | 🎵 BGM: <code>{h(cfg['bgm_file'])}</code>",
                f"• 🏷 Watermark: <code>{h(cfg['running_text'])}</code> (<code>{h(cfg['running_text_mode'])}</code>)",
            ]
        )

        kb: List[List[InlineKeyboardButton]] = []
        toggle_row = []
        for idx, c in enumerate(comments[:24]):
            chk = "✅" if c.get("selected", True) else "⬜"
            toggle_row.append(
                InlineKeyboardButton(f"{chk} #{idx + 1}", callback_data=f"tgl_c:{idx}")
            )
            if len(toggle_row) == 4:
                kb.append(toggle_row)
                toggle_row = []
        if toggle_row:
            kb.append(toggle_row)

        kb.append(
            [
                InlineKeyboardButton("☑️ Chọn tất cả", callback_data="ws:sel_all"),
                InlineKeyboardButton("🔲 Bỏ chọn hết", callback_data="ws:unsel_all"),
            ]
        )
        kb.append(
            [
                InlineKeyboardButton(
                    "🎭 Đổi Sticker & SFX", callback_data="ws:edit_meme_menu"
                ),
                InlineKeyboardButton("➕ Thêm Bình luận", callback_data="ws:add_comment"),
            ]
        )
        kb.append(
            [
                InlineKeyboardButton("✏️ Sửa bài đăng gốc", callback_data="ws:edit_post"),
                InlineKeyboardButton("⚙️ Cài đặt Video", callback_data="menu:settings"),
            ]
        )
        kb.append(
            [
                InlineKeyboardButton(
                    f"🚀 RENDER VIDEO NGAY ({selected_count} cmt)",
                    callback_data="ws:render_now",
                )
            ]
        )

        return "\n".join(lines), InlineKeyboardMarkup(kb)

    # ------------------------------------------------------------------
    # Settings Menu Builder
    # ------------------------------------------------------------------
    def build_settings_menu(
        self, user_id: int, username: str = ""
    ) -> Tuple[str, InlineKeyboardMarkup]:
        cfg = self.key_manager.get_user_settings(user_id, username)
        voice_name = get_voice_label(cfg["voice"])
        theme_str = "🌙 Dark Mode (Tối)" if cfg["dark_mode"] else "☀️ Light Mode (Sáng)"
        badge_str = "✅ Bật" if cfg["channel_badge"] else "❌ Tắt"
        auto_str = (
            "⚡ Tự động Render khi gửi link/ảnh"
            if cfg["auto_render"]
            else "🛠 Duyệt nội dung trước khi Render"
        )

        text = (
            "⚙️ <b>CÀI ĐẶT THÔNG SỐ VIDEO (Tương đương bản Web)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎙 <b>Giọng đọc TTS:</b> <code>{h(voice_name)}</code>\n"
            f"⚡ <b>Tốc độ đọc:</b> <code>{h(cfg['speech_rate'])}</code>\n"
            f"🎨 <b>Mẫu khung bình luận:</b> <code>{h(cfg['platform'].upper())}</code>\n"
            f"🌓 <b>Giao diện Sáng/Tối:</b> <code>{h(theme_str)}</code>\n"
            f"🎬 <b>Video nền:</b> <code>{h(cfg['background_file'])}</code>\n"
            f"🎵 <b>Nhạc nền (BGM):</b> <code>{h(cfg['bgm_file'])}</code>\n"
            f"🏷 <b>Chữ chạy (Watermark):</b> <code>{h(cfg['running_text'])}</code>\n"
            f"🔄 <b>Kiểu chữ chạy:</b> <code>{h(cfg['running_text_mode'])}</code>\n"
            f"🛡 <b>Huy hiệu góc phải:</b> <code>{h(badge_str)}</code>\n"
            f"🤖 <b>Chế độ xử lý link:</b> <code>{h(auto_str)}</code>\n\n"
            "👇 <b>Bấm vào từng mục bên dưới để thay đổi ngay lập tức:</b>"
        )

        kb = [
            [
                InlineKeyboardButton("🎙 Đổi Giọng Đọc", callback_data="cfg:voice_menu"),
                InlineKeyboardButton("🔊 Nghe thử Giọng", callback_data="cfg:test_tts"),
            ],
            [
                InlineKeyboardButton(
                    f"⚡ Tốc độ ({cfg['speech_rate']})", callback_data="cfg:rate_menu"
                ),
                InlineKeyboardButton(
                    f"🎨 Mẫu Thẻ ({cfg['platform'].upper()})",
                    callback_data="cfg:tpl_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🌓 Chế độ: {theme_str}", callback_data="cfg:toggle_dark"
                ),
                InlineKeyboardButton(
                    f"🛡 Huy hiệu: {badge_str}", callback_data="cfg:toggle_badge"
                ),
            ],
            [
                InlineKeyboardButton("🎬 Chọn Video Nền", callback_data="cfg:bg_menu"),
                InlineKeyboardButton("🎵 Chọn Nhạc Nền (BGM)", callback_data="cfg:bgm_menu"),
            ],
            [
                InlineKeyboardButton(
                    "🏷 Đổi chữ Watermark", callback_data="cfg:set_watermark"
                ),
                InlineKeyboardButton(
                    "🔄 Kiểu chạy Watermark", callback_data="cfg:wm_mode_menu"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🤖 Chế độ: {auto_str}", callback_data="cfg:toggle_auto"
                )
            ],
            [InlineKeyboardButton("🔙 Quay lại Dự án Video", callback_data="ws:show")],
        ]
        return text, InlineKeyboardMarkup(kb)

    # ------------------------------------------------------------------
    # Admin Panel Builder (@citywin2026)
    # ------------------------------------------------------------------
    def build_admin_panel(self) -> Tuple[str, InlineKeyboardMarkup]:
        keys = self.key_manager.list_keys()
        total_keys = len(keys)
        used_keys = sum(1 for k in keys if k["bound_user_id"] is not None)
        free_keys = total_keys - used_keys

        lines = [
            f"👑 <b>TRUNG TÂM QUẢN TRỊ ADMIN (@{h(ADMIN_USERNAME)})</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>Tổng số Key:</b> <code>{total_keys}</code> | 🟢 <b>Chưa dùng:</b> <code>{free_keys}</code> | 👤 <b>Đã kích hoạt:</b> <code>{used_keys}</code>",
            "🔒 <b>Cơ chế bảo mật:</b> 1 Key chỉ dùng cho 1 tài khoản duy nhất. Khi xóa Key, người dùng bị chặn ngay lập tức!",
            "",
            "📋 <b>DANH SÁCH KEY HIỆN TẠI:</b>",
        ]

        kb: List[List[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton("➕ Tạo 1 Key Mới", callback_data="adm:gen1"),
                InlineKeyboardButton("➕ Tạo 5 Key Nhanh", callback_data="adm:gen5"),
            ]
        ]

        if not keys:
            lines.append("<i>(Chưa có Key nào. Hãy bấm Tạo Key Mới bên dưới)</i>")
        else:
            for idx, k in enumerate(keys[:15]):
                code = k["key_code"]
                if k["bound_user_id"]:
                    u_tag = (
                        f"@{k['bound_username']}"
                        if k["bound_username"]
                        else k["bound_full_name"] or "User"
                    )
                    status = f"👤 <code>{h(u_tag)}</code> (ID: <code>{k['bound_user_id']}</code>) | 🎬 {k['videos_created']} video"
                else:
                    status = "🟢 <i>Chưa kích hoạt</i>"
                note_part = f" [{h(k['note'])}]" if k.get("note") else ""
                lines.append(f"{idx + 1}. <code>{h(code)}</code>{note_part} — {status}")
                kb.append(
                    [
                        InlineKeyboardButton(
                            f"🗑 Xóa & Vô hiệu hóa: {code}",
                            callback_data=f"adm:del:{code}",
                        )
                    ]
                )

        kb.append(
            [
                InlineKeyboardButton("🧹 Xóa Sạch Chat Này", callback_data="adm:clean_self"),
                InlineKeyboardButton("👥 Xóa Chat Theo User", callback_data="adm:clean_user_menu"),
            ]
        )
        kb.append(
            [
                InlineKeyboardButton("💥 Xóa Chat Tất Cả Users", callback_data="adm:clean_all_confirm"),
                InlineKeyboardButton("🔄 Làm Mới Danh Sách", callback_data="adm:refresh"),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    def build_clean_user_menu(self) -> Tuple[str, InlineKeyboardMarkup]:
        users = self.key_manager.list_all_users()
        lines = [
            "👥 <b>QUẢN LÝ XÓA TIN NHẮN THEO USER</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Tổng số người dùng đã ghi nhận: <b>{len(users)}</b>",
            "",
            "👉 Chọn một người dùng bên dưới để xóa toàn bộ tin nhắn:",
        ]
        kb: List[List[InlineKeyboardButton]] = []
        if not users:
            lines.append("<i>(Chưa có người dùng nào trong cơ sở dữ liệu)</i>")
        else:
            for u in users[:12]:
                uid = u["user_id"]
                uname = f"@{u['username']}" if u["username"] else f"User {uid}"
                kb.append([
                    InlineKeyboardButton(
                        f"🗑 Xóa chat: {uname} (ID: {uid})",
                        callback_data=f"adm:clean_uid:{uid}",
                    )
                ])

        kb.append([
            InlineKeyboardButton("⌨️ Nhập User ID / Username Thủ Công", callback_data="adm:clean_input_uid")
        ])
        kb.append([
            InlineKeyboardButton("🔙 Quay Lại Admin Panel", callback_data="adm:refresh")
        ])
        return "\n".join(lines), InlineKeyboardMarkup(kb)

    # ------------------------------------------------------------------
    # Command Handlers
    # ------------------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        if context.args:
            potential_key = context.args[0].strip()
            if potential_key.upper().startswith("CW26-"):
                ok, msg = self.key_manager.activate_key(
                    potential_key,
                    user.id,
                    user.username or "",
                    user.full_name or "",
                )
                await update.message.reply_text(h(msg))

        allowed, key_or_role = self.key_manager.verify_user_access(
            user.id, user.username
        )
        if not allowed:
            await self.check_access(update)
            return

        welcome = (
            f"👋 Xin chào <b>{h(user.full_name)}</b>!\n"
            "Chào mừng bạn đến với <b>Social Video Automation Studio Bot</b> (Shorts / Reels / TikTok).\n\n"
            f"✅ <b>Trạng thái bản quyền:</b> <code>{h(key_or_role)}</code>\n"
            f"👑 <b>Admin quản lý:</b> @{h(ADMIN_USERNAME)}\n\n"
            "🚀 <b>2 CHẾ ĐỘ SẢN XUẤT VIDEO TỰ ĐỘNG:</b>\n"
            "📸 <b>Chế độ 1 — Đọc Chữ Trong Ảnh & Tự Động Cap Tin Nhắn:</b>\n"
            "• Gửi <b>1 hoặc nhiều ảnh tin nhắn cùng lúc</b> (ảnh đoạn chat dài hoặc ảnh từng câu lẻ).\n"
            "• Bot tự động <b>cắt (cap) từng dòng từ trên xuống dưới</b> và ghép theo <b>đúng thứ tự ảnh bạn gửi</b>, kết hợp <b>Giọng Đối Thoại Đa Nhân Vật / Giọng TikTok BV075</b>!\n\n"
            "🔗 <b>Chế độ 2 — Cào Link Bình Luận MXH:</b>\n"
            "• Dán link <code>Threads</code>, <code>TikTok</code>, <code>Facebook</code> để cào bài viết + bình luận và xuất video <code>.MP4</code> ngay trong Telegram!"
        )
        await update.message.reply_text(
            welcome,
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_main_reply_keyboard(user.id, user.username),
        )

    async def cmd_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        if not context.args:
            allowed, key_or_role = self.key_manager.verify_user_access(
                user.id, user.username
            )
            if allowed:
                await update.message.reply_text(
                    f"🔑 <b>Thông tin bản quyền của bạn:</b>\n"
                    f"• Mã Key đang gắn với tài khoản: <code>{h(key_or_role)}</code>\n"
                    f"• Telegram ID: <code>{user.id}</code>\n"
                    f"• Quản trị viên: @{h(ADMIN_USERNAME)}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    "🔑 Vui lòng nhập mã Key theo cú pháp:\n<code>/key CW26-XXXX-XXXX</code>\n\n"
                    f"Liên hệ Admin @{h(ADMIN_USERNAME)} để nhận Key.",
                    parse_mode=ParseMode.HTML,
                )
            return

        key_code = context.args[0].strip()
        ok, msg = self.key_manager.activate_key(
            key_code, user.id, user.username or "", user.full_name or ""
        )
        clean_msg = msg.replace("`", "")
        await update.message.reply_text(
            h(clean_msg),
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_main_reply_keyboard(user.id, user.username)
            if ok
            else None,
        )

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self.key_manager.is_admin(user.id, user.username):
            await update.message.reply_text(
                f"🚫 Chỉ Admin @{ADMIN_USERNAME} mới có quyền truy cập bảng quản trị!"
            )
            return

        text, markup = self.build_admin_panel()
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )

    async def cmd_genkey(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self.key_manager.is_admin(user.id, user.username):
            await update.message.reply_text(
                f"🚫 Chỉ Admin @{ADMIN_USERNAME} mới có quyền tạo Key!"
            )
            return

        note = " ".join(context.args) if context.args else ""
        k = self.key_manager.create_key(
            note=note, created_by=f"@{user.username or ADMIN_USERNAME}"
        )
        await update.message.reply_text(
            f"✅ <b>ĐÃ TẠO KEY MỚI THÀNH CÔNG</b>\n\n"
            f"🔑 Mã Key: <code>{h(k['key_code'])}</code>\n"
            f"📝 Ghi chú: <code>{h(k['note'] or 'Không có')}</code>\n"
            f"👤 Giới hạn: <code>1 Người dùng duy nhất</code>\n\n"
            f"👉 Gửi lệnh này cho người dùng: <code>/key {h(k['key_code'])}</code>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_delkey(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self.key_manager.is_admin(user.id, user.username):
            await update.message.reply_text(
                f"🚫 Chỉ Admin @{ADMIN_USERNAME} mới có quyền xóa Key!"
            )
            return

        if not context.args:
            await update.message.reply_text(
                "Vui lòng nhập mã Key cần xóa: <code>/delkey CW26-XXXX-XXXX</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        key_code = context.args[0].strip().upper()
        deleted, info = self.key_manager.delete_key(key_code)
        if not deleted or not info:
            await update.message.reply_text(
                f"❌ Không tìm thấy mã Key <code>{h(key_code)}</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        bound_uid = info.get("bound_user_id")
        if bound_uid and int(bound_uid) in self.user_sessions:
            del self.user_sessions[int(bound_uid)]

        if bound_uid:
            try:
                await context.bot.send_message(
                    chat_id=int(bound_uid),
                    text=(
                        f"🚫 <b>THÔNG BÁO THU HỒI KEY</b>\n\n"
                        f"Mã Key <code>{h(key_code)}</code> của bạn vừa bị Admin (@{h(ADMIN_USERNAME)}) xóa.\n"
                        f"Quyền sử dụng bot đã bị vô hiệu hóa ngay lập tức."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        await update.message.reply_text(
            f"🗑 Đã xóa và vô hiệu hóa ngay lập tức mã Key <code>{h(key_code)}</code>!",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_clearchat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        if not self.key_manager.is_admin(user.id, user.username):
            await update.message.reply_text(
                f"🚫 Chỉ Admin @{ADMIN_USERNAME} mới có quyền sử dụng tính năng này!",
                parse_mode=ParseMode.HTML,
            )
            return

        args = context.args or []
        if not args:
            # Clear current chat
            target_chat_id = update.effective_chat.id
            latest_id = update.message.message_id
            await self.purge_chat_messages(
                context.bot, target_chat_id, latest_msg_id=latest_id, max_scan=600
            )
            text_admin, markup = self.build_admin_panel()
            msg = await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    f"🧹 <b>ĐÃ XÓA SẠCH TOÀN BỘ TIN NHẮN TRONG CHAT NÀY!</b>\n\n{text_admin}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            self._record_msg_id(target_chat_id, msg.message_id)
            return

        arg_target = args[0].strip().lower()
        if arg_target in ("all", "tatca", "allusers"):
            if len(args) > 1 and args[1].lower() in ("confirm", "yes", "ok"):
                status_msg = await update.message.reply_text(
                    "⏳ <i>Đang tiến hành xóa sạch tin nhắn của TẤT CẢ người dùng...</i>",
                    parse_mode=ParseMode.HTML,
                )
                uids = self.key_manager.list_all_user_ids()
                total_deleted = 0
                for uid in uids:
                    try:
                        count = await self.purge_chat_messages(
                            context.bot, uid, max_scan=600
                        )
                        total_deleted += count
                    except Exception:
                        pass
                text_admin, markup = self.build_admin_panel()
                await self._safe_edit(
                    status_msg,
                    f"✅ <b>ĐÃ XÓA SẠCH TIN NHẮN CỦA TẤT CẢ NGƯỜI DÙNG!</b>\n\n"
                    f"📊 Đã xử lý: <b>{len(uids)}</b> người dùng.\n"
                    f"🗑 Tổng số tin nhắn đã xóa: <b>{total_deleted}</b> tin nhắn.\n\n{text_admin}",
                    markup,
                )
            else:
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "💥 XÁC NHẬN XÓA TẤT CẢ",
                                callback_data="adm:do_clean_all",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ HỦY BỎ", callback_data="adm:refresh"
                            )
                        ],
                    ]
                )
                await update.message.reply_text(
                    "⚠️ <b>CẢNH BÁO NGUY HIỂM:</b>\n"
                    "Bạn sắp xóa toàn bộ tin nhắn của Bot với <b>TẤT CẢ</b> người dùng trong hệ thống!\n"
                    "Hành động này không thể hoàn tác.\n\n"
                    "Bấm nút xác nhận bên dưới hoặc gõ: <code>/clearchat all confirm</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            return

        target_uid = self.key_manager.find_user_id(arg_target)
        if not target_uid:
            await update.message.reply_text(
                f"❌ Không tìm thấy người dùng có ID hoặc Username: <code>{h(arg_target)}</code>\n\n"
                f"💡 Cú pháp: <code>/clearchat &lt;user_id|@username&gt;</code> hoặc <code>/clearchat all</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        status_msg = await update.message.reply_text(
            f"⏳ <i>Đang dọn dẹp tin nhắn với người dùng <code>{target_uid}</code>...</i>",
            parse_mode=ParseMode.HTML,
        )
        deleted = await self.purge_chat_messages(context.bot, target_uid, max_scan=600)
        text_menu, markup = self.build_clean_user_menu()
        await self._safe_edit(
            status_msg,
            f"✅ <b>ĐÃ XÓA SẠCH TIN NHẮN!</b>\n\n"
            f"👤 Người dùng: <code>{target_uid}</code>\n"
            f"🗑 Đã xóa thành công: <b>{deleted}</b> tin nhắn.\n\n{text_menu}",
            markup,
        )

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.check_access(update):
            return
        user = update.effective_user
        if not user or not update.message:
            return
        text, markup = self.build_settings_menu(user.id, user.username or "")
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )

    async def cmd_watermark(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.check_access(update):
            return
        user = update.effective_user
        if not user or not update.message:
            return
        if not context.args:
            await update.message.reply_text(
                "Nhập nội dung chữ chạy theo cú pháp:\n<code>/watermark @ten_kenh_cua_ban</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        new_wm = " ".join(context.args).strip()
        self.key_manager.update_user_setting(user.id, "running_text", new_wm)
        await update.message.reply_text(
            f"✅ Đã cập nhật chữ chạy (Watermark) thành: <code>{h(new_wm)}</code>",
            parse_mode=ParseMode.HTML,
        )

    # ------------------------------------------------------------------
    # Background Video Upload Handler
    # ------------------------------------------------------------------
    async def handle_video_upload(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self.check_access(update):
            return
        user = update.effective_user
        msg = update.message
        if not user or not msg:
            return

        video_obj = msg.video or msg.document
        if not video_obj:
            return

        status_msg = await msg.reply_text("⏳ Đang tải video nền của bạn về máy chủ...")
        try:
            tg_file = await video_obj.get_file()
            bg_dir = ASSETS_DIR / "backgrounds"
            bg_dir.mkdir(parents=True, exist_ok=True)
            orig_name = (
                getattr(video_obj, "file_name", None)
                or f"tg_bg_{uuid.uuid4().hex[:6]}.mp4"
            )
            clean_name = re.sub(r"[^\w\-_.]", "_", orig_name)
            if not clean_name.lower().endswith(".mp4"):
                clean_name += ".mp4"

            dest_path = bg_dir / clean_name
            temp_dest = bg_dir / f"raw_{uuid.uuid4().hex[:6]}_{clean_name}"
            await tg_file.download_to_drive(custom_path=str(temp_dest))

            # Automatically strip all original audio from uploaded background video (guarantee 100% silent background)
            try:
                ffmpeg_exe = self.compositor.ffmpeg_exe
                cmd_mute = [
                    ffmpeg_exe,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(temp_dest),
                    "-c:v",
                    "copy",
                    "-an",
                    str(dest_path),
                ]
                await asyncio.to_thread(subprocess.run, cmd_mute, check=True)
            except Exception:
                shutil.copyfile(str(temp_dest), str(dest_path))
            finally:
                if temp_dest.exists():
                    try:
                        temp_dest.unlink()
                    except Exception:
                        pass

            self.key_manager.update_user_setting(user.id, "background_file", clean_name)
            await self._safe_edit(
                status_msg,
                f"✅ Đã tải lên và làm sạch âm thanh (100% Mute tiếng nền)! Đã chọn làm video nền: <code>{h(clean_name)}</code>",
            )
        except Exception as e:
            await self._safe_edit(status_msg, f"❌ Lỗi khi tải video nền: {h(e)}")

    # ------------------------------------------------------------------
    # Custom SFX Audio / Voice Note Upload Handler
    # ------------------------------------------------------------------
    async def handle_audio_upload(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self.check_access(update):
            return
        user = update.effective_user
        msg = update.message
        if not user or not msg:
            return

        session = self.user_sessions.get(user.id)
        if not session or not session.get("awaiting_input"):
            await msg.reply_text(
                "💡 Để gắn file SFX riêng cho từng dòng tin nhắn:\n"
                "1. Bấm nút <b>[🔊 Đổi SFX Âm Thanh]</b> trong bảng điều khiển.\n"
                "2. Chọn dòng cần đổi ➡️ bấm <b>[📤 Tải lên file SFX riêng]</b> rồi gửi file âm thanh hoặc tin nhắn thoại vào đây nhé!",
                parse_mode=ParseMode.HTML,
            )
            return

        mode = session.get("awaiting_input", "")
        if mode.startswith("custom_sfx:"):
            target_idx = int(mode.split(":", 1)[1])
            session["awaiting_input"] = None

            audio_obj = msg.audio or msg.voice or msg.document
            if not audio_obj:
                await msg.reply_text("⚠️ Không nhận diện được file âm thanh. Vui lòng gửi lại file .mp3 / .wav hoặc voice note!")
                return

            status_msg = await msg.reply_text("⏳ <i>Đang tải & xử lý âm thanh SFX chuẩn hóa...</i>", parse_mode=ParseMode.HTML)
            try:
                tg_file = await audio_obj.get_file()
                sfx_dir = ASSETS_DIR / "sfx"
                sfx_dir.mkdir(parents=True, exist_ok=True)
                tmp_audio = sfx_dir / f"_tmp_{user.id}_{uuid.uuid4().hex[:6]}.dat"
                dest_fn = f"user_sfx_{user.id}_{target_idx + 1}_{uuid.uuid4().hex[:6]}.wav"
                dest_wav = sfx_dir / dest_fn
                await tg_file.download_to_drive(custom_path=str(tmp_audio))

                cmd = [
                    self.compositor.ffmpeg_exe,
                    "-y",
                    "-i",
                    str(tmp_audio),
                    "-t",
                    "2.5",
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    str(dest_wav),
                ]
                await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True)
                if tmp_audio.exists():
                    try:
                        tmp_audio.unlink()
                    except Exception:
                        pass

                if 0 <= target_idx < len(session["comments"]):
                    session["comments"][target_idx]["sfx_reaction"] = dest_fn
                    session["comments"][target_idx]["custom_sfx_path"] = str(dest_wav)

                await status_msg.edit_text(
                    f"✅ <b>Đã gắn âm thanh riêng cho Dòng #{target_idx + 1} thành công!</b>",
                    parse_mode=ParseMode.HTML,
                )
                if any(c.get("is_manual_script") for c in session.get("comments", [])):
                    ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
                else:
                    ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
                await msg.reply_text(ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb)
            except Exception as e:
                await status_msg.edit_text(f"❌ Lỗi khi xử lý file âm thanh: {h(e)}")

    # ------------------------------------------------------------------
    # Multi-Image Chat Upload, Auto-Cap & Ordered OCR Handler
    # ------------------------------------------------------------------
    async def handle_photo_upload(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self.check_access(update):
            return
        user = update.effective_user
        msg = update.message
        if not user or not msg:
            return

        photo_obj = None
        if msg.photo:
            photo_obj = msg.photo[-1]
        elif msg.sticker:
            photo_obj = msg.sticker
        elif msg.document:
            mime = (msg.document.mime_type or "").lower()
            fname = (msg.document.file_name or "").lower()
            if mime.startswith("image/") or fname.endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
            ):
                photo_obj = msg.document
        if not photo_obj:
            return

        now_ts = time.monotonic()
        uid = user.id
        if uid not in self._pending_photos or not self._pending_photos[uid]:
            self._pending_photos[uid] = []
            self._photo_first_ts[uid] = now_ts

        self._pending_photos[uid].append((msg.message_id, msg, photo_obj))
        self._photo_last_ts[uid] = now_ts

        existing_task = self._photo_flush_tasks.get(uid)
        if not existing_task or existing_task.done():
            t = asyncio.create_task(
                self._flush_user_photos(uid, user.username or "")
            )
            self._photo_flush_tasks[uid] = t
            self.bg_tasks.add(t)
            t.add_done_callback(self.bg_tasks.discard)

    async def _flush_user_photos(self, user_id: int, username: str) -> None:
        # Wait briefly so all photos in a Telegram Album (media_group_id) or rapid multi-send arrive
        while True:
            await asyncio.sleep(0.28)
            now = time.monotonic()
            since_last = now - self._photo_last_ts.get(user_id, now)
            since_first = now - self._photo_first_ts.get(user_id, now)
            if since_last >= 0.90 or since_first >= 3.20:
                break

        batch = self._pending_photos.pop(user_id, [])
        self._photo_flush_tasks.pop(user_id, None)
        if not batch:
            return

        # Sort strictly by Telegram message_id ascending -> 100% exact user send order!
        batch.sort(key=lambda item: item[0])
        anchor_msg = batch[-1][1]

        async with self._get_photo_lock(user_id):
            allowed, _ = self.key_manager.verify_user_access(user_id, username)
            if not allowed:
                return

            upload_dir = OUTPUT_DIR / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            # Check if this photo was explicitly uploaded as a custom meme for a specific chat line
            session = self.user_sessions.get(user_id)
            awaiting = session.get("awaiting_input") if session else None
            caption = (anchor_msg.caption or "").strip()

            # Check if photo was uploaded as Avatar for Person A or Person B
            if awaiting in ("upload_av_a", "upload_av_b") and session:
                f_obj = batch[0][2]
                tg_file = await f_obj.get_file()
                orig_fn = getattr(f_obj, "file_name", "") or ""
                ext = ".png" if orig_fn.endswith(".png") else ".jpg"
                role_key = "a" if awaiting == "upload_av_a" else "b"
                dest = upload_dir / f"avatar_{role_key}_{user_id}_{uuid.uuid4().hex[:6]}{ext}"
                await tg_file.download_to_drive(custom_path=str(dest))

                final_av = dest
                try:
                    png_p = dest.with_suffix(".png")
                    with Image.open(dest) as p_im:
                        p_im.convert("RGBA").save(png_p, format="PNG")
                    final_av = png_p
                except Exception:
                    pass

                if role_key == "a":
                    session["user_a_avatar"] = str(final_av)
                    role_label = session.get("user_a_name", "Người dùng A")
                else:
                    session["user_b_avatar"] = str(final_av)
                    role_label = session.get("user_b_name", "Người dùng B")

                session["awaiting_input"] = None
                self._re_render_script_comments(user_id)

                await anchor_msg.reply_text(
                    f"✅ <b>Đã cập nhật Avatar cho [{h(role_label)}] thành công!</b>\n"
                    "Ảnh đại diện đã được bo tròn và áp dụng vào toàn bộ thẻ tin nhắn kịch bản.",
                    parse_mode=ParseMode.HTML,
                )
                studio_text, studio_kb = self.format_script_studio_message(user_id, username)
                await anchor_msg.reply_text(
                    studio_text, parse_mode=ParseMode.HTML, reply_markup=studio_kb
                )
                return

            target_meme_idx = None
            if awaiting and awaiting.startswith("custom_meme:"):
                try:
                    target_meme_idx = int(awaiting.split(":")[1])
                except Exception:
                    pass
            elif len(batch) == 1 and session and session.get("comments") and caption:
                m_cap = re.search(
                    r"(?:meme|ảnh\s*meme|dòng|cap|kèm|dưới)\s*#?\s*(\d+)|^#(\d+)$",
                    caption,
                    re.IGNORECASE,
                )
                if m_cap:
                    num_s = m_cap.group(1) or m_cap.group(2)
                    try:
                        c_num = int(num_s)
                        if 1 <= c_num <= len(session["comments"]):
                            target_meme_idx = c_num - 1
                    except Exception:
                        pass

            if (
                target_meme_idx is not None
                and session
                and 0 <= target_meme_idx < len(session["comments"])
            ):
                f_obj = batch[0][2]
                tg_file = await f_obj.get_file()
                orig_fn = getattr(f_obj, "file_name", "") or ""
                ext = ".png" if orig_fn.endswith(".png") else ".jpg"
                dest = (
                    upload_dir
                    / f"custom_meme_{user_id}_{target_meme_idx}_{uuid.uuid4().hex[:6]}{ext}"
                )
                await tg_file.download_to_drive(custom_path=str(dest))

                final_meme = dest
                try:
                    png_p = dest.with_suffix(".png")
                    with Image.open(dest) as p_im:
                        p_im.convert("RGBA").save(png_p, format="PNG")
                    final_meme = png_p
                except Exception:
                    pass

                session["comments"][target_meme_idx]["custom_meme_path"] = str(final_meme)
                session["comments"][target_meme_idx]["meme_image_path"] = str(final_meme)
                session["comments"][target_meme_idx]["meme_reaction"] = "custom"
                session["awaiting_input"] = None

                await anchor_msg.reply_text(
                    f"✅ <b>Đã gắn ảnh meme vào bên dưới Dòng #{target_meme_idx + 1} thành công!</b>\n"
                    f"Khi tạo video, ảnh meme này sẽ xuất hiện ngay bên dưới dòng tin nhắn #{target_meme_idx + 1} giống hệt video TikTok mẫu.",
                    parse_mode=ParseMode.HTML,
                )
                if any(c.get("is_manual_script") for c in session.get("comments", [])):
                    ws_text, ws_kb = self.format_script_review_message(user_id, username)
                else:
                    ws_text, ws_kb = self.format_workspace_message(user_id, username)
                await anchor_msg.reply_text(
                    ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb
                )
                return

            # Smart Meme/Sticker Recognition: When user has active lines and sends a single photo or sticker
            if len(batch) == 1 and session and session.get("comments"):
                f_obj = batch[0][2]
                is_stk = hasattr(f_obj, "set_name") or hasattr(f_obj, "emoji")
                tg_file = await f_obj.get_file()
                orig_fn = getattr(f_obj, "file_name", "") or ""
                ext = ".webp" if is_stk else (".png" if orig_fn.endswith(".png") else ".jpg")
                dest = upload_dir / f"pending_meme_{user_id}_{uuid.uuid4().hex[:6]}{ext}"
                await tg_file.download_to_drive(custom_path=str(dest))

                final_dest = dest
                png_path = dest.with_suffix(".png")
                try:
                    with Image.open(dest) as p_im:
                        p_im.convert("RGBA").save(png_path, format="PNG")
                    final_dest = png_path
                except Exception:
                    try:
                        cmd = ["ffmpeg", "-y", "-i", str(dest), "-vframes", "1", str(png_path)]
                        subprocess.run(cmd, capture_output=True, check=True)
                        if png_path.exists():
                            final_dest = png_path
                    except Exception:
                        pass

                session["pending_meme_path"] = str(final_dest)
                session["awaiting_input"] = None
                c_list = session["comments"]

                kb_bind = []
                row = []
                for idx_m, c_m in enumerate(c_list[:16]):
                    spk = c_m.get("speaker") or ("Phải" if c_m.get("align") == "right" else "Trái")
                    snip = c_m.get("content", "").replace("\n", " ")[:14]
                    btn_text = f"#{idx_m + 1} [{spk[:6]}]: {snip}"
                    row.append(InlineKeyboardButton(btn_text, callback_data=f"act:bind_meme:{idx_m}"))
                    if len(row) == 2:
                        kb_bind.append(row)
                        row = []
                if row:
                    kb_bind.append(row)

                kb_bind.append([
                    InlineKeyboardButton("📸 Đây là Ảnh Chụp Tin Nhắn (Tách Cap)", callback_data="act:ocr_pending_image")
                ])
                if any(c.get("is_manual_script") for c in session.get("comments", [])):
                    kb_bind.append([InlineKeyboardButton("🔙 Hủy / Về Kịch Bản", callback_data="ws:script_review")])
                else:
                    kb_bind.append([InlineKeyboardButton("🔙 Hủy / Về Dự Án", callback_data="ws:show")])

                media_type_label = "Sticker" if is_stk else "Ảnh Meme"
                await anchor_msg.reply_text(
                    f"🖼 <b>ĐÃ NHẬN {media_type_label.upper()} CỦA BẠN!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "👉 <b>Bấm chọn dòng bạn muốn gắn Meme này vào bên dưới:</b>\n"
                    "<i>(Meme sẽ xuất hiện ngay dưới tin nhắn đó khi tạo video)</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb_bind),
                )
                return

            status_msg = await anchor_msg.reply_text(
                f"📸 <b>Đang đọc chữ & tự động cắt (Cap) tin nhắn từ {len(batch)} ảnh</b> theo đúng thứ tự gửi...\n"
                "⏳ <i>Đang tách từng dòng từ trên xuống dưới...</i>",
                parse_mode=ParseMode.HTML,
            )
            downloaded_paths: List[str] = []

            try:
                for idx, (mid, _m, f_obj) in enumerate(batch):
                    tg_file = await f_obj.get_file()
                    ext = ".png"
                    orig_fn = getattr(f_obj, "file_name", "") or ""
                    if "." in orig_fn:
                        ext = "." + orig_fn.rsplit(".", 1)[-1].lower()
                    elif not hasattr(f_obj, "file_name"):
                        ext = ".jpg"
                    dest = upload_dir / f"tg_chat_{user_id}_{mid}_{idx:02d}_{uuid.uuid4().hex[:4]}{ext}"
                    await tg_file.download_to_drive(custom_path=str(dest))
                    downloaded_paths.append(str(dest))

                slices = await asyncio.to_thread(
                    self.image_chat_engine.process_batch_images, downloaded_paths
                )

                if not slices:
                    await self._safe_edit(
                        status_msg,
                        "⚠️ <b>Không nhận diện được dòng chữ tin nhắn rõ nét nào trong ảnh vừa gửi!</b>\n"
                        "👉 Hãy thử gửi ảnh chụp màn hình rõ chữ hơn hoặc gửi dưới dạng ảnh HD.",
                    )
                    return

                session = self.user_sessions.get(user_id)
                if (
                    not session
                    or session.get("mode") != "image_chat"
                    or session.get("rendered_completed")
                ):
                    session = self.init_image_chat_session(user_id)

                for s in slices:
                    rec = self.meme_engine.detect_comment_reaction(s.spoken_text)
                    disp_speaker = s.speaker or (
                        "Tin nhắn Phải" if s.align == "right" else "Tin nhắn Trái"
                    )
                    custom_m = getattr(s, "meme_image_path", None)
                    session["comments"].append(
                        {
                            "id": s.slice_id,
                            "username": disp_speaker,
                            "speaker": s.speaker or "",
                            "align": s.align,
                            "handle": s.speaker or "chat",
                            "avatar_url": None,
                            "image_url": None,
                            "card_image_path": s.image_path,
                            "custom_meme_path": custom_m,
                            "meme_image_path": custom_m,
                            "is_chat_strip": True,
                            "content": s.spoken_text,
                            "likes": "0",
                            "replies": 0,
                            "reposts": "0",
                            "shares": "0",
                            "time_ago": "",
                            "selected": True,
                            "meme_reaction": "none",
                            "sfx_reaction": rec.get("sfx_file") or "pop.wav",
                        }
                    )

                session["images_count"] = session.get("images_count", 0) + len(batch)

                cfg = self.key_manager.get_user_settings(user_id, username)
                if cfg["auto_render"]:
                    await self._safe_edit(
                        status_msg,
                        f"✅ Đã tách <b>{len(slices)} dòng tin nhắn</b>! Chế độ <b>Tự động Render</b> đang bật, bắt đầu xuất video MP4...",
                    )
                    await self._execute_video_render(
                        chat_id=anchor_msg.chat_id,
                        user_id=user_id,
                        username=username,
                        status_message=status_msg,
                    )
                else:
                    ws_text, ws_kb = self.format_workspace_message(user_id, username)
                    await self._safe_edit(status_msg, ws_text, reply_markup=ws_kb)

            except Exception as e:
                logger.exception("Error processing chat image upload")
                await self._safe_edit(
                    status_msg, f"❌ Lỗi khi quét & cắt ảnh tin nhắn: <code>{h(e)}</code>"
                )
            finally:
                for p in downloaded_paths:
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Text & Link Message Handler
    # ------------------------------------------------------------------
    async def handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user = update.effective_user
        msg = update.message
        if not user or not msg or not msg.text:
            return

        text = msg.text.strip()

        allowed, _ = self.key_manager.verify_user_access(user.id, user.username)
        if not allowed:
            if text.upper().startswith("CW26-"):
                ok, act_msg = self.key_manager.activate_key(
                    text, user.id, user.username or "", user.full_name or ""
                )
                await msg.reply_text(
                    h(act_msg.replace("`", "")),
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_main_reply_keyboard(user.id, user.username)
                    if ok
                    else None,
                )
                return
            await self.check_access(update)
            return

        if text == "📸 Đọc Ảnh Tin Nhắn":
            cur_s = self.user_sessions.get(user.id)
            if not cur_s or cur_s.get("mode") != "image_chat" or cur_s.get("rendered_completed"):
                self.init_image_chat_session(user.id)
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await msg.reply_text(
                ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb
            )
            return

        if text in ("🔥 Mẫu Demo Threads Viral", "🔥 Mẫu @motvongthread1612"):
            self.load_motvongthread_preset(user.id)
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await msg.reply_text(
                ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb
            )
            return

        if text == "🎬 Phiên làm việc hiện tại":
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await msg.reply_text(
                ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb
            )
            return

        if text == "⚙️ Cài đặt Video":
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await msg.reply_text(s_text, parse_mode=ParseMode.HTML, reply_markup=s_kb)
            return

        if text == "🎙 Chọn Giọng Đọc":
            await self._send_voice_selector(msg, user.id)
            return

        if text == "🖼 Chọn Video Nền":
            await self._send_bg_selector(msg, user.id)
            return

        if text == "🔑 Thông tin Key":
            await self.cmd_key(update, context)
            return

        if text.startswith("👑 Quản Trị Admin"):
            await self.cmd_admin(update, context)
            return

        session = self.user_sessions.get(user.id)
        if session and session.get("awaiting_input"):
            mode = session["awaiting_input"]
            session["awaiting_input"] = None

            if mode == "admin_clean_uid":
                if text.lower() in ("/cancel", "cancel", "hủy", "huy"):
                    text_menu, markup = self.build_clean_user_menu()
                    await msg.reply_text("❌ Đã hủy thao tác dọn dẹp.", reply_markup=markup)
                    return
                target_uid = self.key_manager.find_user_id(text)
                if not target_uid:
                    session["awaiting_input"] = "admin_clean_uid"
                    await msg.reply_text(
                        f"❌ Không tìm thấy người dùng có ID hoặc Username: <code>{h(text)}</code>\n\n"
                        f"Vui lòng nhập lại số ID chính xác (hoặc gõ <code>/cancel</code> để hủy):",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                status_msg = await msg.reply_text(
                    f"⏳ <i>Đang dọn dẹp tin nhắn với người dùng <code>{target_uid}</code>...</i>",
                    parse_mode=ParseMode.HTML,
                )
                deleted = await self.purge_chat_messages(
                    context.bot, target_uid, max_scan=600
                )
                text_menu, markup = self.build_clean_user_menu()
                await self._safe_edit(
                    status_msg,
                    f"✅ <b>ĐÃ XÓA SẠCH TIN NHẮN!</b>\n\n"
                    f"👤 Người dùng: <code>{target_uid}</code>\n"
                    f"🗑 Đã xóa thành công: <b>{deleted}</b> tin nhắn.\n\n{text_menu}",
                    markup,
                )
                return

            if mode == "watermark":
                self.key_manager.update_user_setting(user.id, "running_text", text)
                s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
                await msg.reply_text(
                    f"✅ Đã đổi chữ chạy thành: <code>{h(text)}</code>\n\n{s_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=s_kb,
                )
                return

            if mode == "edit_post" and session.get("post"):
                session["post"]["content"] = text
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    f"✅ Đã cập nhật nội dung bài viết gốc!\n\n{ws_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ws_kb,
                )
                return

            if mode.startswith("edit_comment:"):
                c_idx = int(mode.split(":", 1)[1])
                if 0 <= c_idx < len(session["comments"]):
                    session["comments"][c_idx]["content"] = text
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    f"✅ Đã cập nhật nội dung dòng #{c_idx + 1}!\n\n{ws_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ws_kb,
                )
                return

            if mode.startswith("custom_meme:"):
                c_idx = int(mode.split(":", 1)[1])
                if any(c.get("is_manual_script") for c in session.get("comments", [])):
                    ws_text, ws_kb = self.format_script_review_message(
                        user.id, user.username or ""
                    )
                else:
                    ws_text, ws_kb = self.format_workspace_message(
                        user.id, user.username or ""
                    )
                await msg.reply_text(
                    f"⚠️ Bạn vừa gửi văn bản thay vì ảnh. Đã hủy trạng thái chờ gửi meme cho dòng #{c_idx + 1}.\n"
                    f"👉 Để gắn meme vào dòng #{c_idx + 1}, bạn hãy gửi 1 file ảnh trực tiếp (hoặc gửi ảnh có chú thích <code>meme {c_idx + 1}</code>) nhé!\n\n{ws_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ws_kb,
                )
                return

            if mode == "add_comment":
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                for line in lines:
                    rec = self.meme_engine.detect_comment_reaction(line)
                    new_idx = len(session["comments"]) + 1
                    session["comments"].append(
                        {
                            "id": f"c_tg_{uuid.uuid4().hex[:4]}",
                            "username": f"Khán giả {new_idx}",
                            "handle": f"khangia_{new_idx}",
                            "avatar_url": f"https://api.dicebear.com/7.x/avataaars/svg?seed=tg_{new_idx}",
                            "content": line,
                            "likes": "520",
                            "replies": 5,
                            "time_ago": "vừa xong",
                            "selected": True,
                            "meme_reaction": rec.get("meme_file") or "none",
                            "sfx_reaction": rec.get("sfx_file") or "pop.wav",
                        }
                    )
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    f"✅ Đã thêm {len(lines)} bình luận mới!\n\n{ws_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ws_kb,
                )
                return

            if mode == "batch_text_replace":
                raw_lines = [l.strip() for l in text.split("\n") if l.strip()]
                comments = session.get("comments", [])
                updated_count = 0
                for i, ln in enumerate(raw_lines):
                    if i < len(comments):
                        comments[i]["content"] = ln
                        updated_count += 1
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    f"✅ Đã cập nhật lại lời thoại cho {updated_count} dòng tin nhắn!\n\n{ws_text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ws_kb,
                )
                return

            if mode == "set_name_a":
                session["user_a_name"] = text.strip()[:24]
                session["awaiting_input"] = None
                self._re_render_script_comments(user.id)
                await msg.reply_text(
                    f"✅ <b>Đã đổi tên Người Dùng A thành:</b> <code>{h(session['user_a_name'])}</code>",
                    parse_mode=ParseMode.HTML,
                )
                st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
                await msg.reply_text(st_text, parse_mode=ParseMode.HTML, reply_markup=st_kb)
                return

            if mode == "set_name_b":
                session["user_b_name"] = text.strip()[:24]
                session["awaiting_input"] = None
                self._re_render_script_comments(user.id)
                await msg.reply_text(
                    f"✅ <b>Đã đổi tên Người Dùng B thành:</b> <code>{h(session['user_b_name'])}</code>",
                    parse_mode=ParseMode.HTML,
                )
                st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
                await msg.reply_text(st_text, parse_mode=ParseMode.HTML, reply_markup=st_kb)
                return

            if mode == "manual_script":
                if session.get("mode") != "image_chat":
                    self.init_image_chat_session(user.id)
                    session = self.user_sessions[user.id]

                session["awaiting_input"] = None
                raw_lines = [l.strip() for l in text.split("\n") if l.strip()]
                new_slices = self._parse_and_append_script_lines(session, raw_lines)

                rev_text, rev_kb = self.format_script_review_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    rev_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=rev_kb,
                )
                return

        # Direct Chat Script Detection (no button click required if in image_chat or containing chat tags)
        if (
            (session and session.get("mode") == "image_chat")
            or text.startswith("[Trái]")
            or text.startswith("[Phải]")
            or text.startswith("[trái]")
            or text.startswith("[phải]")
            or text.startswith("A:")
            or text.startswith("B:")
            or text.startswith("a:")
            or text.startswith("b:")
        ) and not text.startswith("http") and not text.startswith("/") and len(text) > 3:
            raw_lines = [l.strip() for l in text.split("\n") if l.strip()]
            # Only trigger auto-script if multi-line OR has dialogue tag [Trái]/[Phải]/A:/B:
            if len(raw_lines) >= 1 and (len(raw_lines) >= 2 or "[" in raw_lines[0] or raw_lines[0][:2].lower() in ("a:", "b:")):
                if not session or session.get("mode") != "image_chat":
                    self.init_image_chat_session(user.id)
                    session = self.user_sessions[user.id]

                new_slices = self._parse_and_append_script_lines(session, raw_lines)

                rev_text, rev_kb = self.format_script_review_message(
                    user.id, user.username or ""
                )
                await msg.reply_text(
                    rev_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=rev_kb,
                )
                return

        self._spawn_task(self._scrape_and_prepare(update, text))

    async def _scrape_and_prepare(self, update: Update, url_or_text: str) -> None:
        user = update.effective_user
        msg = update.message
        if not user or not msg:
            return

        cfg = self.key_manager.get_user_settings(user.id, user.username or "")
        progress_msg = await msg.reply_text(
            f"🔍 <b>Đang cào dữ liệu bài viết & bình luận từ:</b> <code>{h(url_or_text[:80])}</code> ...\n"
            "⏳ <i>Vui lòng đợi vài giây...</i>",
            parse_mode=ParseMode.HTML,
        )

        try:
            post_data = await asyncio.to_thread(self.scraper.scrape, url_or_text)
            if cfg["min_likes"] > 0:
                c_filter = CommentFilter(min_likes=cfg["min_likes"])
                post_data.comments = c_filter.filter_comments(post_data.comments)

            comments_list = []
            for c in post_data.comments:
                rec = self.meme_engine.detect_comment_reaction(c.content)
                comments_list.append(
                    {
                        "id": c.id,
                        "username": c.username,
                        "handle": c.handle or c.username,
                        "avatar_url": c.avatar_url,
                        "image_url": getattr(c, "image_url", None),
                        "content": c.content,
                        "likes": c.likes,
                        "replies": c.replies,
                        "reposts": getattr(c, "reposts", "0"),
                        "shares": getattr(c, "shares", "0"),
                        "time_ago": "",
                        "selected": True,
                        "meme_reaction": "none",
                        "sfx_reaction": rec.get("sfx_file") or "pop.wav",
                    }
                )

            rec_bgm, _ = self.meme_engine.auto_select_bgm(
                post_data.content, [c["content"] for c in comments_list]
            )

            self.user_sessions[user.id] = {
                "mode": "social",
                "url": url_or_text,
                "platform": post_data.platform or detect_platform(url_or_text),
                "recommended_bgm": rec_bgm,
                "post": {
                    "id": post_data.post_id or "post_01",
                    "username": post_data.author,
                    "handle": post_data.author_handle,
                    "avatar_url": post_data.avatar_url,
                    "image_url": getattr(post_data, "image_url", None),
                    "content": post_data.content,
                    "likes": post_data.likes,
                    "replies": len(comments_list),
                    "reposts": getattr(post_data, "reposts", "0"),
                    "shares": getattr(post_data, "shares", "0"),
                    "time_ago": "",
                    "is_op": True,
                },
                "comments": comments_list,
                "awaiting_input": None,
            }

            if cfg["auto_render"]:
                await self._safe_edit(
                    progress_msg,
                    "✅ Đã cào xong dữ liệu! Chế độ <b>Tự động Render</b> đang bật, bắt đầu xuất video MP4...",
                )
                await self._execute_video_render(
                    chat_id=msg.chat_id,
                    user_id=user.id,
                    username=user.username or "",
                    status_message=progress_msg,
                )
            else:
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await self._safe_edit(progress_msg, ws_text, reply_markup=ws_kb)

        except Exception as e:
            logger.exception("Error scraping link in Telegram bot")
            await self._safe_edit(progress_msg, f"❌ Lỗi khi cào dữ liệu: {h(e)}")

    # ------------------------------------------------------------------
    # Helper Sub-Menus
    # ------------------------------------------------------------------
    async def _send_voice_selector(
        self, msg_or_query_msg: Any, user_id: int, edit: bool = False
    ) -> None:
        cfg = self.key_manager.get_user_settings(user_id)
        cur_v = cfg["voice"]
        kb: List[List[InlineKeyboardButton]] = []
        for v in VOICE_CATALOG:
            mark = "✅ " if cur_v == v["id"] else ""
            kb.append(
                [
                    InlineKeyboardButton(
                        f"{mark}{v['name']}",
                        callback_data=f"set_v:{v['id']}",
                    )
                ]
            )
        kb.append(
            [
                InlineKeyboardButton(
                    "🔊 Nghe thử giọng đang chọn", callback_data="cfg:test_tts"
                )
            ]
        )
        kb.append(
            [
                InlineKeyboardButton("🎬 Quay lại Dự án", callback_data="ws:show"),
                InlineKeyboardButton("⚙️ Cài đặt Video", callback_data="menu:settings"),
            ]
        )
        text = (
            "🎙 <b>CHỌN GIỌNG ĐỌC AI & GIỌNG TIKTOK VIRAL</b>\n"
            f"Đang chọn: <code>{h(get_voice_label(cur_v))}</code>\n\n"
            "• <b>🎭 Đối Thoại Đa Nhân Vật:</b> Tự động phân vai nhiều giọng nam/nữ trẻ trung cho từng người nhắn giống hệt video TikTok mẫu!\n"
            "• <b>🔥 Thanh Niên / Cô Gái TikTok (BV075 / BV074):</b> Giọng đọc CapCut / TikTok triệu view."
        )
        if edit:
            await self._safe_edit(msg_or_query_msg, text, InlineKeyboardMarkup(kb))
        else:
            await msg_or_query_msg.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb)
            )

    async def _send_bg_selector(
        self, msg_or_query_msg: Any, user_id: int, edit: bool = False
    ) -> None:
        cfg = self.key_manager.get_user_settings(user_id)
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

        kb = []
        for bg in bgs[:10]:
            mark = "✅ " if cfg["background_file"] == bg else "🎬 "
            kb.append(
                [InlineKeyboardButton(f"{mark}{bg}", callback_data=f"set_bg:{bg}")]
            )
        kb.append(
            [InlineKeyboardButton("🔙 Quay lại Cài đặt", callback_data="menu:settings")]
        )

        text = (
            "🖼 <b>CHỌN VIDEO NỀN (BACKGROUND 9:16)</b>\n"
            f"Đang chọn: <code>{h(cfg['background_file'])}</code>\n\n"
            "💡 <i>Mẹo: Bạn có thể gửi trực tiếp 1 file video .MP4 vào khung chat của Bot để tải lên làm video nền riêng!</i>"
        )
        if edit:
            await self._safe_edit(msg_or_query_msg, text, InlineKeyboardMarkup(kb))
        else:
            await msg_or_query_msg.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb)
            )

    # ------------------------------------------------------------------
    # Callback Query Handler
    # ------------------------------------------------------------------
    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user or not query.data:
            return

        data = query.data

        if data.startswith("adm:"):
            if not self.key_manager.is_admin(user.id, user.username):
                await query.answer(
                    f"🚫 Chỉ Admin @{ADMIN_USERNAME} mới có quyền này!", show_alert=True
                )
                return

            if data == "adm:gen1":
                k = self.key_manager.create_key(
                    note="Tạo từ Bot", created_by=f"@{user.username or ADMIN_USERNAME}"
                )
                await query.answer(f"Đã tạo Key: {k['key_code']}")
                text, markup = self.build_admin_panel()
                await self._safe_edit(
                    query.message,
                    f"✅ <b>Vừa tạo Key mới:</b> <code>{h(k['key_code'])}</code> (Lệnh kích hoạt: <code>/key {h(k['key_code'])}</code>)\n\n{text}",
                    markup,
                )
                return

            if data == "adm:gen5":
                created = [
                    self.key_manager.create_key(
                        note="Batch 5", created_by=f"@{user.username or ADMIN_USERNAME}"
                    )["key_code"]
                    for _ in range(5)
                ]
                await query.answer("Đã tạo 5 Key mới!")
                text, markup = self.build_admin_panel()
                keys_str = "\n".join([f"• <code>/key {h(c)}</code>" for c in created])
                await self._safe_edit(
                    query.message,
                    f"✅ <b>Vừa tạo 5 Key mới:</b>\n{keys_str}\n\n{text}",
                    markup,
                )
                return

            if data.startswith("adm:del:"):
                key_code = data.split("adm:del:", 1)[1]
                deleted, info = self.key_manager.delete_key(key_code)
                if deleted and info:
                    bound_uid = info.get("bound_user_id")
                    if bound_uid and int(bound_uid) in self.user_sessions:
                        del self.user_sessions[int(bound_uid)]
                    if bound_uid:
                        try:
                            await context.bot.send_message(
                                chat_id=int(bound_uid),
                                text=(
                                    f"🚫 <b>THÔNG BÁO THU HỒI KEY</b>\n\n"
                                    f"Mã Key <code>{h(key_code)}</code> của bạn vừa bị Admin (@{h(ADMIN_USERNAME)}) xóa.\n"
                                    f"Bạn không thể tiếp tục sử dụng bot."
                                ),
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception:
                            pass
                    await query.answer(
                        f"🗑 Đã xóa & vô hiệu hóa Key {key_code} ngay lập tức!",
                        show_alert=True,
                    )
                else:
                    await query.answer("Key không tồn tại hoặc đã xóa.")

                text, markup = self.build_admin_panel()
                await self._safe_edit(query.message, text, markup)
                return

            if data == "adm:refresh":
                await query.answer("Đã làm mới!")
                text, markup = self.build_admin_panel()
                await self._safe_edit(query.message, text, markup)
                return

            if data == "adm:clean_self":
                await query.answer("Đang xóa tin nhắn chat này...")
                chat_id = query.message.chat_id
                latest_id = query.message.message_id
                await self.purge_chat_messages(
                    context.bot, chat_id, latest_msg_id=latest_id, max_scan=600
                )
                text, markup = self.build_admin_panel()
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🧹 <b>ĐÃ XÓA SẠCH TIN NHẮN TRONG CHAT NÀY!</b>\n\n{text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
                self._record_msg_id(chat_id, msg.message_id)
                return

            if data == "adm:clean_user_menu":
                await query.answer()
                text, markup = self.build_clean_user_menu()
                await self._safe_edit(query.message, text, markup)
                return

            if data.startswith("adm:clean_uid:"):
                target_uid_str = data.split("adm:clean_uid:", 1)[1]
                target_uid = int(target_uid_str)
                await query.answer()
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ XÁC NHẬN XÓA", callback_data=f"adm:do_clean:{target_uid}")],
                    [InlineKeyboardButton("🔙 Quay Lại", callback_data="adm:clean_user_menu")],
                ])
                await self._safe_edit(
                    query.message,
                    f"⚠️ <b>XÁC NHẬN XÓA TIN NHẮN USER</b>\n\n"
                    f"Bạn có chắc chắn muốn xóa toàn bộ lịch sử tin nhắn của Bot với người dùng <code>{target_uid}</code> không?\n\n"
                    f"<i>(Hệ thống sẽ quét và dọn sạch các tin nhắn trong vòng 48h và toàn bộ tin nhắn Bot đã gửi)</i>",
                    kb,
                )
                return

            if data.startswith("adm:do_clean:"):
                target_uid_str = data.split("adm:do_clean:", 1)[1]
                target_uid = int(target_uid_str)
                await query.answer(f"Đang xóa tin nhắn của UID {target_uid}...", show_alert=False)
                deleted = await self.purge_chat_messages(context.bot, target_uid, max_scan=600)
                text, markup = self.build_clean_user_menu()
                await self._safe_edit(
                    query.message,
                    f"✅ <b>Đã dọn dẹp thành công {deleted} tin nhắn</b> của người dùng <code>{target_uid}</code>!\n\n{text}",
                    markup,
                )
                return

            if data == "adm:clean_all_confirm":
                await query.answer()
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💥 XÁC NHẬN XÓA TẤT CẢ", callback_data="adm:do_clean_all")],
                    [InlineKeyboardButton("❌ HỦY BỎ", callback_data="adm:refresh")],
                ])
                await self._safe_edit(
                    query.message,
                    "⚠️ <b>CẢNH BÁO NGUY HIỂM:</b>\n\n"
                    "Bạn sắp xóa toàn bộ lịch sử tin nhắn của Bot với <b>TẤT CẢ</b> người dùng trong hệ thống!\n"
                    "Hành động này không thể hoàn tác.\n\n"
                    "Bạn có chắc chắn muốn tiếp tục thực hiện không?",
                    kb,
                )
                return

            if data == "adm:do_clean_all":
                await query.answer("Đang tiến hành dọn dẹp tất cả users...", show_alert=False)
                await self._safe_edit(
                    query.message,
                    "⏳ <b>ĐANG TIẾN HÀNH DỌN DẸP TOÀN BỘ HỆ THỐNG...</b>\n\nVui lòng đợi vài giây trong khi Bot xóa tin nhắn của từng người dùng...",
                )
                uids = self.key_manager.list_all_user_ids()
                total_deleted = 0
                for uid in uids:
                    try:
                        count = await self.purge_chat_messages(context.bot, uid, max_scan=600)
                        total_deleted += count
                    except Exception:
                        pass
                text, markup = self.build_admin_panel()
                await self._safe_edit(
                    query.message,
                    f"✅ <b>ĐÃ HOÀN TẤT XÓA TIN NHẮN TẤT CẢ USERS!</b>\n\n"
                    f"📊 Đã xử lý: <b>{len(uids)}</b> người dùng\n"
                    f"🗑 Tổng cộng đã xóa: <b>{total_deleted}</b> tin nhắn.\n\n{text}",
                    markup,
                )
                return

            if data == "adm:clean_input_uid":
                session = self.user_sessions.get(user.id) or {}
                session["awaiting_input"] = "admin_clean_uid"
                self.user_sessions[user.id] = session
                await query.answer()
                await self._safe_edit(
                    query.message,
                    "⌨️ <b>NHẬP USER ID HOẶC USERNAME CẦN XÓA TIN NHẮN:</b>\n\n"
                    "Gửi ID dạng số (ví dụ: <code>8618384107</code>) hoặc Username (ví dụ: <code>@Tama839</code>) vào ô chat.\n\n"
                    "<i>Hoặc bấm nút bên dưới để quay lại.</i>",
                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Quay lại", callback_data="adm:clean_user_menu")]]),
                )
                return

        if not await self.check_access(update):
            return

        if user.id not in self.user_sessions:
            self.load_motvongthread_preset(user.id)
        session = self.user_sessions[user.id]

        if data.startswith("tgl_c:"):
            idx = int(data.split(":")[1])
            if 0 <= idx < len(session["comments"]):
                cur = session["comments"][idx].get("selected", True)
                session["comments"][idx]["selected"] = not cur
                await query.answer(
                    f"Bình luận #{idx + 1}: {'Đã chọn ✅' if not cur else 'Bỏ chọn ⬜'}"
                )
                ws_text, ws_kb = self.format_workspace_message(
                    user.id, user.username or ""
                )
                await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:sel_all":
            for c in session["comments"]:
                c["selected"] = True
            await query.answer("Đã chọn tất cả bình luận!")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:unsel_all":
            for c in session["comments"]:
                c["selected"] = False
            await query.answer("Đã bỏ chọn tất cả!")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:show":
            await query.answer()
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:manual_script_menu":
            await query.answer()
            st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
            await self._safe_edit(query.message, st_text, st_kb)
            return

        if data == "ws:set_name_a":
            session["awaiting_input"] = "set_name_a"
            await query.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Hủy bỏ", callback_data="ws:manual_script_menu")]])
            await query.message.reply_text(
                "✏️ <b>ĐỔI TÊN NGƯỜI DÙNG A (BÊN TRÁI)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Hãy nhập Tên mới cho Người Dùng A (Ví dụ: <code>Nam</code>, <code>Anh Thơ</code>, <code>Chồng</code>, <code>Hunter</code>...):",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb,
            )
            return

        if data == "ws:set_name_b":
            session["awaiting_input"] = "set_name_b"
            await query.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Hủy bỏ", callback_data="ws:manual_script_menu")]])
            await query.message.reply_text(
                "✏️ <b>ĐỔI TÊN NGƯỜI DÙNG B (BÊN PHẢI)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Hãy nhập Tên mới cho Người Dùng B (Ví dụ: <code>Lan</code>, <code>Nghệ sĩ</code>, <code>Vợ</code>, <code>Em</code>...):",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb,
            )
            return

        if data == "ws:upload_av_a":
            session["awaiting_input"] = "upload_av_a"
            await query.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Hủy bỏ", callback_data="ws:manual_script_menu")]])
            await query.message.reply_text(
                "📸 <b>TẢI ẢNH AVATAR CHO NGƯỜI DÙNG A (BÊN TRÁI)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Hãy gửi 1 tấm ảnh chân dung/avatar cho Người Dùng A vào đây!\n"
                "⚡ <i>Bot sẽ tự động cắt bo tròn chuyên nghiệp và hiển thị ở mọi tin nhắn của Người Dùng A.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb,
            )
            return

        if data == "ws:upload_av_b":
            session["awaiting_input"] = "upload_av_b"
            await query.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Hủy bỏ", callback_data="ws:manual_script_menu")]])
            await query.message.reply_text(
                "📸 <b>TẢI ẢNH AVATAR CHO NGƯỜI DÙNG B (BÊN PHẢI)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Hãy gửi 1 tấm ảnh chân dung/avatar cho Người Dùng B vào đây!\n"
                "⚡ <i>Bot sẽ tự động cắt bo tròn chuyên nghiệp và hiển thị ở mọi tin nhắn của Người Dùng B.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb,
            )
            return

        if data == "ws:script_review":
            await query.answer()
            rev_text, rev_kb = self.format_script_review_message(user.id, user.username or "")
            await self._safe_edit(query.message, rev_text, rev_kb)
            return

        if data == "ws:set_color_a":
            await query.answer()
            col_text, col_kb = self.format_script_color_menu(user.id, target="a")
            await self._safe_edit(query.message, col_text, col_kb)
            return

        if data == "ws:set_color_b":
            await query.answer()
            col_text, col_kb = self.format_script_color_menu(user.id, target="b")
            await self._safe_edit(query.message, col_text, col_kb)
            return

        if data.startswith("set_col_a:"):
            col_k = data.split(":", 1)[1]
            session["user_a_color"] = col_k
            await query.answer(f"Đã chọn màu {NAME_COLOR_PALETTE.get(col_k, ('Màu mới', ''))[0]} cho Người dùng A")
            self._re_render_script_comments(user.id)
            st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
            await self._safe_edit(query.message, st_text, st_kb)
            return

        if data.startswith("set_col_b:"):
            col_k = data.split(":", 1)[1]
            session["user_b_color"] = col_k
            await query.answer(f"Đã chọn màu {NAME_COLOR_PALETTE.get(col_k, ('Màu mới', ''))[0]} cho Người dùng B")
            self._re_render_script_comments(user.id)
            st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
            await self._safe_edit(query.message, st_text, st_kb)
            return

        if data == "ws:set_voice_a":
            await query.answer()
            v_text, v_kb = self.format_script_voice_menu(user.id, target="a")
            await self._safe_edit(query.message, v_text, v_kb)
            return

        if data == "ws:set_voice_b":
            await query.answer()
            v_text, v_kb = self.format_script_voice_menu(user.id, target="b")
            await self._safe_edit(query.message, v_text, v_kb)
            return

        if data.startswith("set_spk_v_a:"):
            v_id = data.split(":", 1)[1]
            session["user_a_voice"] = v_id
            await query.answer(f"Đã gán giọng {get_voice_label(v_id)} cho Nhân vật A")
            st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
            await self._safe_edit(query.message, st_text, st_kb)
            return

        if data.startswith("set_spk_v_b:"):
            v_id = data.split(":", 1)[1]
            session["user_b_voice"] = v_id
            await query.answer(f"Đã gán giọng {get_voice_label(v_id)} cho Nhân vật B")
            st_text, st_kb = self.format_script_studio_message(user.id, user.username or "")
            await self._safe_edit(query.message, st_text, st_kb)
            return

        if data == "ws:input_script_now":
            session["awaiting_input"] = "manual_script"
            await query.answer()
            name_a = session.get("user_a_name") or "Người dùng A"
            name_b = session.get("user_b_name") or "Người dùng B"
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Quay Lại Studio", callback_data="ws:manual_script_menu")]])
            await query.message.reply_text(
                "✍️ <b>NHẬP KỊCH BẢN CHAT 2 VAI (TỰ ĐỘNG VẼ BONG BÓNG & AVATAR)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Hãy dán/nhập đoạn chat vào đây, mỗi dòng là một tin nhắn:\n\n"
                "💡 <b>Ví dụ cú pháp:</b>\n"
                f"• <code>A: Anh có gói nào cho em không</code>\n"
                f"• <code>B: Dạ có gói 20tr nè anh</code>\n"
                f"• <code>A: Lotte mới của Hàn Quốc hả em</code>\n"
                f"• <code>B: Dạ đúng rồi anh ơi</code>\n\n"
                "Hoặc dán văn bản bình thường (Bot sẽ tự động đảo lượt xen kẽ A - B)!\n"
                f"⚡ <i>Nhân vật A: <b>{h(name_a)}</b> | Nhân vật B: <b>{h(name_b)}</b></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb,
            )
            return

        if data == "ws:batch_text_menu":
            session["awaiting_input"] = "batch_text_replace"
            await query.answer()
            c_len = len(session.get("comments", []))
            await query.message.reply_text(
                "📝 <b>SỬA LẠI TOÀN BỘ LỜI THOẠI HÀNG LOẠT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Hãy dán danh sách các câu thoại mới vào đây (mỗi dòng tương ứng với 1 câu hiện tại từ #1 đến #{c_len}):\n\n"
                "Ví dụ:\n"
                "<code>Câu mới dòng 1</code>\n"
                "<code>Câu mới dòng 2</code>\n"
                "<code>Câu mới dòng 3</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "ws:clear_chat":
            self.init_image_chat_session(user.id)
            await query.answer("🗑 Đã xóa danh sách cũ! Hãy gửi ảnh tin nhắn mới.")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:edit_line_menu":
            await query.answer()
            kb_ln = []
            for idx, c in enumerate(session["comments"][:24]):
                spk = c.get("speaker") or c.get("username", f"Dòng {idx + 1}")
                kb_ln.append(
                    [
                        InlineKeyboardButton(
                            f"✏️ #{idx + 1} [{spk[:14]}]: {c['content'][:32]}",
                            callback_data=f"edit_ln:{idx}",
                        )
                    ]
                )
            kb_ln.append(
                [InlineKeyboardButton("🔙 Quay lại Dự án", callback_data="ws:show")]
            )
            await self._safe_edit(
                query.message,
                "✏️ <b>CHỌN DÒNG TIN NHẮN CẦN CHỈNH SỬA CHỮ ĐỌC:</b>",
                InlineKeyboardMarkup(kb_ln),
            )
            return

        if data.startswith("edit_ln:"):
            idx = int(data.split(":", 1)[1])
            await query.answer()
            if 0 <= idx < len(session["comments"]):
                c = session["comments"][idx]
                session["awaiting_input"] = f"edit_comment:{idx}"
                await query.message.reply_text(
                    f"✏️ <b>SỬA CHỮ ĐỌC CHO DÒNG #{idx + 1}</b>\n"
                    f"Chữ hiện tại: <code>{h(c['content'])}</code>\n\n"
                    "👉 Nhập câu mới bạn muốn giọng AI đọc cho ảnh cap này:",
                    parse_mode=ParseMode.HTML,
                )
            return

        if data == "ws:sfx_menu":
            await query.answer()
            session["awaiting_input"] = None
            kb_sfx = [
                [
                    InlineKeyboardButton("🤣 Tất cả Fah", callback_data="set_all_sfx:fah.mp3"),
                    InlineKeyboardButton("💣 Tất cả Vine Boom", callback_data="set_all_sfx:vine_boom.mp3"),
                ],
                [
                    InlineKeyboardButton("✨ Tất cả Anime Wow", callback_data="set_all_sfx:anime_wow.mp3"),
                    InlineKeyboardButton("😏 Tất cả Rizz", callback_data="set_all_sfx:rizz.mp3"),
                ],
                [
                    InlineKeyboardButton("🎈 Tất cả Pop", callback_data="set_all_sfx:pop.wav"),
                    InlineKeyboardButton("🔔 Tất cả Ding", callback_data="set_all_sfx:ding.mp3"),
                ],
                [
                    InlineKeyboardButton("🔇 TẮT ÂM TẤT CẢ DÒNG", callback_data="set_all_sfx:none"),
                ],
            ]
            for idx, c in enumerate(session.get("comments", [])[:24]):
                cur_s = c.get("sfx_reaction", "pop.wav")
                s_icon = "🔇" if cur_s == "none" else "🔊"
                sfx_label = self.meme_engine.get_sfx_name(cur_s)
                sfx_btn_txt = sfx_label.split(" (")[0]
                if len(sfx_btn_txt) > 16:
                    sfx_btn_txt = sfx_btn_txt[:14] + ".."
                snip = c.get("content", "").replace("\n", " ")[:20]
                kb_sfx.append(
                    [
                        InlineKeyboardButton(
                            f"{s_icon} #{idx + 1} [{sfx_btn_txt}]: {snip}",
                            callback_data=f"sfx_line:{idx}:0",
                        )
                    ]
                )
            has_script = any(c.get("is_manual_script") for c in session.get("comments", []))
            if has_script:
                kb_sfx.append([InlineKeyboardButton("🔙 Quay lại Kịch Bản", callback_data="ws:script_review")])
            kb_sfx.append([InlineKeyboardButton("🔙 Quay lại Bảng Điều Khiển", callback_data="ws:show")])

            sfx_count = len(self.meme_engine.get_available_sfx()) - 1
            await self._safe_edit(
                query.message,
                f"🔊 <b>KHO ÂM THANH HIỆU ỨNG VIRAL ({sfx_count} SOUNDS MYINSTANTS)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Bấm đổi nhanh cho <b>Toàn bộ dòng</b> ở trên, hoặc bấm vào <b>từng dòng tin nhắn</b> ở dưới để chọn trong kho 50+ âm thanh Myinstants hot nhất:",
                InlineKeyboardMarkup(kb_sfx),
            )
            return

        if data.startswith("set_all_sfx:"):
            s_name = data.split(":", 1)[1]
            for c in session.get("comments", []):
                c["sfx_reaction"] = s_name
                if s_name != "custom":
                    c["custom_sfx_path"] = None
            label = self.meme_engine.get_sfx_name(s_name)
            await query.answer(f"Đã đổi toàn bộ SFX thành: {label}!")
            if any(c.get("is_manual_script") for c in session.get("comments", [])):
                ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
            else:
                ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data.startswith("sfx_page_info:"):
            await query.answer("Dùng 2 nút mũi tên để lật trang danh sách âm thanh!", show_alert=False)
            return

        if data.startswith("sfx_line:"):
            parts = data.split(":")
            idx = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            await query.answer()

            comments = session.get("comments", [])
            if 0 <= idx < len(comments):
                c = comments[idx]
                cur_s = c.get("sfx_reaction", "pop.wav")
                cur_name = self.meme_engine.get_sfx_name(cur_s)
                c_content = c.get("content", "").replace("\n", " ")
                if len(c_content) > 50:
                    c_content = c_content[:47] + "..."

                all_sfx = self.meme_engine.get_available_sfx()
                sound_items = [s for s in all_sfx if s["id"] != "none"]

                page_size = 8
                total_pages = max(1, (len(sound_items) + page_size - 1) // page_size)
                page = max(0, min(page, total_pages - 1))

                start = page * page_size
                chunk = sound_items[start : start + page_size]

                kb_item_sfx: List[List[InlineKeyboardButton]] = []
                row: List[InlineKeyboardButton] = []
                for s in chunk:
                    is_active = (cur_s == s["id"])
                    mark = "✅ " if is_active else ""
                    label = s["name"]
                    if len(label) > 18:
                        label = label.split(" (")[0][:18]
                    row.append(
                        InlineKeyboardButton(
                            f"{mark}{label}",
                            callback_data=f"set_sfx:{idx}:{s['id']}",
                        )
                    )
                    if len(row) == 2:
                        kb_item_sfx.append(row)
                        row = []
                if row:
                    kb_item_sfx.append(row)

                # Navigation pagination
                nav_row: List[InlineKeyboardButton] = []
                if page > 0:
                    nav_row.append(
                        InlineKeyboardButton("⬅️ Trước", callback_data=f"sfx_line:{idx}:{page - 1}")
                    )
                nav_row.append(
                    InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data=f"sfx_page_info:{idx}")
                )
                if page < total_pages - 1:
                    nav_row.append(
                        InlineKeyboardButton("Sau ➡️", callback_data=f"sfx_line:{idx}:{page + 1}")
                    )
                kb_item_sfx.append(nav_row)

                kb_item_sfx.append([
                    InlineKeyboardButton("🔇 Tắt Âm Dòng Này", callback_data=f"set_sfx:{idx}:none"),
                    InlineKeyboardButton("📤 Tải SFX Riêng", callback_data=f"upload_sfx:{idx}"),
                ])
                kb_item_sfx.append([
                    InlineKeyboardButton("🔙 Quay lại Danh Sách Dòng", callback_data="ws:sfx_menu"),
                ])

                await self._safe_edit(
                    query.message,
                    f"🔊 <b>CHỌN TIẾNG HIỆU ỨNG CHO DÒNG #{idx + 1}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"💬 Nội dung: <i>{h(c_content)}</i>\n"
                    f"🎵 Đang chọn: <b>{h(cur_name)}</b>\n\n"
                    f"👉 Chọn 1 âm thanh từ kho <b>{len(sound_items)} SFX Myinstants</b> bên dưới (Trang {page + 1}/{total_pages}):",
                    InlineKeyboardMarkup(kb_item_sfx),
                )
            return

        if data.startswith("set_sfx:"):
            parts = data.split(":")
            idx = int(parts[1])
            s_name = parts[2]
            if 0 <= idx < len(session.get("comments", [])):
                session["comments"][idx]["sfx_reaction"] = s_name
                session["comments"][idx]["custom_sfx_path"] = None
                label = self.meme_engine.get_sfx_name(s_name)
                await query.answer(f"Dòng #{idx + 1}: Đã chọn {label}!", show_alert=False)
            if any(c.get("is_manual_script") for c in session.get("comments", [])):
                ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
            else:
                ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data.startswith("upload_sfx:"):
            idx = int(data.split(":", 1)[1])
            session["awaiting_input"] = f"custom_sfx:{idx}"
            await query.answer()
            await query.message.reply_text(
                f"📤 <b>GỬI FILE ÂM THANH CHO DÒNG #{idx + 1}</b>\n"
                f"• Nội dung: <i>{h(session['comments'][idx].get('content', ''))}</i>\n\n"
                "👉 Hãy gửi file âm thanh (.mp3, .wav) hoặc gửi tin nhắn thoại (Voice note) vào đây. "
                "Bot sẽ tự động chuẩn hóa và gắn làm SFX riêng cho dòng này!",
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("set_v_ws:"):
            v_id = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "voice", v_id)
            await query.answer(f"🎙 Đã bật giọng: {get_voice_label(v_id)}")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:add_comment":
            session["awaiting_input"] = "add_comment"
            await query.answer()
            await query.message.reply_text(
                "✍️ <b>NHẬP BÌNH LUẬN MỚI</b>\n"
                "Hãy gửi 1 hoặc nhiều dòng bình luận vào khung chat (mỗi dòng sẽ trở thành 1 bình luận riêng):",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "ws:edit_post":
            session["awaiting_input"] = "edit_post"
            await query.answer()
            await query.message.reply_text(
                "✏️ <b>SỬA NỘI DUNG BÀI ĐĂNG GỐC</b>\n"
                "Hãy nhập nội dung mới cho bài đăng mở đầu video:",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "ws:attach_meme_menu":
            await query.answer()
            session["awaiting_input"] = None
            kb = []
            for idx, c in enumerate(session.get("comments", [])[:24]):
                has_m = "🖼 " if (c.get("custom_meme_path") or c.get("meme_image_path")) else ""
                c_name = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
                content_snip = c.get("content", "").replace("\n", " ")[:24]
                kb.append(
                    [
                        InlineKeyboardButton(
                            f"{has_m}#{idx + 1} {c_name[:14]}: {content_snip}",
                            callback_data=f"ask_meme_action:{idx}",
                        )
                    ]
                )
            has_script = any(c.get("is_manual_script") for c in session.get("comments", []))
            if has_script:
                kb.append([InlineKeyboardButton("🔙 Quay lại Kịch Bản", callback_data="ws:script_review")])
            kb.append(
                [InlineKeyboardButton("🔙 Quay lại Bảng Điều Khiển", callback_data="ws:show")]
            )
            await self._safe_edit(
                query.message,
                "🖼 <b>CHỌN DÒNG CẦN GẮN HOẶC ĐỔI ẢNH MEME DƯỚI DÒNG:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>Bấm chọn 1 dòng ở dưới để gửi ảnh từ máy lên hoặc chọn sticker vui nhộn có sẵn:</i>\n"
                "<i>(Bạn cũng có thể gửi ảnh trực tiếp vào chat kèm chú thích ví dụ: <code>meme 2</code> hoặc <code>dòng 2</code>)</i>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("ask_meme_action:"):
            idx = int(data.split(":")[1])
            await query.answer()
            session["awaiting_input"] = None
            if idx < 0 or idx >= len(session.get("comments", [])):
                return
            c = session["comments"][idx]
            has_m = bool(c.get("custom_meme_path") or c.get("meme_image_path"))
            c_name = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
            content_snip = c.get("content", "").replace("\n", " ")
            if len(content_snip) > 55:
                content_snip = content_snip[:52] + "..."

            kb = [
                [
                    InlineKeyboardButton(
                        "📤 Tải Ảnh Meme Riêng Từ Máy Lên",
                        callback_data=f"ask_meme_upload:{idx}",
                    )
                ],
                [
                    InlineKeyboardButton("🤣 Cười 3D", callback_data=f"set_preset_meme:{idx}:meme_laugh.png"),
                    InlineKeyboardButton("😭 Khóc 3D", callback_data=f"set_preset_meme:{idx}:meme_cry.png"),
                    InlineKeyboardButton("😱 Sốc 3D", callback_data=f"set_preset_meme:{idx}:meme_shock.png"),
                ],
                [
                    InlineKeyboardButton("🤡 Chú Hề", callback_data=f"set_preset_meme:{idx}:meme_clown.png"),
                    InlineKeyboardButton("🐱 Mèo Cười", callback_data=f"set_preset_meme:{idx}:laughing_cat.png"),
                    InlineKeyboardButton("🔥 Quá Cháy", callback_data=f"set_preset_meme:{idx}:meme_fire.png"),
                    InlineKeyboardButton("💀 Bất Lực", callback_data=f"set_preset_meme:{idx}:meme_dead.png"),
                ],
            ]
            if has_m:
                kb.append(
                    [
                        InlineKeyboardButton(
                            "🗑 Gỡ Bỏ Ảnh Meme Hiện Tại",
                            callback_data=f"clear_meme:{idx}",
                        )
                    ]
                )
            kb.append(
                [
                    InlineKeyboardButton(
                        "🔙 Quay lại danh sách dòng", callback_data="ws:attach_meme_menu"
                    )
                ]
            )
            has_script = any(c.get("is_manual_script") for c in session.get("comments", []))
            if has_script:
                kb.append([InlineKeyboardButton("🔙 Quay lại Kịch Bản", callback_data="ws:script_review")])

            status_str = "🖼️ Đang có ảnh Meme / Sticker bên dưới" if has_m else "⚪ Chưa có ảnh Meme"
            await self._safe_edit(
                query.message,
                f"🖼 <b>TÙY CHỈNH ẢNH MEME CHO DÒNG #{idx + 1} ({h(c_name)}):</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💬 Nội dung: <i>{h(content_snip)}</i>\n"
                f"📌 Trạng thái: <b>{status_str}</b>\n\n"
                "👉 Bạn có thể <b>Tải ảnh meme riêng</b> từ máy lên hoặc chọn nhanh <b>Sticker 3D có sẵn</b>:",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("set_preset_meme:"):
            _, idx_s, meme_file = data.split(":", 2)
            idx = int(idx_s)
            if 0 <= idx < len(session.get("comments", [])):
                m_path = ASSETS_DIR / "memes" / meme_file
                if m_path.exists():
                    session["comments"][idx]["custom_meme_path"] = str(m_path)
                    session["comments"][idx]["meme_image_path"] = str(m_path)
                    session["comments"][idx]["meme_reaction"] = meme_file
                await query.answer(f"Đã gắn meme {meme_file} cho dòng #{idx + 1}!")
            if any(c.get("is_manual_script") for c in session.get("comments", [])):
                ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
            else:
                ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data.startswith("ask_meme_upload:"):
            idx = int(data.split(":")[1])
            session["awaiting_input"] = f"custom_meme:{idx}"
            await query.answer()
            c = session["comments"][idx]
            c_name = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
            content_snip = c.get("content", "").replace("\n", " ")
            if len(content_snip) > 55:
                content_snip = content_snip[:52] + "..."
            kb = [
                [InlineKeyboardButton("🔙 Hủy / Chọn Dòng Khác", callback_data="ws:attach_meme_menu")],
                [InlineKeyboardButton("🔙 Quay lại Kịch Bản", callback_data="ws:script_review")]
            ]
            await self._safe_edit(
                query.message,
                f"🖼 <b>ĐANG CHỜ BẠN GỬI ẢNH MEME CHO DÒNG #{idx + 1} ({h(c_name)})</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💬 Dòng tin nhắn: <i>{h(content_snip)}</i>\n\n"
                "👉 <b>Hãy gửi 1 tấm ảnh meme</b> (ảnh chế mèo, reaction hài hước...) vào khung chat ngay bây giờ!\n\n"
                "Bot sẽ tự động lưu và gắn ảnh nằm ngay bên dưới dòng tin nhắn này trong video 9:16.\n\n"
                "<i>(Nếu bạn muốn đổi ý, bấm nút [🔙 Hủy / Chọn Dòng Khác] bên dưới)</i>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("clear_meme:"):
            idx = int(data.split(":")[1])
            if 0 <= idx < len(session["comments"]):
                session["comments"][idx]["custom_meme_path"] = None
                session["comments"][idx]["meme_image_path"] = None
                session["comments"][idx]["meme_reaction"] = "none"
            await query.answer("Đã gỡ ảnh meme của dòng!")
            if any(c.get("is_manual_script") for c in session.get("comments", [])):
                ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
            else:
                ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "ws:edit_meme_menu":
            await query.answer()
            kb = []
            for idx, c in enumerate(session.get("comments", [])[:24]):
                c_name = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
                kb.append(
                    [
                        InlineKeyboardButton(
                            f"#{idx + 1} {c_name[:18]} ({c.get('meme_reaction', 'none')} | {c.get('sfx_reaction', 'pop.wav')})",
                            callback_data=f"c_edit:{idx}",
                        )
                    ]
                )
            kb.append([InlineKeyboardButton("🔙 Quay lại Dự án", callback_data="ws:show")])
            await self._safe_edit(
                query.message,
                "🎭 <b>CHỌN DÒNG CẦN GẮN MEME / STICKER 3D / ÂM THANH SFX:</b>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("c_edit:"):
            idx = int(data.split(":")[1])
            await query.answer()
            c = session["comments"][idx]
            memes = self.meme_engine.get_available_memes()
            kb = []
            for m in memes:
                mark = "✅ " if c.get("meme_reaction") == m["id"] else ""
                kb.append(
                    [
                        InlineKeyboardButton(
                            f"{mark}{m['name']}",
                            callback_data=f"c_setm:{idx}:{m['id']}:{m['sfx']}",
                        )
                    ]
                )
            sfx_row = [
                InlineKeyboardButton("🔊 Pop", callback_data=f"c_sets:{idx}:pop.wav"),
                InlineKeyboardButton("🔊 Bruh", callback_data=f"c_sets:{idx}:bruh.wav"),
                InlineKeyboardButton(
                    "🔊 Boom", callback_data=f"c_sets:{idx}:vine_boom.wav"
                ),
                InlineKeyboardButton(
                    "🔊 Cười", callback_data=f"c_sets:{idx}:cartoon_laugh.wav"
                ),
            ]
            kb.append(sfx_row)
            kb.append([InlineKeyboardButton("🔙 Quay lại Dự án", callback_data="ws:show")])
            c_name = c.get("username") or c.get("speaker") or ("Phải" if c.get("align") == "right" else "Trái")
            await self._safe_edit(
                query.message,
                f"🎭 <b>TÙY CHỈNH BÌNH LUẬN #{idx + 1} ({h(c_name)})</b>\n"
                f"Nội dung: <i>{h(c.get('content', ''))}</i>\n"
                f"Sticker hiện tại: <code>{h(c.get('meme_reaction', 'none'))}</code> | SFX: <code>{h(c.get('sfx_reaction', 'pop.wav'))}</code>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("c_setm:"):
            _, idx_s, meme_id, sfx_id = data.split(":")
            idx = int(idx_s)
            if 0 <= idx < len(session["comments"]):
                session["comments"][idx]["meme_reaction"] = meme_id
                session["comments"][idx]["sfx_reaction"] = sfx_id
            await query.answer(f"Đã đặt Sticker: {meme_id}")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data.startswith("c_sets:"):
            _, idx_s, sfx_id = data.split(":")
            idx = int(idx_s)
            if 0 <= idx < len(session["comments"]):
                session["comments"][idx]["sfx_reaction"] = sfx_id
            await query.answer(f"Đã đặt âm thanh SFX: {sfx_id}")
            ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "menu:settings":
            await query.answer()
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:voice_menu":
            await query.answer()
            await self._send_voice_selector(query.message, user.id, edit=True)
            return

        if data.startswith("set_v:"):
            v_id = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "voice", v_id)
            await query.answer(f"🎙 Đã chọn: {get_voice_label(v_id)}")
            await self._send_voice_selector(query.message, user.id, edit=True)
            return

        if data == "cfg:test_tts":
            await query.answer("🔊 Đang tạo âm thanh nghe thử...")
            cfg = self.key_manager.get_user_settings(user.id, user.username or "")
            v_label = get_voice_label(cfg["voice"])
            sample_res = await self.tts_engine.synthesize_item(
                item_id=f"tg_prev_{uuid.uuid4().hex[:6]}",
                text="Anh Thơ cho em đi hát đám cưới chung đi, em hát nhạc G-Dragon cháy lắm luôn á!",
                voice="tiktok-BV075" if cfg["voice"] == "dialogue-auto" else cfg["voice"],
                rate=cfg["speech_rate"],
            )
            with open(sample_res["audio_path"], "rb") as af:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=af,
                    title=f"Nghe thử: {v_label}",
                    caption=f"🎙 Giọng đọc: <code>{h(v_label)}</code> | Tốc độ: <code>{h(cfg['speech_rate'])}</code>",
                    parse_mode=ParseMode.HTML,
                )
            return

        if data == "cfg:rate_menu":
            await query.answer()
            rates = ["-15%", "-5%", "+0%", "+10%", "+15%", "+25%"]
            kb = [
                [
                    InlineKeyboardButton(r, callback_data=f"set_rate:{r}")
                    for r in rates[:3]
                ],
                [
                    InlineKeyboardButton(r, callback_data=f"set_rate:{r}")
                    for r in rates[3:]
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Quay lại Cài đặt", callback_data="menu:settings"
                    )
                ],
            ]
            await self._safe_edit(
                query.message,
                "⚡ <b>CHỌN TỐC ĐỘ ĐỌC GIỌNG AI:</b>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("set_rate:"):
            rate_val = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "speech_rate", rate_val)
            await query.answer(f"Đã đặt tốc độ đọc: {rate_val}")
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:tpl_menu":
            await query.answer()
            kb = [
                [
                    InlineKeyboardButton(
                        "🤖 Tự động theo Link", callback_data="set_tpl:auto"
                    ),
                    InlineKeyboardButton(
                        "🧵 Threads (Chuẩn)", callback_data="set_tpl:threads"
                    ),
                ],
                [
                    InlineKeyboardButton("🎵 TikTok", callback_data="set_tpl:tiktok"),
                    InlineKeyboardButton("📘 Facebook", callback_data="set_tpl:facebook"),
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Quay lại Cài đặt", callback_data="menu:settings"
                    )
                ],
            ]
            await self._safe_edit(
                query.message,
                "🎨 <b>CHỌN MẪU GIAO DIỆN KHUNG BÌNH LUẬN:</b>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("set_tpl:"):
            tpl_val = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "platform", tpl_val)
            await query.answer(f"Đã chọn mẫu: {tpl_val.upper()}")
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:toggle_dark":
            cfg = self.key_manager.get_user_settings(user.id, user.username or "")
            new_val = not cfg["dark_mode"]
            self.key_manager.update_user_setting(user.id, "dark_mode", new_val)
            await query.answer(
                f"Đã chuyển sang: {'Dark Mode 🌙' if new_val else 'Light Mode ☀️'}"
            )
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:toggle_badge":
            cfg = self.key_manager.get_user_settings(user.id, user.username or "")
            new_val = not cfg["channel_badge"]
            self.key_manager.update_user_setting(user.id, "channel_badge", new_val)
            await query.answer(
                f"Huy hiệu góc phải: {'Bật ✅' if new_val else 'Tắt ❌'}"
            )
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:toggle_auto":
            cfg = self.key_manager.get_user_settings(user.id, user.username or "")
            new_val = not cfg["auto_render"]
            self.key_manager.update_user_setting(user.id, "auto_render", new_val)
            await query.answer(
                f"Chế độ: {'Tự động Render ⚡' if new_val else 'Duyệt trước khi Render 🛠'}"
            )
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:bg_menu":
            await query.answer()
            await self._send_bg_selector(query.message, user.id, edit=True)
            return

        if data.startswith("set_bg:"):
            bg_val = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "background_file", bg_val)
            await query.answer(f"Đã chọn video nền: {bg_val}")
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:bgm_menu":
            await query.answer()
            bgms = [f.name for f in (ASSETS_DIR / "bgm").glob("*.wav")] + [
                f.name for f in (ASSETS_DIR / "bgm").glob("*.mp3")
            ]
            kb = []
            for b in bgms:
                kb.append(
                    [InlineKeyboardButton(f"🎵 {b}", callback_data=f"set_bgm:{b}")]
                )
            kb.append(
                [InlineKeyboardButton("🔇 Tắt nhạc nền", callback_data="set_bgm:none")]
            )
            kb.append(
                [
                    InlineKeyboardButton(
                        "🔙 Quay lại Cài đặt", callback_data="menu:settings"
                    )
                ]
            )
            await self._safe_edit(
                query.message,
                "🎵 <b>CHỌN NHẠC NỀN (BGM CHẠY XUYÊN SUỐT VIDEO):</b>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("set_bgm:"):
            bgm_val = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "bgm_file", bgm_val)
            await query.answer(f"Đã chọn nhạc nền: {bgm_val}")
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data == "cfg:set_watermark":
            session["awaiting_input"] = "watermark"
            await query.answer()
            await query.message.reply_text(
                "🏷 <b>NHẬP CHỮ CHẠY (WATERMARK) MỚI</b>\n"
                "Hãy gửi tên kênh hoặc watermark bạn muốn hiển thị trên video (ví dụ: <code>@motvongthread1612</code>):",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "cfg:wm_mode_menu":
            await query.answer()
            kb = [
                [
                    InlineKeyboardButton(
                        "📌 Góc trái trên (Chuẩn @Vietcaigido?)",
                        callback_data="set_wmm:top_left",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✨ Bay lượn nhẹ quanh video",
                        callback_data="set_wmm:floating",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬆️ Chạy ngang phía trên", callback_data="set_wmm:marquee_top"
                    ),
                    InlineKeyboardButton(
                        "⬇️ Chạy ngang phía dưới",
                        callback_data="set_wmm:marquee_bottom",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Kết hợp cả hai", callback_data="set_wmm:both"
                    ),
                    InlineKeyboardButton(
                        "❌ Tắt chữ chạy", callback_data="set_wmm:none"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Quay lại Cài đặt", callback_data="menu:settings"
                    )
                ],
            ]
            await self._safe_edit(
                query.message,
                "🔄 <b>CHỌN KIỂU CHỮ CHẠY XUNG QUANH VIDEO:</b>",
                InlineKeyboardMarkup(kb),
            )
            return

        if data.startswith("set_wmm:"):
            wmm_val = data.split(":", 1)[1]
            self.key_manager.update_user_setting(user.id, "running_text_mode", wmm_val)
            await query.answer(f"Đã chọn kiểu chữ chạy: {wmm_val}")
            s_text, s_kb = self.build_settings_menu(user.id, user.username or "")
            await self._safe_edit(query.message, s_text, s_kb)
            return

        if data in ("ws:render_now", "act:render"):
            await query.answer("🎬 Đã đưa vào luồng Render tốc độ cao!")
            self._spawn_task(
                self._execute_video_render(
                    chat_id=query.message.chat_id,
                    user_id=user.id,
                    username=user.username or "",
                    status_message=query.message,
                )
            )
            return

        if data.startswith("act:bind_meme:"):
            idx = int(data.split(":")[2])
            pending_p = session.get("pending_meme_path")
            await query.answer()
            if pending_p and os.path.exists(pending_p) and 0 <= idx < len(session.get("comments", [])):
                session["comments"][idx]["custom_meme_path"] = str(pending_p)
                session["comments"][idx]["meme_image_path"] = str(pending_p)
                session["comments"][idx]["meme_reaction"] = "custom"
                session["pending_meme_path"] = None
                await query.message.reply_text(
                    f"✅ <b>Đã gắn ảnh meme vào bên dưới Dòng #{idx + 1} thành công!</b>",
                    parse_mode=ParseMode.HTML,
                )
            if any(c.get("is_manual_script") for c in session.get("comments", [])):
                ws_text, ws_kb = self.format_script_review_message(user.id, user.username or "")
            else:
                ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
            await self._safe_edit(query.message, ws_text, ws_kb)
            return

        if data == "act:ocr_pending_image":
            pending_p = session.get("pending_meme_path")
            await query.answer("Đang tiến hành tách tin nhắn từ ảnh...")
            session["pending_meme_path"] = None
            if pending_p and os.path.exists(pending_p):
                status_msg = await query.message.reply_text(
                    "📸 <b>Đang đọc chữ & tách tin nhắn từ ảnh...</b>\n⏳ <i>Vui lòng đợi giây lát...</i>",
                    parse_mode=ParseMode.HTML,
                )
                try:
                    slices = await asyncio.to_thread(
                        self.image_chat_engine.process_batch_images, [str(pending_p)]
                    )
                    if not slices:
                        await status_msg.edit_text(
                            "❌ <b>Không tìm thấy đoạn tin nhắn nào trong ảnh vừa gửi!</b>",
                            parse_mode=ParseMode.HTML,
                        )
                        return
                    session["mode"] = "image_chat"
                    session["chat_slices"] = [s.to_dict() for s in slices]
                    session["comments"] = []
                    for idx_s, s in enumerate(slices):
                        spk = s.speaker or ("Phải" if s.align == "right" else "Trái")
                        session["comments"].append(
                            {
                                "id": f"chat_slice_{idx_s}",
                                "username": spk,
                                "speaker": spk,
                                "align": s.align,
                                "content": s.spoken_text,
                                "card_image_path": s.image_path,
                                "meme_image_path": s.meme_image_path,
                                "custom_meme_path": None,
                                "selected": True,
                                "meme_reaction": "none",
                                "sfx_reaction": "pop.wav",
                                "is_chat_strip": True,
                            }
                        )
                    await status_msg.edit_text(
                        f"✅ <b>Đã tách thành công {len(slices)} dòng tin nhắn!</b>",
                        parse_mode=ParseMode.HTML,
                    )
                    ws_text, ws_kb = self.format_workspace_message(user.id, user.username or "")
                    await query.message.reply_text(ws_text, parse_mode=ParseMode.HTML, reply_markup=ws_kb)
                except Exception as e:
                    await status_msg.edit_text(f"❌ Lỗi tách tin nhắn: {h(e)}")
            return

    # ------------------------------------------------------------------
    # Full Video Rendering Pipeline & MP4 Delivery
    # ------------------------------------------------------------------
    async def _execute_video_render(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        status_message: Any,
    ) -> None:
        allowed, _ = self.key_manager.verify_user_access(user_id, username)
        if not allowed:
            await self._safe_edit(
                status_message,
                "🚫 Key của bạn đã bị vô hiệu hóa! Không thể thực hiện Render.",
            )
            return

        session = self.user_sessions.get(user_id)
        if not session:
            session = self.load_motvongthread_preset(user_id)

        is_image_chat = session.get("mode") == "image_chat"
        post = session.get("post")
        selected_comments = [
            c for c in session["comments"] if c.get("selected", True)
        ]
        if not selected_comments:
            empty_warn = (
                "⚠️ Bạn chưa có dòng tin nhắn nào được chọn! Hãy gửi 1 hoặc nhiều ảnh tin nhắn vào khung chat trước."
                if is_image_chat
                else "⚠️ Vui lòng chọn ít nhất 1 bình luận trước khi bấm Render Video!"
            )
            await self._safe_edit(
                status_message,
                empty_warn,
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 Quay lại Dự án", callback_data="ws:show"
                            )
                        ]
                    ]
                ),
            )
            return

        cfg = self.key_manager.get_user_settings(user_id, username)
        platform = (
            session.get("platform", "threads")
            if cfg["platform"] == "auto"
            else cfg["platform"]
        )

        t0 = time.monotonic()
        last_edit_ts = 0.0
        last_pct = -10
        loop = asyncio.get_running_loop()

        async def _push_progress(pct: int, detail: str, force: bool = False) -> None:
            nonlocal last_edit_ts, last_pct
            now = time.monotonic()
            if not force and (now - last_edit_ts < 1.25 or abs(pct - last_pct) < 4):
                return
            last_edit_ts = now
            last_pct = pct
            await self._safe_edit(
                status_message,
                self._make_progress_bar(pct, now - t0, detail),
            )

        try:
            await _push_progress(
                2, "Đang khởi tạo luồng xử lý & kiểm tra bản quyền...", force=True
            )

            async with self.render_semaphore:
                allowed_now, _ = self.key_manager.verify_user_access(user_id, username)
                if not allowed_now:
                    await self._safe_edit(
                        status_message,
                        "🚫 Key của bạn đã bị Admin thu hồi trong lúc chờ!",
                    )
                    return

                tts_input_items: List[Dict[str, Any]] = []
                if not is_image_chat and post:
                    tts_input_items.append(
                        {
                            "id": f"p_{post['id']}_{uuid.uuid4().hex[:4]}",
                            "text": post["content"],
                            "speaker": post.get("username", ""),
                            "align": "left",
                            "raw_item": post,
                            "is_op": True,
                        }
                    )
                voice_a = session.get("user_a_voice") or "vclip-adam-beta-3"
                voice_b = session.get("user_b_voice") or "vclip-adam-beta-2"

                for c in selected_comments:
                    align = c.get("align", "left")
                    c_voice = c.get("voice")
                    if not c_voice and is_image_chat:
                        c_voice = voice_b if align == "right" else voice_a
                    tts_input_items.append(
                        {
                            "id": f"c_{c['id']}_{uuid.uuid4().hex[:4]}",
                            "text": c["content"],
                            "speaker": c.get("speaker") or c.get("username", ""),
                            "align": align,
                            "voice": c_voice,
                            "raw_item": c,
                            "is_op": False,
                        }
                    )

                await _push_progress(
                    5,
                    f"1/3 Tổng hợp giọng đọc AI đa luồng (0/{len(tts_input_items)})...",
                    force=True,
                )

                async def _on_tts_prog(done: int, total: int) -> None:
                    pct = 5 + int((done / max(1, total)) * 27)
                    await _push_progress(
                        pct, f"1/3 Đang tạo giọng đọc AI ({done}/{total})..."
                    )

                timeline_items = await self.tts_engine.batch_synthesize(
                    items=tts_input_items,
                    voice=cfg["voice"],
                    rate=cfg["speech_rate"],
                    progress_callback=_on_tts_prog,
                )

                ordered_card_paths: List[Optional[str]] = [None] * len(timeline_items)
                card_specs: List[Dict[str, Any]] = []
                card_spec_positions: List[int] = []

                for idx_t, item_meta in enumerate(timeline_items):
                    matched = next(
                        x for x in tts_input_items if x["id"] == item_meta["id"]
                    )
                    raw = matched["raw_item"]
                    is_post_item = matched["is_op"]

                    pre_cap = raw.get("card_image_path")
                    if pre_cap and os.path.exists(pre_cap):
                        ordered_card_paths[idx_t] = pre_cap
                        item_meta["is_chat_strip"] = bool(
                            raw.get("is_chat_strip", True)
                        )
                    else:
                        card_spec_positions.append(idx_t)
                        card_specs.append(
                            {
                                "item_id": item_meta["id"],
                                "platform": platform,
                                "username": raw["username"],
                                "content": raw["content"],
                                "likes": raw.get("likes", "1,2K"),
                                "replies": raw.get("replies", 10),
                                "reposts": raw.get("reposts", "0"),
                                "shares": raw.get("shares", "0"),
                                "is_op": is_post_item,
                                "dark_mode": bool(cfg["dark_mode"]),
                                "avatar_url": raw.get("avatar_url"),
                                "image_url": raw.get("image_url"),
                            }
                        )

                    custom_meme = raw.get("custom_meme_path") or raw.get("meme_image_path")
                    if custom_meme and os.path.exists(custom_meme):
                        item_meta["meme_image_path"] = str(custom_meme)
                    else:
                        meme_file = raw.get("meme_reaction")
                        if meme_file and meme_file != "none":
                            m_path = ASSETS_DIR / "memes" / meme_file
                            if m_path.exists():
                                item_meta["meme_image_path"] = str(m_path)

                    custom_sfx = raw.get("custom_sfx_path")
                    if custom_sfx and os.path.exists(custom_sfx):
                        item_meta["sfx_path"] = str(custom_sfx)
                    else:
                        sfx_file = raw.get("sfx_reaction")
                        if sfx_file and sfx_file != "none":
                            s_path = ASSETS_DIR / "sfx" / sfx_file
                            if s_path.exists():
                                item_meta["sfx_path"] = str(s_path)

                if card_specs:
                    await _push_progress(
                        33,
                        f"2/3 Đang chụp Cap bình luận chuẩn Threads (0/{len(card_specs)})...",
                        force=True,
                    )

                    def _on_card_prog(done: int, total: int) -> None:
                        pct = 33 + int((done / max(1, total)) * 20)
                        asyncio.run_coroutine_threadsafe(
                            _push_progress(
                                pct, f"2/3 Đang chụp Cap bình luận ({done}/{total})..."
                            ),
                            loop,
                        )

                    rendered_list = await asyncio.to_thread(
                        self.renderer.render_cards_parallel,
                        card_specs,
                        _on_card_prog,
                    )
                    for pos, r_path in zip(card_spec_positions, rendered_list):
                        ordered_card_paths[pos] = r_path
                else:
                    await _push_progress(
                        52,
                        f"2/3 Đã sẵn sàng {len(ordered_card_paths)} ảnh Cap tin nhắn theo đúng thứ tự!",
                        force=True,
                    )

                card_image_paths: List[str] = [
                    str(p) for p in ordered_card_paths if p
                ]

                await _push_progress(
                    55,
                    "3/3 Đang ghép Video MP4 9:16 + Nhạc nền + SFX...",
                    force=True,
                )

                bg_path = self.default_assets["background"]
                if cfg["background_file"] and "img_1149" not in cfg["background_file"].lower():
                    custom_bg = ASSETS_DIR / "backgrounds" / cfg["background_file"]
                    if custom_bg.exists():
                        bg_path = str(custom_bg)

                bgm_path = None
                if is_image_chat:
                    # In chat video mode, background is 100% silent by default (no background video noise or BGM music)
                    # unless user explicitly selected a specific non-default BGM file
                    if cfg["bgm_file"] and cfg["bgm_file"] not in ("none", "meme_tiktok_viral.wav"):
                        custom_bgm = ASSETS_DIR / "bgm" / cfg["bgm_file"]
                        if custom_bgm.exists():
                            bgm_path = str(custom_bgm)
                else:
                    if cfg["bgm_file"] and cfg["bgm_file"] != "none":
                        custom_bgm = ASSETS_DIR / "bgm" / cfg["bgm_file"]
                        if custom_bgm.exists():
                            bgm_path = str(custom_bgm)
                        else:
                            bgm_path = self.default_assets["bgm"]

                badge_path = None
                if cfg["channel_badge"]:
                    custom_badge = ASSETS_DIR / "badges" / "drama_badge.png"
                    if custom_badge.exists():
                        badge_path = str(custom_badge)

                def _on_ffmpeg_prog(ratio: float) -> None:
                    pct = 55 + int(min(1.0, max(0.0, ratio)) * 41)
                    asyncio.run_coroutine_threadsafe(
                        _push_progress(
                            pct,
                            f"3/3 Đang xuất Video MP4 ({int(ratio * 100)}% FFmpeg)...",
                        ),
                        loop,
                    )

                video_filename = f"tg_video_{uuid.uuid4().hex[:8]}.mp4"
                final_video_path = await asyncio.to_thread(
                    self.compositor.compose_video,
                    timeline_items=timeline_items,
                    card_image_paths=card_image_paths,
                    background_video_path=bg_path,
                    bgm_path=bgm_path,
                    default_sfx_path=self.default_assets["pop"],
                    channel_badge_path=badge_path,
                    output_filename=video_filename,
                    bgm_volume=float(cfg["bgm_volume"]),
                    sfx_volume=float(cfg["sfx_volume"]),
                    running_text=cfg["running_text"],
                    running_text_mode=cfg["running_text_mode"],
                    width=1080,
                    height=1920,
                    progress_callback=_on_ffmpeg_prog,
                )

            self.key_manager.increment_video_count(user_id)
            total_dur = timeline_items[-1]["end_time"]
            render_elapsed = time.monotonic() - t0

            file_mb = os.path.getsize(final_video_path) / (1024 * 1024)
            await _push_progress(
                98,
                f"Đã nén chuẩn HD ({file_mb:.1f} MB)! Đang bắn tốc độ cao lên Telegram...",
                force=True,
            )

            try:
                await self.app.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO
                )
            except Exception:
                pass

            caption = (
                f"🎬 <b>VIDEO ĐÃ XUẤT THÀNH CÔNG! (100%)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏱ <b>Thời lượng:</b> <code>{total_dur:.1f}s</code> | ⚡ <b>Render:</b> <code>{render_elapsed:.1f}s</code> | 📦 <code>{file_mb:.1f} MB</code>\n"
                f"🎙 <b>Giọng đọc:</b> <code>{h(get_voice_label(cfg['voice']))}</code> ({h(cfg['speech_rate'])})\n"
                f"🖼 <b>Video nền:</b> <code>{h(cfg['background_file'])}</code>\n"
                f"🏷 <b>Watermark:</b> <code>{h(cfg['running_text'])}</code>\n"
                f"👑 <b>Hệ thống:</b> @{h(ADMIN_USERNAME)}"
            )

            await self._upload_video_fast_ipv4(
                chat_id=chat_id,
                video_path=final_video_path,
                caption=caption,
                width=720,
                height=1280,
            )

            if is_image_chat:
                session["rendered_completed"] = True

            ws_text, ws_kb = self.format_workspace_message(user_id, username)
            await self._safe_edit(
                status_message,
                f"✅ <b>Đã gửi video thành công!</b>\n\n{ws_text}",
                ws_kb,
            )

        except Exception as e:
            logger.exception("Error rendering video for Telegram user")
            await self._safe_edit(
                status_message,
                f"❌ <b>Lỗi khi Render Video:</b> <code>{h(e)}</code>",
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔙 Quay lại Dự án", callback_data="ws:show"
                            )
                        ]
                    ]
                ),
            )

    async def _upload_video_fast_ipv4(
        self,
        chat_id: int,
        video_path: str,
        caption: str,
        width: int = 720,
        height: int = 1280,
    ) -> None:
        """
        Ultra-fast IPv4 multipart video uploader via native 64-bit Windows curl.exe (Schannel + Winsock autotuning),
        bypassing Python 32-bit SSL chunk bottlenecks and ISP IPv6 PMTU blackholes.
        Automatically falls back to IPv4-bound HTTPX if curl.exe is unavailable.
        """
        curl_exe = (
            r"C:\Windows\System32\curl.exe"
            if os.name == "nt"
            else (shutil.which("curl") or "/usr/bin/curl")
        )
        last_err: Optional[Exception] = None

        if curl_exe and os.path.exists(curl_exe) and self._bot_token:
            api_url = f"https://api.telegram.org/bot{self._bot_token}/sendVideo"
            fd, cap_file = tempfile.mkstemp(suffix=".txt", prefix="tg_cap_")
            os.close(fd)
            try:
                Path(cap_file).write_text(caption, encoding="utf-8")
                for attempt in range(3):
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            curl_exe,
                            "-4",
                            "--http1.1",
                            "-s",
                            "-S",
                            "--connect-timeout",
                            "10",
                            "--speed-limit",
                            "35000",
                            "--speed-time",
                            "8",
                            "--max-time",
                            "75",
                            "-F",
                            f"chat_id={chat_id}",
                            "-F",
                            f"video=@{video_path};type=video/mp4",
                            "-F",
                            f"caption=<{cap_file}",
                            "-F",
                            "parse_mode=HTML",
                            "-F",
                            "supports_streaming=true",
                            "-F",
                            f"width={width}",
                            "-F",
                            f"height={height}",
                            api_url,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout_b, stderr_b = await proc.communicate()
                        if proc.returncode == 0 and stdout_b:
                            resp = json.loads(stdout_b.decode("utf-8", errors="replace"))
                            if resp.get("ok"):
                                return
                            raise RuntimeError(
                                f"Telegram API response error: {resp.get('description', resp)}"
                            )
                        err_txt = stderr_b.decode("utf-8", errors="replace").strip()
                        raise RuntimeError(
                            f"curl exit {proc.returncode}: {err_txt or 'upload aborted'}"
                        )
                    except Exception as c_exc:
                        last_err = c_exc
                        logger.warning(
                            f"Fast IPv4 curl sendVideo attempt {attempt + 1}/3 warning: {c_exc}"
                        )
                        await asyncio.sleep(0.8 * (attempt + 1))
            finally:
                try:
                    os.remove(cap_file)
                except Exception:
                    pass

        # Fallback: Strict IPv4 HTTPX upload with hard wall-clock timeout
        for upload_attempt in range(3):
            try:
                with open(video_path, "rb") as vf:
                    await asyncio.wait_for(
                        self.app.bot.send_video(
                            chat_id=chat_id,
                            video=vf,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                            width=width,
                            height=height,
                            read_timeout=45,
                            write_timeout=45,
                            connect_timeout=12,
                        ),
                        timeout=60.0,
                    )
                return
            except Exception as up_exc:
                last_err = up_exc
                logger.warning(
                    f"Fallback HTTPX send_video attempt {upload_attempt + 1}/3 failed: {up_exc}"
                )
                await asyncio.sleep(1.0 * (upload_attempt + 1))

        if last_err is not None:
            raise last_err

    async def _error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logger.error("Telegram Bot exception:", exc_info=context.error)

    # ------------------------------------------------------------------
    # Lifecycle Management (Start / Stop Polling)
    # ------------------------------------------------------------------
    async def start_polling(self, token: str) -> Tuple[bool, str]:
        clean_token = token.strip()
        if not clean_token:
            return False, "Token không được để trống."

        if self.is_running and self.app:
            await self.stop_polling()

        try:
            # Force strict IPv4 (0.0.0.0) on all Telegram API requests to eliminate IPv6 PMTU stalls
            api_req = HTTPXRequest(
                connection_pool_size=32,
                read_timeout=30.0,
                write_timeout=30.0,
                media_write_timeout=45.0,
                connect_timeout=10.0,
                pool_timeout=15.0,
                http_version="1.1",
                httpx_kwargs={
                    "transport": httpx.AsyncHTTPTransport(
                        local_address="0.0.0.0", retries=2
                    )
                },
            )
            poll_req = HTTPXRequest(
                connection_pool_size=8,
                read_timeout=25.0,
                write_timeout=15.0,
                connect_timeout=10.0,
                pool_timeout=15.0,
                http_version="1.1",
                httpx_kwargs={
                    "transport": httpx.AsyncHTTPTransport(
                        local_address="0.0.0.0", retries=2
                    )
                },
            )

            app = (
                ApplicationBuilder()
                .token(clean_token)
                .concurrent_updates(True)
                .request(api_req)
                .get_updates_request(poll_req)
                .build()
            )

            app.add_handler(TypeHandler(Update, self._track_update_messages), group=-1)
            app.add_handler(CommandHandler("start", self.cmd_start))
            app.add_handler(CommandHandler("help", self.cmd_start))
            app.add_handler(CommandHandler("key", self.cmd_key))
            app.add_handler(CommandHandler("admin", self.cmd_admin))
            app.add_handler(CommandHandler(["clearchat", "clean"], self.cmd_clearchat))
            app.add_handler(CommandHandler("genkey", self.cmd_genkey))
            app.add_handler(CommandHandler("delkey", self.cmd_delkey))
            app.add_handler(CommandHandler("settings", self.cmd_settings))
            app.add_handler(CommandHandler("watermark", self.cmd_watermark))
            app.add_handler(CallbackQueryHandler(self.handle_callback))
            app.add_handler(
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE | filters.Sticker.ALL,
                    self.handle_photo_upload,
                )
            )
            app.add_handler(
                MessageHandler(
                    filters.VIDEO | filters.Document.VIDEO, self.handle_video_upload
                )
            )
            app.add_handler(
                MessageHandler(
                    filters.AUDIO | filters.VOICE | filters.Document.AUDIO,
                    self.handle_audio_upload,
                )
            )
            app.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, self.handle_text_message
                )
            )
            app.add_error_handler(self._error_handler)

            await app.initialize()
            bot_info = await app.bot.get_me()
            self.bot_username = bot_info.username or "SocialVideoBot"

            await app.start()
            await app.updater.start_polling(drop_pending_updates=False)

            self.app = app
            self._bot_token = clean_token
            self.is_running = True
            self._spawn_task(self._periodic_cleanup_loop())
            if not os.environ.get("TELEGRAM_BOT_TOKEN"):
                self.key_manager.set_config("telegram_bot_token", clean_token)
            logger.info(
                f"Telegram Bot @{self.bot_username} started polling successfully!"
            )
            return True, f"Đã khởi chạy thành công Bot @{self.bot_username}!"
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")
            self.is_running = False
            self.app = None
            return False, f"Không thể khởi động Bot (Kiểm tra lại Token từ @BotFather): {e}"

    async def stop_polling(self) -> None:
        if self.app and self.is_running:
            try:
                await self.app.updater.stop()
                await self.app.stop()
                await self.app.shutdown()
            except Exception as e:
                logger.warning(f"Error stopping bot: {e}")
        self.is_running = False
        self.app = None
        self.bot_username = ""
        self._bot_token = ""
