"""
Humor & Meme Intelligence Engine for Vietnamese Social Video Automation.
Detects humor, emotions, irony, and automatically pairs comments with
viral reaction stickers (3D Fluent Emojis), meme sound effects (Vine Boom, Bruh, Cartoon Laugh),
and automatically selects the optimal meme background music.
"""

import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Emotion & Humor Keyword Lexicon
FUNNY_KEYWORDS = [
    r"=+\)+", r"haha+", r"kaka+", r"cười", r"hài", r"nhặt mồm", r"xỉu",
    r"vãi", r"vcl", r"vkl", r"chết tiệt", r"chết cười", r"ngô", r"ngáo",
    r"😂", r"🤣", r"😆"
]

SAD_IRONIC_KEYWORDS = [
    r"1 sao", r"không cam tâm", r"nhỏ", r"khóc", r"buồn", r"cay", r"chán",
    r"tệ", r"siêu tệ", r"không xứng", r"chia tay", r"thất vọng", r"hận",
    r"😭", r"🥲", r"🥺", r"💔"
]

SHOCK_KEYWORDS = [
    r"á đù", r"kinh vậy", r"sốc", r"gì vậy", r"clgt", r"cái quái", r"ảo thật",
    r"bất ngờ", r"tận thế", r"độn thổ", r"😱", r"🤯"
]

CLOWN_KEYWORDS = [
    r"chú hề", r"môn đăng hộ đối", r"thầy bói", r"phong thủy", r"vô lý",
    r"hợp lý", r"bạn thân", r"nuôi cá", r"🤡"
]

DEAD_KEYWORDS = [
    r"bất lực", r"toang", r"hỏng hẳn", r"xu cà na", r"hết cứu", r"💀"
]

