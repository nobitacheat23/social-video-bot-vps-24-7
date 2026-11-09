"""
Smart Chat Image OCR & Automatic Top-to-Bottom Bubble Slicer Engine.
Supports:
1. Single-row pre-cropped chat message strips (reads text & preserves exact user image order)
2. Long/Full chat screenshots (Messenger, Zalo, Telegram, Instagram, iMessage, SMS - Light & Dark mode):
   Automatically detects and slices ('cap') individual message bubbles from Row #1 at the top
   down to Row #N at the bottom, separating sender-name headers from spoken message text!
3. Multi-image batch processing preserving strict user-submitted order.
4. Dual-Pass Tesseract OCR Engine with adaptive inverted thresholding for Dark Mode colored bubbles
   (purple, lavender, cyan, blue, green) and automatic OCR.space cloud fallback!
"""

from __future__ import annotations

import io
import os
import re
import shutil
import socket
import statistics
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageOps, ImageDraw, ImageFont
import requests
import urllib3.util.connection as urllib3_cn

from app.teencode import strip_dates_and_timestamps

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET


@dataclass
class OCRLine:
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_y(self) -> float:
        return self.top + self.height * 0.5


@dataclass
class ExtractedChatSlice:
    slice_id: str
    source_image: str
    order_index: int
    speaker: str
    spoken_text: str
    align: str  # "left" | "right" | "center"
    image_path: str
    width: int
    height: int
    is_meme_photo: bool = False
    meme_image_path: Optional[str] = None


STATUS_BAR_RE = re.compile(
    r"^(?:\d{1,2}:\d{2}(?:\s*(?:AM|PM|SA|CH))?|4G|5G|LTE|Wi-?Fi|\d{1,3}%|[\d.,]+\s*[KM]B/s|Viettel|VNPT|VinaPhone|MobiFone)$",
    re.IGNORECASE,
)

HEADER_NOISE_RE = re.compile(
    r"^(?:Đang hoạt động|Hoạt động\s+.*trước|Active\s+now|Active\s+.*ago|Tin nhắn|Chi tiết|Trang cá nhân)$",
    re.IGNORECASE,
)

FOOTER_INPUT_RE = re.compile(
    r"^(?:Nhắn tin\.{0,3}|Tin nhắn\.{0,3}|Nhập tin nhắn\.{0,3}|Nhập @,\s*tin nhắn\.{0,3}|Aa|Message\.{0,3}|Gửi|Send|Write a message\.{0,3})$",
    re.IGNORECASE,
)

STANDALONE_CHAT_META_RE = re.compile(
    r"^(?:"
    r"\d{1,2}:\d{2}(?:\s*(?:SA|CH|AM|PM))?"
    r"|(?:Hôm nay|Hôm qua|Today|Yesterday|Thứ\s*[2-7]|Th\s*[2-7]|Chủ nhật|CN)(?:[\s,•\-]+\d{1,2}:\d{2}(?:\s*(?:SA|CH|AM|PM))?)?"
    r"|(?:Đã xem|Đã gửi|Đã nhận|Đang gửi|Seen|Delivered|Sent)(?:\s+.*)?$"
    r"|\d{1,2}\s*(?:tháng|thg|/|-)\s*\d{1,2}(?:[\s,/,-]+\d{2,4})?(?:\s+lúc\s+\d{1,2}:\d{2})?"
    r")$",
    re.IGNORECASE,
)

CHAT_CONVERSATIONAL_WORDS = {
    "ơi", "oi", "ạ", "a", "nhé", "nhe", "nha", "nhá", "vậy", "vay", "sao",
    "đâu", "dau", "được", "duoc", "không", "khong", "k", "ko", "hông", "hong",
    "đi", "di", "coi", "xem", "hát", "hat", "thì", "thi", "nè", "ne", "luôn", "luon",
    "còn", "con", "gửi", "gui", "có", "co", "thêm", "them", "này", "nay", "kia",
    "bác", "bac", "ông", "ong", "bà", "ba", "anh", "chị", "chi", "em", "mình", "minh",
    "tôi", "toi", "tao", "mày", "may", "tớ", "to", "cậu", "cau", "hả", "ha", "hử", "hu",
    "thế", "the", "gì", "gi", "chi", "mấy", "may", "bao", "nhiêu", "nhieu", "v", "vs",
    "đang", "dang", "sẽ", "se", "rồi", "roi", "chưa", "chua", "đã", "da", "lại", "lai",
    "bank", "stk", "sdt", "zl", "zalo", "fb", "ib", "demo", "th", "mà", "ma", "giọng",
    "giong", "video", "clip", "bot", "auto", "cap", "bảo", "sếp", "oke", "ok", "dạ",
}


def is_conversational_chat_message(text: str) -> bool:
    """Returns True if the text represents conversational message content rather than a sender name header."""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"\d{7,}", s) or "http" in s.lower() or "tiktok" in s.lower():
        return True
    words = [w.lower().strip(".,?!:;-_/\\\"'()[]{}") for w in s.split()]
    return any(w in CHAT_CONVERSATIONAL_WORDS for w in words)


