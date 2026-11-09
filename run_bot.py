"""
Standalone CLI Launcher for the Social Video Automation Telegram Bot.
Admin: @citywin2026
"""

import asyncio
import os
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from app.key_manager import KeyManager, ADMIN_USERNAME
from app.scraper import SocialScraper
from app.renderer import CardRenderer
from app.tts_engine import TTSEngine
from app.compositor import VideoCompositor
from app.sfx_generator import ensure_default_assets
from app.meme_engine import MemeEngine
from app.telegram_bot import TelegramVideoBotService


async def main():
    print("=" * 65)
    print("🤖 SOCIAL VIDEO AUTOMATION TELEGRAM BOT")
    print(f"👑 Admin toàn quyền: @{ADMIN_USERNAME}")
    print("🔒 Bảo mật: 1 Key = 1 Người dùng | Xóa Key = Vô hiệu hóa tức thì")
    print("=" * 65)

    default_assets = ensure_default_assets(str(PROJECT_ROOT))
    key_manager = KeyManager(db_path=str(PROJECT_ROOT / "data" / "bot_auth.db"))

    # Ensure at least one initial key exists
    existing_keys = key_manager.list_keys()
    if not existing_keys:
        first_key = key_manager.create_key(
            note="Key mặc định ban đầu", created_by=f"@{ADMIN_USERNAME}"
        )
        print(f"🔑 Đã tạo Key đầu tiên: {first_key['key_code']}")
    else:
        print(f"🔑 Số lượng Key hiện có trong hệ thống: {len(existing_keys)}")
        for k in existing_keys[:5]:
            status = (
                f"Đã gắn với ID {k['bound_user_id']} (@{k['bound_username']})"
                if k["bound_user_id"]
                else "Chưa kích hoạt"
            )
            print(f"   • {k['key_code']} -> {status}")

    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or key_manager.get_config("telegram_bot_token", "").strip()
    )

    if len(sys.argv) > 1 and sys.argv[1].strip():
        token = sys.argv[1].strip()

    if not token:
        print("\n⚠️ Chưa tìm thấy TELEGRAM_BOT_TOKEN.")
        token = input("👉 Nhập Telegram Bot Token (từ @BotFather): ").strip()

    if not token:
        print("❌ Bạn chưa nhập Bot Token. Thoát chương trình.")
        return

    scraper = SocialScraper()
    renderer = CardRenderer(
        templates_dir=str(PROJECT_ROOT / "templates"),
        output_dir=str(PROJECT_ROOT / "output" / "cards"),
    )
    tts_engine = TTSEngine(output_dir=str(PROJECT_ROOT / "output" / "tts"))
    compositor = VideoCompositor(output_dir=str(PROJECT_ROOT / "output" / "videos"))
    meme_engine = MemeEngine(assets_dir=str(PROJECT_ROOT / "assets"))

    bot_service = TelegramVideoBotService(
        key_manager=key_manager,
        scraper=scraper,
        renderer=renderer,
        tts_engine=tts_engine,
        compositor=compositor,
        meme_engine=meme_engine,
        default_assets=default_assets,
    )

    ok, msg = await bot_service.start_polling(token)
    if not ok:
        print(f"❌ {msg}")
        return

    print(f"\n✅ {msg}")
    print(f"👉 Truy cập Bot trên Telegram: https://t.me/{bot_service.bot_username}")
    print(f"👉 Admin @{ADMIN_USERNAME} có thể gõ lệnh: /admin, /genkey, /delkey")
    print(" nhấn Ctrl+C để dừng Bot.")
    print("=" * 65)

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        print("\n⏹ Đang dừng Telegram Bot...")
    finally:
        await bot_service.stop_polling()
        renderer.close()


if __name__ == "__main__":
    asyncio.run(main())