class MemeEngine:
    def __init__(self, assets_dir: str):
        self.assets_dir = assets_dir

    def detect_comment_reaction(self, text: str) -> Dict[str, Optional[str]]:
        """
        Analyzes comment text and returns optimal meme sound effect (SFX)
        and optional aesthetic 3D reaction sticker.
        By default, keeps videos clean (meme_file=None) unless requested.
        """
        lower = text.lower()

        # 1. Sad / Ironic (1-star review humor) -> Bruh SFX
        for pat in SAD_IRONIC_KEYWORDS:
            if re.search(pat, lower):
                return {
                    "meme_file": None,
                    "sfx_file": "bruh.wav",
                    "emotion": "sad_ironic",
                    "label": "😭 Hài hước trớ trêu (Bruh SFX)"
                }

        # 2. Absurd / Shock -> Vine Boom
        for pat in SHOCK_KEYWORDS:
            if re.search(pat, lower):
                return {
                    "meme_file": None,
                    "sfx_file": "vine_boom.wav",
                    "emotion": "shock",
                    "label": "😱 Sốc giật mình (Vine Boom SFX)"
                }

        # 3. Clown / Absurd reason -> Bruh
        for pat in CLOWN_KEYWORDS:
            if re.search(pat, lower):
                return {
                    "meme_file": None,
                    "sfx_file": "bruh.wav",
                    "emotion": "clown",
                    "label": "🤡 Châm biếm cạn lời (Bruh SFX)"
                }

        # 4. Dead / Helpless -> Vine Boom
        for pat in DEAD_KEYWORDS:
            if re.search(pat, lower):
                return {
                    "meme_file": None,
                    "sfx_file": "vine_boom.wav",
                    "emotion": "dead",
                    "label": "💀 Bất lực toang (Vine Boom SFX)"
                }

        # 5. Funny / Laughing -> Cartoon Laugh
        for pat in FUNNY_KEYWORDS:
            if re.search(pat, lower):
                return {
                    "meme_file": None,
                    "sfx_file": "cartoon_laugh.wav",
                    "emotion": "funny",
                    "label": "🤣 Cười hả hê =))) (Cartoon Laugh SFX)"
                }

        # Default fallback -> clean pop sound
        return {
            "meme_file": None,
            "sfx_file": "pop.wav",
            "emotion": "neutral",
            "label": "Bình thường (Pop SFX)"
        }

    def auto_select_bgm(self, post_text: str, comments_text: List[str]) -> Tuple[str, str]:
        """
        Scans entire thread content to automatically pick the most fitting meme BGM.
        Returns (bgm_filename, reason).
        """
        combined = (post_text + " " + " ".join(comments_text)).lower()

        # Check for meme / viral indicators
        meme_score = sum(1 for pat in (FUNNY_KEYWORDS + SAD_IRONIC_KEYWORDS + CLOWN_KEYWORDS) if re.search(pat, combined))

        if meme_score >= 1:
            return "meme_tiktok_viral.wav", "Tự động chọn nhạc Meme TikTok Viral theo nội dung bài viết"
        else:
            return "chill_bgm.wav", "Tự động chọn nhạc Lofi Chill thư giãn"

    def parse_review_card(self, text: str) -> Optional[Dict[str, str]]:
        """
        Checks if text contains an embedded 1-star review format
        and parses out details for the inner review card template.
        """
        if "⭐" in text or "★" in text or "1 sao" in text.lower():
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            author = "Người dùng"
            date = "gần đây"
            rev_text = text
            helpful = "Hữu ích"

            for l in lines:
                if "⭐" in l or "★" in l:
                    # extract author/date if format: ⭐☆☆☆☆ Quân Nguyễn (14/8/25):
                    m = re.search(r"[⭐★☆]+\s*([^(:]+)(?:\(([^)]+)\))?", l)
                    if m:
                        author = m.group(1).strip()
                        if m.group(2):
                            date = m.group(2).strip()
                elif "hữu ích" in l.lower():
                    helpful = l
                elif not l.startswith("Đánh giá"):
                    rev_text = l.replace('"', '').strip()

            return {
                "review_author": author,
                "review_date": date,
                "review_text": rev_text,
                "review_helpful": helpful,
                "review_initial": author[0].upper() if author else "Q"
            }
        return None

    def get_available_memes(self) -> List[Dict[str, str]]:
        """Returns list of modern 3D Fluent sticker templates for UI dropdown."""
        return [
            {"id": "none", "name": "❌ Không hiện sticker (Tối giản chuẩn TikTok)", "sfx": "pop.wav"},
            {"id": "meme_laugh.png", "name": "🤣 Emoji Cười 3D Fluent", "sfx": "cartoon_laugh.wav"},
            {"id": "meme_cry.png", "name": "😭 Emoji Khóc lóc 3D Fluent", "sfx": "bruh.wav"},
            {"id": "meme_clown.png", "name": "🤡 Emoji Chú hề 3D Fluent", "sfx": "bruh.wav"},
            {"id": "meme_dead.png", "name": "💀 Emoji Bất lực 3D Fluent", "sfx": "vine_boom.wav"},
            {"id": "meme_shock.png", "name": "😱 Emoji Sốc giật mình 3D Fluent", "sfx": "vine_boom.wav"},
            {"id": "meme_fire.png", "name": "🔥 Emoji Quá cháy 3D Fluent", "sfx": "pop.wav"},
            {"id": "laughing_cat.png", "name": "🐱 Sticker Mèo cười", "sfx": "cartoon_laugh.wav"}
        ]

    def get_available_sfx(self) -> List[Dict[str, str]]:
        """Returns catalog of viral SFX sound effects from assets/sfx."""
        assets_p = Path(self.assets_dir)
        cat_file = assets_p / "sfx" / "sfx_catalog.json"
        if cat_file.exists():
            try:
                import json
                items = json.loads(cat_file.read_text(encoding="utf-8"))
                res = [{"id": "none", "name": "🔇 Tắt âm (Không có tiếng SFX)", "file": "none"}]
                for it in items:
                    res.append({"id": it["file"], "name": it["name"], "file": it["file"]})
                return res
            except Exception:
                pass

        # Fallback to directory listing
        sfx_dir = assets_p / "sfx"
        res = [{"id": "none", "name": "🔇 Tắt âm (Không có tiếng SFX)", "file": "none"}]
        if sfx_dir.exists():
            for p in sorted(sfx_dir.glob("*.*")):
                if p.suffix.lower() in (".wav", ".mp3"):
                    res.append({"id": p.name, "name": f"🔊 {p.stem.replace('_', ' ').title()}", "file": p.name})
        return res

    def get_sfx_name(self, sfx_id: str) -> str:
        """Returns friendly display label for a sound effect ID."""
        if not sfx_id or sfx_id == "none":
            return "🔇 Tắt âm"
        for item in self.get_available_sfx():
            if item["id"] == sfx_id or item["file"] == sfx_id:
                return item["name"]
        return f"🔊 {sfx_id}"

if __name__ == "__main__":
    engine = MemeEngine("")
    samples = [
        "cần phải đăng nhập mà tôi vẫn còn nhỏ nên ko biết đăng nhập",
        "Tag nhầm sếp tổng vào nhóm chat nói xấu sếp cười ỉa =)))",
        "Thầy bói bảo anh mạng Hỏa em mạng Thủy nên chia tay",
    ]
    for s in samples:
        print(s, "->", engine.detect_comment_reaction(s))