def _clean_ocr_line_text(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"[®©™¦¬¤§¶«»]+", "", s)
    s = re.sub(r"^[|¦•·»«@©®~_\-=+—]+\s*", "", s)
    s = re.sub(r"\s*[|¦©®~_—\-+=]+\s*$", "", s)
    s = re.sub(r"\s@\s", " ", s)
    # Strip trailing timestamps and OCR status ticks (e.g. "16:41", "19:38", ".¿ ..„", "x4;", ":ạ:42")
    s = re.sub(r"[\s,•\.]+(?:\d{1,2}:\d{2}(?:\s*(?:SA|CH|AM|PM))?|[¿\.\,„›\<\>~]+)+.*$", "", s)
    s = re.sub(r"\s+[-–—~¿\.\,„›\<\>]+.*$", "", s)
    s = re.sub(r"\s+[xXø]?\d{1,2}[;:\.\,\s].*$", "", s)
    s = re.sub(r"[\s,:•\.]+(?:[a-zA-ZÀ-ỹ\s]{1,3}:)?\d{1,2}(?:\s*(?:SA|CH|AM|PM))?$", "", s)
    if not re.search(r"^\d+$", s.strip()):
        s = re.sub(r"\s+\d{1,2}\s*(?:vớ|v|vv|vợ)?$", "", s)
        s = re.sub(r"\s+\d{1,2}$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class ImageChatEngine:
    """
    Extracts ordered chat message caps + clean Vietnamese TTS text from single-row or full-page chat images.
    """

    def __init__(self, output_dir: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.output_dir = Path(output_dir or (base_dir / "output" / "cards"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _find_tesseract() -> Optional[str]:
        t_cmd = shutil.which("tesseract")
        if t_cmd:
            return t_cmd
        win_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
        for wp in win_paths:
            if wp and os.path.exists(wp):
                return wp
        return None

    def _ocr_via_ocrspace(self, img: Image.Image) -> List[OCRLine]:
        """
        High-accuracy Vietnamese Neural OCR (OCR.space Engine 2) with automatic <900KB JPEG scaling
        and exact coordinate projection back to original image dimensions.
        """
        orig_w, orig_h = img.size
        max_dim = 2200
        scale = 1.0
        work_img = img
        if max(orig_w, orig_h) > max_dim:
            scale = max_dim / float(max(orig_w, orig_h))
            work_img = img.resize(
                (max(1, int(orig_w * scale)), max(1, int(orig_h * scale))),
                Image.Resampling.LANCZOS,
            )

        buf = io.BytesIO()
        quality = 88
        work_img.save(buf, format="JPEG", quality=quality, optimize=True)
        while buf.tell() > 920_000 and quality > 55:
            quality -= 12
            buf = io.BytesIO()
            work_img.save(buf, format="JPEG", quality=quality, optimize=True)
        buf.seek(0)

        api_key = os.environ.get("OCR_SPACE_API_KEY", "helloworld")
        resp = requests.post(
            "https://api.ocr.space/parse/image",
            data={
                "apikey": api_key,
                "OCREngine": "2",
                "isOverlayRequired": "true",
                "language": "auto",
                "scale": "true",
            },
            files={"file": ("chat.jpg", buf.getvalue(), "image/jpeg")},
            timeout=18,
        )
        if resp.status_code != 200:
            return []

        payload = resp.json()
        if payload.get("IsErroredOnProcessing"):
            return []

        inv_scale = 1.0 / scale
        lines: List[OCRLine] = []
        for pr in payload.get("ParsedResults") or []:
            overlay = pr.get("TextOverlay") or {}
            for ln in overlay.get("Lines") or []:
                txt = _clean_ocr_line_text(ln.get("LineText", ""))
                if not txt:
                    continue
                words = ln.get("Words") or []
                if words:
                    min_l = min(float(w.get("Left", 0)) for w in words)
                    min_t = min(float(w.get("Top", ln.get("MinTop", 0))) for w in words)
                    max_r = max(
                        float(w.get("Left", 0)) + float(w.get("Width", 0))
                        for w in words
                    )
                    max_b = max(
                        float(w.get("Top", 0)) + float(w.get("Height", ln.get("MaxHeight", 20)))
                        for w in words
                    )
                else:
                    min_l = 0.0
                    min_t = float(ln.get("MinTop", 0))
                    max_r = float(work_img.size[0])
                    max_b = min_t + float(ln.get("MaxHeight", 20))

                lines.append(
                    OCRLine(
                        text=txt,
                        left=max(0, int(round(min_l * inv_scale))),
                        top=max(0, int(round(min_t * inv_scale))),
                        width=max(4, int(round((max_r - min_l) * inv_scale))),
                        height=max(4, int(round((max_b - min_t) * inv_scale))),
                    )
                )
        return lines

    def _ocr_via_tesseract(self, img: Image.Image, img_path: str) -> List[OCRLine]:
        """
        Offline Vietnamese LSTM OCR via Tesseract 5 TSV output:
        1. Pass 1: High-resolution Lanczos upscale with PSM 3 (automatic multi-column chat segmentation).
        2. Pass 2: Inverted adaptive thresholding for Dark Mode colored bubbles (purple, lavender, cyan, green).
        3. Seamless deduplicated spatial merge between Pass 1 and Pass 2.
        """
        tess = self._find_tesseract()
        if not tess:
            return []

        orig_w, orig_h = img.size
        scale = 3.0 if orig_w < 600 else (2.0 if orig_w < 1200 else 1.0)
        inv_scale = 1.0 / scale

        def run_tsv_on_image(work_img: Image.Image, psm: int = 3) -> List[OCRLine]:
            tmp_p = str(self.output_dir / f"_ocr_{uuid.uuid4().hex[:8]}.png")
            try:
                work_img.save(tmp_p, format="PNG")
                cmd = [tess, tmp_p, "stdout", "-l", "vie", "--psm", str(psm), "tsv"]
                res = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
            finally:
                if os.path.exists(tmp_p):
                    try:
                        os.remove(tmp_p)
                    except Exception:
                        pass

            if res.returncode != 0 or not res.stdout:
                return []

            grouped: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
            for row in res.stdout.splitlines()[1:]:
                cols = row.split("\t")
                if len(cols) < 12:
                    continue
                try:
                    level = int(cols[0])
                    conf = float(cols[10])
                    word_txt = cols[11].strip()
                    if level != 5 or conf < 28 or not word_txt:
                        continue
                    l_px = int(cols[6]) * inv_scale
                    w_px = int(cols[8]) * inv_scale
                    h_px = int(cols[9]) * inv_scale
                    # Filter margin noise
                    if (l_px + w_px) < orig_w * 0.12 and conf < 82:
                        continue
                    if len(word_txt) <= 2 and conf < 68 and (h_px > 48 or w_px < 6):
                        continue
                    key = (int(cols[2]), int(cols[3]), int(cols[4]))
                    grouped.setdefault(key, []).append({
                        "text": word_txt,
                        "left": l_px,
                        "top": int(cols[7]) * inv_scale,
                        "width": max(3, w_px),
                        "height": max(3, h_px),
                        "conf": conf,
                    })
                except ValueError:
                    continue

            extracted: List[OCRLine] = []
            for _, words in grouped.items():
                if len(words) >= 2:
                    first_w, second_w = words[0], words[1]
                    if (
                        len(first_w["text"]) <= 2
                        and (second_w["left"] - (first_w["left"] + first_w["width"])) > 18
                        and first_w["left"] < orig_w * 0.14
                    ):
                        words = words[1:]

                full_txt = _clean_ocr_line_text(" ".join(w["text"] for w in words))
                if not full_txt or not re.search(r"[A-Za-z0-9À-ỹ]", full_txt):
                    continue
                min_l = int(round(min(w["left"] for w in words)))
                min_t = int(round(min(w["top"] for w in words)))
                max_r = int(round(max(w["left"] + w["width"] for w in words)))
                max_b = int(round(max(w["top"] + w["height"] for w in words)))
                extracted.append(
                    OCRLine(
                        text=full_txt,
                        left=min_l,
                        top=min_t,
                        width=max(4, max_r - min_l),
                        height=max(4, max_b - min_t),
                    )
                )
            return extracted

        # Pass 1: Standard Upscaled Image
        up_img = img.resize(
            (int(orig_w * scale), int(orig_h * scale)),
            Image.Resampling.LANCZOS,
        )
        lines_p1 = run_tsv_on_image(up_img, psm=3)
        if not lines_p1:
            lines_p1 = run_tsv_on_image(up_img, psm=6)

        # Detect Dark Mode background margin luminance
        gray = up_img.convert("L")
        edge_px: List[int] = []
        gw, gh = gray.size
        for y in range(0, gh, max(1, gh // 100)):
            for x in (0, 1, 2, max(0, gw - 3), max(0, gw - 2), max(0, gw - 1)):
                edge_px.append(gray.getpixel((x, y)))
        med_bg = float(statistics.median(edge_px)) if edge_px else 255.0
        is_dark_mode = med_bg < 125.0

        lines_p2: List[OCRLine] = []
        if is_dark_mode:
            thresh = int(max(med_bg + 45, 142))
            bin_img = gray.point(lambda p: 255 if p <= thresh else 0, mode="L")
            lines_p2 = run_tsv_on_image(bin_img, psm=3)

        if not lines_p2:
            lines_p1.sort(key=lambda x: (x.top, x.left))
            return lines_p1

        # Deduplicate & Merge Pass 1 and Pass 2
        def overlap_lines(a: OCRLine, b: OCRLine) -> bool:
            ov_h = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
            min_h = min(a.height, b.height)
            if min_h <= 0 or (ov_h / min_h) < 0.40:
                return False
            ov_w = max(0, min(a.right, b.right) - max(a.left, b.left))
            min_w = min(a.width, b.width)
            if min_w <= 0 or (ov_w / min_w) < 0.20:
                return False
            return True

        merged: List[OCRLine] = list(lines_p1)
        for l2 in lines_p2:
            matched = None
            for l1 in merged:
                if overlap_lines(l1, l2):
                    matched = l1
                    break
            if matched is None:
                merged.append(l2)
            else:
                if len(l2.text) > len(matched.text) * 1.25:
                    merged.remove(matched)
                    merged.append(l2)

        merged.sort(key=lambda x: (x.top, x.left))
        return merged

    def extract_ocr_lines(self, img: Image.Image, img_path: str) -> List[OCRLine]:
        """
        Runs unlimited offline Tesseract 5 LSTM (vie+eng) as primary engine (<0.3s/img, zero rate limits),
        with OCR.space Engine 2 as automatic cloud fallback!
        """
        lines = self._ocr_via_tesseract(img, img_path)
        if lines:
            return lines
        try:
            return self._ocr_via_ocrspace(img)
        except Exception:
            return []

    @staticmethod
    def _compute_row_foreground_ratio(img: Image.Image) -> Tuple[List[float], Tuple[int, int, int]]:
        """
        Computes per-row foreground pixel coverage (0.0 .. 1.0) relative to dominant chat background color.
        """
        orig_w, orig_h = img.size
        probe_w = min(320, orig_w)
        small = img.resize((probe_w, orig_h), Image.Resampling.BILINEAR).convert("RGB")
        px = small.load()

        y_start = int(orig_h * 0.12)
        y_end = max(y_start + 1, int(orig_h * 0.88))
        step_y = max(1, (y_end - y_start) // 80)
        edge_samples_r: List[int] = []
        edge_samples_g: List[int] = []
        edge_samples_b: List[int] = []
        margin_xs = [
            max(0, int(probe_w * 0.015)),
            max(0, int(probe_w * 0.50)),
            min(probe_w - 1, int(probe_w * 0.985)),
        ]
        for y in range(y_start, y_end, step_y):
            for mx in margin_xs:
                r, g, b = px[mx, y]
                edge_samples_r.append(r)
                edge_samples_g.append(g)
                edge_samples_b.append(b)

        bg_r = int(statistics.median(edge_samples_r)) if edge_samples_r else 255
        bg_g = int(statistics.median(edge_samples_g)) if edge_samples_g else 255
        bg_b = int(statistics.median(edge_samples_b)) if edge_samples_b else 255

        x0 = max(0, int(probe_w * 0.03))
        x1 = min(probe_w, int(probe_w * 0.97))
        span = max(1, x1 - x0)

        row_fg: List[float] = []
        for y in range(orig_h):
            fg_count = 0
            for x in range(x0, x1):
                r, g, b = px[x, y]
                if abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b) > 20:
                    fg_count += 1
            row_fg.append(fg_count / span)

        return row_fg, (bg_r, bg_g, bg_b)

    @staticmethod
    def _has_background_gap(row_fg: List[float], y_a: int, y_b: int) -> Optional[int]:
        """
        Checks if there is a clean horizontal background gap between y_a and y_b.
        Returns the midpoint y of the widest gap if found, else None.
        """
        h = len(row_fg)
        lo = max(0, min(h - 1, y_a))
        hi = max(0, min(h, y_b))
        if hi - lo < 2:
            return None

        best_len = 0
        best_mid = None
        cur_start = None
        for y in range(lo, hi):
            if row_fg[y] < 0.038:
                if cur_start is None:
                    cur_start = y
            else:
                if cur_start is not None:
                    seg_len = y - cur_start
                    if seg_len > best_len:
                        best_len = seg_len
                        best_mid = (cur_start + y) // 2
                    cur_start = None
        if cur_start is not None:
            seg_len = hi - cur_start
            if seg_len > best_len:
                best_len = seg_len
                best_mid = (cur_start + hi) // 2

        return best_mid if best_len >= 2 else None

    @classmethod
    def _is_sender_header(
        cls,
        img: Image.Image,
        bg_rgb: Tuple[int, int, int],
        ln: OCRLine,
        nxt: OCRLine,
        med_h: float,
    ) -> bool:
        v_gap = nxt.top - ln.bottom
        if not (0 <= v_gap <= max(med_h * 2.5, 48)):
            return False
        # Conversational text is NEVER a sender name header!
        if is_conversational_chat_message(ln.text):
            return False
        # Any text with punctuation or URLs or numbers is NEVER a sender header
        if any(p in ln.text for p in ("?", "!", ".", ",", ";", ":", "/")):
            return False
        if len(ln.text.split()) > 4:
            return False
        # Genuine sender name header is rendered in smaller font without conversational text
        if ln.height <= nxt.height * 0.88:
            return True
        return False

    @staticmethod
    def _line_align(ln: OCRLine, img_w: int) -> str:
        if ln.left >= img_w * 0.36 or (ln.right >= img_w * 0.82 and ln.left >= img_w * 0.14):
            return "right"
        if ln.left > img_w * 0.28 and ln.right < img_w * 0.72 and ln.width < img_w * 0.45:
            return "center"
        return "left"

    @staticmethod
    def apply_rounded_corners(im: Image.Image, radius: int = 24) -> Image.Image:
        """
        Creates smooth, anti-aliased rounded corners with alpha transparency (RGBA),
        giving the chat card/strip an authentic floating TikTok/iOS mobile bubble form.
        """
        im_rgba = im.convert("RGBA")
        w, h = im_rgba.size
        r = min(radius, max(4, h // 2), max(4, w // 2))
        if r <= 2:
            return im_rgba
        mask = Image.new("L", (w * 2, h * 2), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, w * 2, h * 2), radius=r * 2, fill=255)
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
        im_rgba.putalpha(mask)
        return im_rgba

    def create_chat_strip_from_text(
        self,
        text: str,
        speaker: str = "",
        align: str = "left",
        start_order_idx: int = 0,
        width: int = 1000,
        avatar_path: Optional[str] = None,
        speaker_color: Optional[Union[str, Tuple[int, int, int, int]]] = None,
    ) -> ExtractedChatSlice:
        """
        Generates an authentic, crisp mobile chat bubble card from manual text (no screenshot needed!).
        Features centered rounded container frame (bo khung ở giữa), circular avatar, customizable speaker name color,
        and high-contrast typography (52pt).
        """
        color_lookup = {
            "red": (225, 29, 72, 255),
            "purple": (124, 58, 237, 255),
            "blue": (37, 99, 235, 255),
            "green": (5, 150, 105, 255),
            "orange": (234, 88, 12, 255),
            "amber": (217, 119, 6, 255),
            "pink": (236, 72, 153, 255),
            "dark": (31, 41, 55, 255),
            "cyan": (2, 132, 199, 255),
        }
        if isinstance(speaker_color, tuple) and len(speaker_color) >= 3:
            speaker_rgba = speaker_color if len(speaker_color) == 4 else (*speaker_color[:3], 255)
        elif isinstance(speaker_color, str) and speaker_color.lower() in color_lookup:
            speaker_rgba = color_lookup[speaker_color.lower()]
        elif isinstance(speaker_color, str) and speaker_color.startswith("#"):
            hex_str = speaker_color.lstrip("#")
            if len(hex_str) == 6:
                try:
                    speaker_rgba = (
                        int(hex_str[0:2], 16),
                        int(hex_str[2:4], 16),
                        int(hex_str[4:6], 16),
                        255,
                    )
                except Exception:
                    speaker_rgba = (124, 58, 237, 255) if align == "right" else (225, 29, 72, 255)
            else:
                speaker_rgba = (124, 58, 237, 255) if align == "right" else (225, 29, 72, 255)
        else:
            speaker_rgba = (124, 58, 237, 255) if align == "right" else (225, 29, 72, 255)

        font_path = "C:/Windows/Fonts/segoeui.ttf"
        font_bold = "C:/Windows/Fonts/segoeuib.ttf"
        linux_candidates = [
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ),
            (
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ),
        ]
        if not os.path.exists(font_path):
            for reg_c, bld_c in linux_candidates:
                if os.path.exists(reg_c):
                    font_path = reg_c
                    font_bold = bld_c
                    break

        # Adaptive font sizing based on message text length so cards remain crisp, balanced and never clip
        text_len = len(text)
        if text_len <= 45:
            text_font_size = 48
            spk_font_size = 34
        elif text_len <= 110:
            text_font_size = 44
            spk_font_size = 32
        elif text_len <= 220:
            text_font_size = 38
            spk_font_size = 30
        else:
            text_font_size = 34
            spk_font_size = 28

        try:
            f_text = ImageFont.truetype(font_path, text_font_size)
            f_spk = ImageFont.truetype(font_bold, spk_font_size)
        except Exception:
            f_text = ImageFont.load_default()
            f_spk = ImageFont.load_default()

        # Strict Pixel-Measured Text Wrapping:
        # Ensures text never overflows out of the chat bubble regardless of uppercase or long words
        card_w = width
        av_size = 90
        bubble_pad_x = 34
        bubble_pad_y = 22
        max_bubble_w = card_w - av_size - 90
        max_text_w_allowed = max_bubble_w - bubble_pad_x * 2

        words = text.split()
        lines: List[str] = []
        cur = ""
        for w in words:
            cand = f"{cur} {w}".strip() if cur else w
            cand_box = f_text.getbbox(cand)
            cand_w = cand_box[2] - cand_box[0]
            if cand_w <= max_text_w_allowed:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                    cur = ""
                # Handle single giant unbroken word/URL by splitting characters
                w_box = f_text.getbbox(w)
                if w_box[2] - w_box[0] > max_text_w_allowed:
                    sub_cur = ""
                    for ch in w:
                        if f_text.getbbox(sub_cur + ch)[2] - f_text.getbbox(sub_cur + ch)[0] <= max_text_w_allowed:
                            sub_cur += ch
                        else:
                            if sub_cur:
                                lines.append(sub_cur)
                            sub_cur = ch
                    cur = sub_cur
                else:
                    cur = w
        if cur:
            lines.append(cur)
        if not lines:
            lines = [text]

        # Limit maximum lines per bubble card to 20 lines
        lines = lines[:20]

        line_h = int(text_font_size * 1.35)
        text_box_h = len(lines) * line_h
        bubble_h = text_box_h + bubble_pad_y * 2

        max_text_w = 0
        for l in lines:
            bbox = f_text.getbbox(l)
            max_text_w = max(max_text_w, bbox[2] - bbox[0])

        spk_h = 38 if speaker else 0
        card_h = max(av_size + 42, spk_h + bubble_h + 38)

        # Bubble width is strictly bounded by max_bubble_w and always encloses max_text_w with padding
        bubble_w = min(max_bubble_w, max(260, max_text_w + bubble_pad_x * 2))

        im = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)

        # Outer framed container (white background with rounded corners and subtle border)
        draw.rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1),
            radius=38,
            fill=(255, 255, 255, 255),
            outline=(226, 232, 240, 255),
            width=3,
        )

        def _make_avatar(av_path: Optional[str], av_name: str, bg_col: Tuple[int, int, int, int]) -> Image.Image:
            av_im = Image.new("RGBA", (av_size, av_size), (0, 0, 0, 0))
            mask = Image.new("L", (av_size, av_size), 0)
            d_mask = ImageDraw.Draw(mask)
            d_mask.ellipse((0, 0, av_size, av_size), fill=255)

            if av_path and os.path.exists(av_path):
                try:
                    u_im = Image.open(av_path).convert("RGBA")
                    u_im = ImageOps.fit(u_im, (av_size, av_size), Image.Resampling.LANCZOS)
                    av_im.paste(u_im, (0, 0), mask=mask)
                    d_av = ImageDraw.Draw(av_im)
                    d_av.ellipse((0, 0, av_size - 1, av_size - 1), outline=(255, 255, 255, 240), width=3)
                    return av_im
                except Exception:
                    pass

            d = ImageDraw.Draw(av_im)
            d.ellipse((0, 0, av_size, av_size), fill=bg_col)
            d.ellipse((0, 0, av_size - 1, av_size - 1), outline=(255, 255, 255, 240), width=3)
            try:
                f_in = ImageFont.truetype(font_bold, int(av_size * 0.52))
            except Exception:
                f_in = ImageFont.load_default()
            letter = (av_name or "A")[0].upper()
            l_box = f_in.getbbox(letter)
            tw = l_box[2] - l_box[0]
            th = l_box[3] - l_box[1]
            tx = (av_size - tw) // 2 - l_box[0]
            ty = (av_size - th) // 2 - l_box[1]
            d.text((tx, ty), letter, fill=(255, 255, 255, 255), font=f_in)
            return av_im

        if align == "right":
            av_x = card_w - av_size - 28
            av_y = spk_h + 12
            avatar = _make_avatar(avatar_path, speaker or "B", speaker_rgba)
            im.paste(avatar, (av_x, av_y), mask=avatar)

            bx2 = av_x - 18
            bx1 = bx2 - bubble_w
            by1 = spk_h + 10
            by2 = by1 + bubble_h

            if speaker:
                s_bbox = f_spk.getbbox(speaker)
                s_w = s_bbox[2] - s_bbox[0]
                draw.text((bx2 - s_w - 6, 12), speaker, fill=speaker_rgba, font=f_spk)

            # Bubble shape with smooth rounded corners in soft lavender
            draw.rounded_rectangle((bx1, by1, bx2, by2), radius=30, fill=(245, 243, 255, 255), outline=(221, 214, 254, 255), width=2)
            tx = bx1 + bubble_pad_x
            ty = by1 + bubble_pad_y
            for l in lines:
                draw.text((tx, ty), l, fill=(17, 24, 39, 255), font=f_text)
                ty += line_h
        else:
            av_x = 28
            av_y = spk_h + 12
            avatar = _make_avatar(avatar_path, speaker or "A", speaker_rgba)
            im.paste(avatar, (av_x, av_y), mask=avatar)

            bx1 = av_x + av_size + 18
            bx2 = bx1 + bubble_w
            by1 = spk_h + 10
            by2 = by1 + bubble_h

            if speaker:
                draw.text((bx1 + 8, 12), speaker, fill=speaker_rgba, font=f_spk)

            # Bubble shape with clean soft fill and subtle outline
            draw.rounded_rectangle((bx1, by1, bx2, by2), radius=30, fill=(243, 244, 246, 255), outline=(229, 231, 235, 255), width=2)
            tx = bx1 + bubble_pad_x
            ty = by1 + bubble_pad_y
            for l in lines:
                draw.text((tx, ty), l, fill=(17, 24, 39, 255), font=f_text)
                ty += line_h

        sid = f"manual_{uuid.uuid4().hex[:8]}"
        out_p = self.output_dir / f"chat_{sid}.png"
        im.save(out_p, format="PNG")

        return ExtractedChatSlice(
            slice_id=sid,
            source_image="manual_text_script",
            order_index=start_order_idx,
            speaker=speaker,
            spoken_text=text,
            align=align,
            image_path=str(out_p),
            width=im.size[0],
            height=im.size[1],
        )

    def process_image(
        self, image_path: str, start_order_idx: int = 0
    ) -> List[ExtractedChatSlice]:
        """
        Analyzes a single chat image (either a 1-line pre-cropped strip OR a long multi-message screenshot)
        and returns 1..N ExtractedChatSlice items in strict top-to-bottom order.
        """
        raw_img = Image.open(image_path)
        raw_img = ImageOps.exif_transpose(raw_img)
        if raw_img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", raw_img.size, (255, 255, 255))
            bg.paste(raw_img, mask=raw_img.split()[-1])
            img = bg
        else:
            img = raw_img.convert("RGB")

        img_w, img_h = img.size
        is_full_phone_screen = (img_h / max(1.0, float(img_w))) >= 1.45

        ocr_lines = self.extract_ocr_lines(img, image_path)

        # Filter phone chrome (status bar, nav header, bottom input box) & standalone timestamps
        valid_lines: List[OCRLine] = []
        top_chrome_cutoff = 0
        bottom_chrome_cutoff = img_h

        for ln in ocr_lines:
            txt = ln.text.strip()
            if not txt or len(txt) <= 1:
                continue
            if len(txt) <= 2 and not is_conversational_chat_message(txt):
                continue

            # Filter status bar (top clock, battery, wifi, 4G/5G, carrier) near top of phone screenshots
            if ln.bottom <= img_h * 0.11:
                if (
                    not strip_dates_and_timestamps(txt)
                    or re.search(r'\b\d{1,2}[:.hH]\d{2}\b', txt)
                    or STATUS_BAR_RE.search(txt)
                ):
                    top_chrome_cutoff = max(top_chrome_cutoff, ln.bottom + 4)
                    continue

            if ln.bottom <= img_h * 0.125 and HEADER_NOISE_RE.search(txt):
                top_chrome_cutoff = max(top_chrome_cutoff, ln.bottom + 8)
                continue

            if ln.top >= img_h * 0.89 and FOOTER_INPUT_RE.search(txt):
                bottom_chrome_cutoff = min(bottom_chrome_cutoff, ln.top - 8)
                continue

            clean_meta_candidate = re.sub(r'[\s•\-_✓✔dDđĐ]+', ' ', txt).strip()
            # If the entire line is purely dates, timestamps, or OCR status noise, discard it completely!
            if (
                not strip_dates_and_timestamps(clean_meta_candidate)
                or STANDALONE_CHAT_META_RE.search(clean_meta_candidate)
                or re.fullmatch(r'^(?:\d{1,2}\s*[:.hH]\s*\d{2}|[:\s]?[a-zA-ZÀ-ỹ0-9_]{0,2}:\d{2})(?:\s*(?:AM|PM|SA|CH))?(?:\s*[\S]{1,4})?$', clean_meta_candidate, flags=re.IGNORECASE)
            ):
                continue

            valid_lines.append(ln)

        valid_lines.sort(key=lambda x: (x.top, x.left))

        # CASE 0: Image has no text at all (e.g., sticker or meme photo)
        if not valid_lines:
            sid = f"meme_{uuid.uuid4().hex[:8]}"
            out_p = self.output_dir / f"chat_{sid}.png"
            img.save(out_p, format="PNG")
            return [
                ExtractedChatSlice(
                    slice_id=sid,
                    source_image=os.path.basename(image_path),
                    order_index=start_order_idx,
                    speaker="",
                    spoken_text="",
                    align="center",
                    image_path=str(out_p),
                    width=img_w,
                    height=img_h,
                    is_meme_photo=True,
                )
            ]

        med_h = float(statistics.median([ln.height for ln in valid_lines]))
        row_fg, bg_rgb = self._compute_row_foreground_ratio(img)

        # Group valid_lines into logical Message Bubbles (with optional small sender-name header)
        bubbles: List[Dict[str, Any]] = []
        i = 0
        n_lines = len(valid_lines)

        while i < n_lines:
            ln = valid_lines[i]
            speaker = ""
            vis_top = ln.top

            # Check if `ln` is a small Sender Name Header directly above the bubble body
            if i + 1 < n_lines:
                nxt = valid_lines[i + 1]
                if self._is_sender_header(img, bg_rgb, ln, nxt, med_h):
                    speaker = ln.text.strip()
                    i += 1
                    ln = valid_lines[i]

            align = self._line_align(ln, img_w)
            body_lines = [ln]
            vis_bottom = ln.bottom
            vis_left = ln.left
            vis_right = ln.right
            i += 1

            # Merge subsequent lines that belong to the SAME wrapped message bubble
            while i < n_lines:
                cand = valid_lines[i]
                prev = body_lines[-1]
                v_gap = cand.top - prev.bottom
                cand_align = self._line_align(cand, img_w)

                if i + 1 < n_lines:
                    after_cand = valid_lines[i + 1]
                    if self._is_sender_header(img, bg_rgb, cand, after_cand, med_h):
                        break

                # Multi-line wrap detection (allows up to 20 lines per message bubble):
                # Lines inside the SAME message bubble have a vertical line wrap gap v_gap <= max(7.5, med_h * 0.52).
                # Between DIFFERENT message bubbles, the outer gap is strictly >= med_h * 0.78 (or >= 11px) with bubble borders.
                is_single_strip_candidate = (img_h / max(1.0, float(img_w))) <= 0.75 and n_lines <= 24
                max_intra_gap = max(24.0, med_h * 1.25) if is_single_strip_candidate else max(7.5, med_h * 0.52)

                # Check horizontal alignment affinity:
                same_side = (
                    cand_align == align
                    or (align == "left" and cand.left < img_w * 0.50)
                    or (align == "right" and cand.right > img_w * 0.50)
                )

                # Only check wallpaper background gap if vertical gap is wider than intra-bubble line wrap
                has_bg_gap = (
                    self._has_background_gap(row_fg, prev.bottom, cand.top) is not None
                    if v_gap > max_intra_gap
                    else False
                )

                if same_side and v_gap <= max_intra_gap and not has_bg_gap and len(body_lines) < 20:
                    body_lines.append(cand)
                    vis_bottom = max(vis_bottom, cand.bottom)
                    vis_left = min(vis_left, cand.left)
                    vis_right = max(vis_right, cand.right)
                    i += 1
                else:
                    break

            raw_spoken = " ".join(b.text for b in body_lines).strip()
            clean_spoken = strip_dates_and_timestamps(raw_spoken)
            clean_spoken = re.sub(
                r"(?::[a-zA-Z0-9_]+:|\b)\d{1,2}[:.hH]\d{2}(?:\s*(?:AM|PM|SA|CH))?(?:\s*[✓✔dDđĐ\-_]{1,3})?\b",
                "",
                clean_spoken,
                flags=re.IGNORECASE,
            )
            clean_spoken = re.sub(r"(?:[:\s]|^)[a-zA-ZÀ-ỹ0-9_]{0,2}:\d{2}\b", " ", clean_spoken)
            clean_spoken = re.sub(r":\s*[a-zA-ZÀ-ỹ]\s*:\s*\d{1,2}", "", clean_spoken)
            clean_spoken = re.sub(r"\b:\d{2}\b", "", clean_spoken)
            clean_spoken = re.sub(r"(?:\s|^):[a-zA-ZÀ-ỹ0-9_]{0,3}(?:\s|$)", " ", clean_spoken)
            clean_spoken = re.sub(r"\s+đt\s*$", "", clean_spoken, flags=re.IGNORECASE)
            clean_spoken = re.sub(r"\s{2,}", " ", clean_spoken).strip()
            bubbles.append(
                {
                    "speaker": speaker,
                    "spoken_text": clean_spoken or raw_spoken,
                    "align": align if align in ("left", "right") else "left",
                    "vis_top": vis_top,
                    "vis_bottom": vis_bottom,
                    "vis_left": vis_left,
                    "vis_right": vis_right,
                }
            )

        # CASE 1: Single-bubble pre-cropped horizontal strip -> preserve and auto-trim outside video background!
        if len(bubbles) == 1 and (img_h / max(1.0, float(img_w))) <= 0.75:
            b = bubbles[0]
            y_crop_top = 0
            y_crop_bot = img_h

            # Look at row colors at outer margin (x = 35) to detect video background transition (e.g. orange vs white card)
            test_x = min(35, img_w // 8)
            sample_y = (b["vis_top"] + b["vis_bottom"]) // 2
            if 0 <= sample_y < img_h:
                ref_rgb = img.getpixel((test_x, sample_y))[:3]
                # Scan upward from vis_top - 6
                cur_y = max(0, b["vis_top"] - 6)
                while cur_y > 0:
                    px = img.getpixel((test_x, cur_y))[:3]
                    diff = sum(abs(a - c) for a, c in zip(px, ref_rgb))
                    if diff > 105:
                        y_crop_top = max(0, cur_y + 2)
                        break
                    cur_y -= 2

                # Scan downward from vis_bottom + 8
                cur_y = min(img_h - 1, b["vis_bottom"] + 8)
                while cur_y < img_h - 1:
                    px = img.getpixel((test_x, cur_y))[:3]
                    diff = sum(abs(a - c) for a, c in zip(px, ref_rgb))
                    if diff > 105:
                        y_crop_bot = min(img_h, cur_y - 2)
                        break
                    cur_y += 2

            if y_crop_bot - y_crop_top < 40:
                y_crop_top = 0
                y_crop_bot = img_h

            cropped = img.crop((0, y_crop_top, img_w, y_crop_bot))
            # Upscale cropped slice 3.5x with Lanczos anti-aliasing for massive, bold mobile display (gấp 3.5 lần)
            scale_factor = 3.5
            cw35 = max(1080, int(round(cropped.size[0] * scale_factor)))
            ch35 = max(240, int(round(cropped.size[1] * scale_factor)))
            cropped_35x = cropped.resize((cw35, ch35), Image.Resampling.LANCZOS)
            corner_r = min(48, max(24, int(ch35 * 0.16)))
            rounded = self.apply_rounded_corners(cropped_35x, radius=corner_r)

            sid = f"img_{uuid.uuid4().hex[:8]}"
            out_p = self.output_dir / f"chat_{sid}.png"
            rounded.save(out_p, format="PNG")
            return [
                ExtractedChatSlice(
                    slice_id=sid,
                    source_image=os.path.basename(image_path),
                    order_index=start_order_idx,
                    speaker=b["speaker"],
                    spoken_text=b["spoken_text"],
                    align=b["align"],
                    image_path=str(out_p),
                    width=rounded.size[0],
                    height=rounded.size[1],
                )
            ]

        # CASE 2: Multi-bubble screenshot (or tall phone screenshot) -> Auto-Cap each bubble with rounded corners!
        slices: List[ExtractedChatSlice] = []
        pad_top_px = max(14, int(round(med_h * 0.75)))
        pad_bot_px = max(18, int(round(med_h * 0.95)))

        for idx, b in enumerate(bubbles):
            min_allowed_y = top_chrome_cutoff
            max_allowed_y = bottom_chrome_cutoff

            if idx > 0:
                prev_b = bubbles[idx - 1]
                min_allowed_y = max(min_allowed_y, (prev_b["vis_bottom"] + b["vis_top"]) // 2)

            if idx + 1 < len(bubbles):
                next_b = bubbles[idx + 1]
                max_allowed_y = min(max_allowed_y, (b["vis_bottom"] + next_b["vis_top"]) // 2)

            # Expand around text box up to bubble boundary + clean breathing room
            y1 = max(min_allowed_y, b["vis_top"] - pad_top_px)
            y2 = min(max_allowed_y, b["vis_bottom"] + pad_bot_px)

            if y2 - y1 < 26:
                y1 = max(0, b["vis_top"] - 14)
                y2 = min(img_h, b["vis_bottom"] + 16)

            # Horizontal crop:
            # If bubble is left-aligned: include left avatar / margin + bubble right padding
            # If bubble is right-aligned: include bubble left padding + right margin
            if b["align"] == "left":
                x1 = max(0, b["vis_left"] - 75)
                x2 = min(img_w, max(int(img_w * 0.65), b["vis_right"] + 45))
            elif b["align"] == "right":
                x1 = max(0, min(int(img_w * 0.35), b["vis_left"] - 45))
                x2 = min(img_w, b["vis_right"] + 40)
            else:
                x1 = max(0, b["vis_left"] - 45)
                x2 = min(img_w, b["vis_right"] + 45)

            # Ensure minimum width for balanced mobile presentation
            if (x2 - x1) < int(img_w * 0.55):
                mid_x = (x1 + x2) // 2
                half_w = int(img_w * 0.28)
                x1 = max(0, mid_x - half_w)
                x2 = min(img_w, mid_x + half_w)

            cropped = img.crop((x1, y1, x2, y2))
            # Upscale cropped slice 3.5x with Lanczos anti-aliasing for massive, bold mobile display (gấp 3.5 lần)
            scale_factor = min(4.0, max(3.0, 1920.0 / float(cropped.size[0])))
            cw35 = max(900, int(round(cropped.size[0] * scale_factor)))
            ch35 = max(160, int(round(cropped.size[1] * scale_factor)))
            cropped_35x = cropped.resize((cw35, ch35), Image.Resampling.LANCZOS)
            corner_r = min(48, max(20, int(ch35 * 0.16)))
            rounded = self.apply_rounded_corners(cropped_35x, radius=corner_r)

            sid = f"cap_{uuid.uuid4().hex[:8]}"
            out_p = self.output_dir / f"chat_{sid}.png"
            rounded.save(out_p, format="PNG")

            slices.append(
                ExtractedChatSlice(
                    slice_id=sid,
                    source_image=os.path.basename(image_path),
                    order_index=start_order_idx + idx,
                    speaker=b["speaker"],
                    spoken_text=b["spoken_text"],
                    align=b["align"],
                    image_path=str(out_p),
                    width=rounded.size[0],
                    height=rounded.size[1],
                )
            )

        return slices

    def process_batch_images(
        self, image_paths: List[str], start_order_idx: int = 0
    ) -> List[ExtractedChatSlice]:
        """
        Processes multiple user-uploaded images in parallel while strictly preserving
        1) the exact image order sent by the user (image 1 -> image 2 -> image 3...), and
        2) the exact top-to-bottom row order within each image!
        """
        from concurrent.futures import ThreadPoolExecutor

        if not image_paths:
            return []

        max_w = min(4, len(image_paths))
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            per_image_slices = list(pool.map(lambda p: self.process_image(p, 0), image_paths))

        all_slices: List[ExtractedChatSlice] = []
        running_idx = start_order_idx
        for slice_list in per_image_slices:
            for s in slice_list:
                # If this image is a pure meme photo and there is a preceding chat message slice:
                # Automatically pair it as the meme photo underneath that chat bubble!
                if s.is_meme_photo and all_slices:
                    all_slices[-1].meme_image_path = s.image_path
                    continue
                s.order_index = running_idx
                all_slices.append(s)
                running_idx += 1

        return all_slices
