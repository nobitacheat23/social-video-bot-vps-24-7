"""
Bulletproof Multi-Voice Text-To-Speech (TTS) Engine for Vietnamese Social & Chat Video Automation.
Supports:
1. Viral TikTok / CapCut Voices (BV075_streaming Male Meme, BV074_streaming Female Viral)
2. Youthful Southern Vietnamese Male ("vi-VN-NamMinhYouth" - fast, energetic skit style)
3. Multi-Character Auto-Dialogue ("dialogue-auto" - assigns consistent distinct voices per chat speaker!)
4. Natural Microsoft Neural Voices (Hoài My, Nam Minh)
5. Automatic date/timestamp stripping, emoji-only silent beat synthesis, & strict FFmpeg validation.
"""

import asyncio
import base64
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import edge_tts
from gtts import gTTS
import imageio_ffmpeg
import requests
import urllib3.util.connection as urllib3_cn
from app.teencode import normalize_for_tts

# Prefer IPv4 to avoid residential/ISP IPv6 blackholes
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

VIETNAMESE_VOICES = {
    "adam": "tiktok-adam",
    "adam_beta_3": "vclip-adam-beta-3",
    "adam_beta_2": "vclip-adam-beta-2",
    "google_chi_google": "google-chigoogle",
    "tiktok_male": "tiktok-BV075",
    "tiktok_female": "tiktok-BV074",
    "tiktok_warm_male": "tiktok-BV071",
    "dialogue_auto": "dialogue-auto",
    "male_youth": "vi-VN-NamMinhYouth",
    "female": "vi-VN-HoaiMyNeural",
    "male": "vi-VN-NamMinhNeural",
}

VOICE_CATALOG = [
    {
        "id": "tiktok-adam",
        "name": "🎙 Adam (Nam Bắc Trầm Ấm - Review Phim / ElevenLabs)",
        "short_name": "🎙 Adam (Nam Bắc Review)",
        "gender": "Male",
    },
    {
        "id": "vclip-adam-beta-3",
        "name": "🎙 Adam Beta 3 (Nam Nam Hóm Hỉnh, Mộc Mạc & Dí Dỏm)",
        "short_name": "🎙 Adam Beta 3 (Nam Nam Hóm Hỉnh)",
        "gender": "Male",
    },
    {
        "id": "vclip-adam-beta-2",
        "name": "🎙 Adam Beta 2 (Nam Nam Bình Dân, Chân Thật & Vô Tri Hài)",
        "short_name": "🎙 Adam Beta 2 (Nam Nam Vô Tri)",
        "gender": "Male",
    },
    {
        "id": "google-chigoogle",
        "name": "🤖 Chị Google (Huyền Thoại Video Hài Hước Mạng Xã Hội)",
        "short_name": "🤖 Chị Google",
        "gender": "Female",
    },
    {
        "id": "tiktok-BV075",
        "name": "🔥 Thanh Niên TikTok / CapCut (BV075 - Nam Meme Viral)",
        "short_name": "🔥 Nam TikTok Viral (BV075)",
        "gender": "Male",
    },
    {
        "id": "tiktok-BV074",
        "name": "🔥 Cô Gái TikTok / CapCut (BV074 - Nữ Review Hoạt Ngôn)",
        "short_name": "🔥 Nữ TikTok Viral (BV074)",
        "gender": "Female",
    },
    {
        "id": "tiktok-BV071",
        "name": "🎙 Nam Trầm Ấm TikTok / CapCut (BV071)",
        "short_name": "🎙 Nam Trầm TikTok (BV071)",
        "gender": "Male",
    },
    {
        "id": "dialogue-auto",
        "name": "🎭 Đối Thoại Đa Nhân Vật (Phân vai giống Video TikTok mẫu)",
        "short_name": "🎭 Đối Thoại Đa Nhân Vật",
        "gender": "Multi",
    },
    {
        "id": "vi-VN-HoaiMyNeural",
        "name": "🎙 Hoài My (Nữ AI Truyền cảm, ngọt ngào)",
        "short_name": "🎙 Hoài My (Nữ AI)",
        "gender": "Female",
    },
    {
        "id": "vi-VN-NamMinhNeural",
        "name": "🎙 Nam Minh (Nam AI Trầm ấm, cuốn hút)",
        "short_name": "🎙 Nam Minh (Nam AI)",
        "gender": "Male",
    },
    {
        "id": "vi-VN-NamMinhYouth",
        "name": "⚡ Anh Trai Sài Gòn Lầy Lội (Nam Trẻ Năng Động)",
        "short_name": "⚡ Nam Trẻ Năng Động",
        "gender": "Male",
    },
]

FEMALE_SPEAKER_HINTS = {
    "chị", "chi", "cô", "co", "mẹ", "me", "má", "bà", "vợ", "vo", "bạn gái",
    "linh", "vy", "vi", "thảo", "thao", "trang", "hương", "huong", "lan",
    "mai", "ngọc", "ngoc", "hà", "ha", "nhi", "yến", "yen", "quỳnh", "quynh",
    "trâm", "tram", "ngân", "ngan", "phương", "phuong", "thư", "thu", "bích",
    "diễm", "diem", "huyền", "huyen", "nhung", "tuyết", "tuyet", "my", "mi",
    "thúy", "thuy", "trinh", "ân", "hân", "han", "nguyên", "châu", "chau",
}

PRONOUNCEABLE_RE = re.compile(r"[A-Za-z0-9À-ỹ]", re.UNICODE)


def _get_ffmpeg_exe() -> str:
    if os.name != "nt":
        sys_ff = shutil.which("ffmpeg")
        if sys_ff:
            return sys_ff
    return imageio_ffmpeg.get_ffmpeg_exe()


def get_voice_label(voice_id: str) -> str:
    for v in VOICE_CATALOG:
        if v["id"] == voice_id:
            return v["short_name"]
    return voice_id


def _parse_rate_pct(rate_str: str) -> int:
    m = re.search(r"([+-]?\d+)", str(rate_str or "+0%"))
    return int(m.group(1)) if m else 0


def _format_rate_pct(pct: int) -> str:
    pct = max(-40, min(60, pct))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def probe_valid_audio_duration(file_path: str) -> Optional[float]:
    """
    Verifies the audio file is non-empty and readable by FFmpeg, returning exact duration in seconds.
    Returns None if file is missing, 0-byte, or corrupt.
    """
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 300:
        return None

    ffmpeg_exe = _get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-hide_banner", "-i", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

    if "Invalid argument" in result.stderr or "Failed to read frame size" in result.stderr:
        return None

    match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)", result.stderr)
    if match:
        hours = float(match.group(1))
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        dur = hours * 3600 + minutes * 60 + seconds
        if dur > 0.05:
            return dur
    return None


def get_audio_duration(file_path: str) -> float:
    dur = probe_valid_audio_duration(file_path)
    return dur if dur is not None else 2.0


def generate_silent_mp3(out_path: str, duration_sec: float = 1.8) -> None:
    """Generates a valid 24kHz mono MP3 silence track via FFmpeg (used for emoji/image-only rows)."""
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except Exception:
            pass
    ffmpeg_exe = _get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration_sec:.2f}",
        "-b:a",
        "48k",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=True)


def _split_utf8_chunks(text: str, max_bytes: int = 230) -> List[str]:
    """Splits Vietnamese text cleanly on punctuation/word boundaries so each chunk <= max_bytes UTF-8."""
    text = text.strip()
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    parts = re.split(r"([.!?;,\n]+)", text)
    sentences: List[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if re.fullmatch(r"[.!?;,\n]+", p):
            buf += p
            sentences.append(buf.strip())
            buf = ""
        else:
            if buf:
                sentences.append(buf.strip())
            buf = p
    if buf.strip():
        sentences.append(buf.strip())

    chunks: List[str] = []
    cur = ""
    for sent in sentences:
        candidate = f"{cur} {sent}".strip() if cur else sent
        if len(candidate.encode("utf-8")) <= max_bytes:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
                cur = ""
            if len(sent.encode("utf-8")) <= max_bytes:
                cur = sent
            else:
                words = sent.split()
                w_cur = ""
                for w in words:
                    w_cand = f"{w_cur} {w}".strip() if w_cur else w
                    if len(w_cand.encode("utf-8")) <= max_bytes:
                        w_cur = w_cand
                    else:
                        if w_cur:
                            chunks.append(w_cur)
                        w_cur = w[:60]
                if w_cur:
                    cur = w_cur
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _synthesize_tiktok_sync(
    text: str, tiktok_voice_code: str, out_path: str, rate_pct: int = 0
) -> bool:
    """
    Synthesizes authentic TikTok/CapCut streaming voice (BV075_streaming / BV074_streaming)
    with automatic chunk stitching and optional tempo adjustment.
    """
    chunks = _split_utf8_chunks(text, max_bytes=230)
    if not chunks:
        return False

    tmp_files: List[str] = []
    ffmpeg_exe = _get_ffmpeg_exe()
    try:
        for i, ch in enumerate(chunks):
            ok_chunk = False
            for attempt in range(2):
                try:
                    resp = requests.post(
                        "https://tiktok-tts.weilnet.workers.dev/api/generation",
                        json={"text": ch, "voice": tiktok_voice_code},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        payload = resp.json()
                        b64_data = payload.get("data")
                        if payload.get("success") and b64_data:
                            raw_mp3 = base64.b64decode(b64_data)
                            if len(raw_mp3) >= 300:
                                part_p = f"{out_path}.part{i}.mp3"
                                with open(part_p, "wb") as f:
                                    f.write(raw_mp3)
                                tmp_files.append(part_p)
                                ok_chunk = True
                                break
                except Exception:
                    pass
            if not ok_chunk:
                return False

        # Concatenate chunks + apply rate_pct tempo & trim trailing TikTok silence padding
        tempo = max(0.75, min(1.55, 1.0 + (rate_pct / 100.0)))
        inputs: List[str] = []
        for tf in tmp_files:
            inputs.extend(["-i", tf])

        n = len(tmp_files)
        af_chain = (
            f"concat=n={n}:v=0:a=1,"
            f"silenceremove=start_periods=1:start_silence=0.05:start_threshold=-45dB:"
            f"stop_periods=-1:stop_silence=0.14:stop_threshold=-45dB,"
            f"aresample=24000,atempo={tempo:.3f}"
        )
        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            af_chain,
            "-b:a",
            "64k",
            out_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return probe_valid_audio_duration(out_path) is not None
    except Exception:
        return False
    finally:
        for tf in tmp_files:
            try:
                if os.path.exists(tf):
                    os.remove(tf)
            except Exception:
                pass


def _is_female_speaker(speaker: str) -> bool:
    low = (speaker or "").lower()
    tokens = re.findall(r"[a-zà-ỹ]+", low)
    return any(t in FEMALE_SPEAKER_HINTS for t in tokens)


class TTSEngine:
    def __init__(self, output_dir: str, max_concurrency: int = 4):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrency = max_concurrency
        self._global_sem = asyncio.Semaphore(max_concurrency)

    @staticmethod
    def _resolve_voice_profile(voice_id: str, base_rate: str, base_pitch: str) -> Dict[str, Any]:
        rate_pct = _parse_rate_pct(base_rate)
        if voice_id in ("google-chigoogle", "gtts"):
            return {
                "engine": "gtts",
                "rate_pct": rate_pct,
                "fallback_voice": "vi-VN-HoaiMyNeural",
                "fallback_rate": _format_rate_pct(rate_pct),
                "fallback_pitch": "+0Hz",
            }
        if voice_id in ("tiktok-adam", "ai-adam", "adam", "vclip-adam"):
            return {
                "engine": "tiktok",
                "tiktok_code": "BV071_streaming",
                "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
                "rate_pct": max(-20, min(30, rate_pct + 1)),
                "fallback_voice": "vi-VN-NamMinhNeural",
                "fallback_rate": _format_rate_pct(rate_pct + 2),
                "fallback_pitch": "-4Hz",
            }
        if voice_id in ("vclip-adam-beta-3", "adam-beta-3", "adam_beta_3"):
            return {
                "engine": "tiktok",
                "tiktok_code": "BV075_streaming",
                "rate_pct": rate_pct + 8,
                "fallback_voice": "vi-VN-NamMinhNeural",
                "fallback_rate": _format_rate_pct(rate_pct + 14),
                "fallback_pitch": "+4Hz",
            }
        if voice_id in ("vclip-adam-beta-2", "adam-beta-2", "adam_beta_2"):
            return {
                "engine": "tiktok",
                "tiktok_code": "BV075_streaming",
                "rate_pct": rate_pct + 2,
                "fallback_voice": "vi-VN-NamMinhNeural",
                "fallback_rate": _format_rate_pct(rate_pct + 6),
                "fallback_pitch": "-2Hz",
            }
        if voice_id == "tiktok-BV075":
            return {
                "engine": "tiktok",
                "tiktok_code": "BV075_streaming",
                "rate_pct": rate_pct + 6,
                "fallback_voice": "vi-VN-NamMinhNeural",
                "fallback_rate": _format_rate_pct(rate_pct + 18),
                "fallback_pitch": "+8Hz",
            }
        if voice_id == "tiktok-BV074":
            return {
                "engine": "tiktok",
                "tiktok_code": "BV074_streaming",
                "rate_pct": rate_pct + 5,
                "fallback_voice": "vi-VN-HoaiMyNeural",
                "fallback_rate": _format_rate_pct(rate_pct + 15),
                "fallback_pitch": "+4Hz",
            }
        if voice_id == "vi-VN-NamMinhYouth":
            return {
                "engine": "edge",
                "edge_voice": "vi-VN-NamMinhNeural",
                "edge_rate": _format_rate_pct(rate_pct + 18),
                "edge_pitch": "+10Hz",
            }
        if voice_id == "vi-VN-NamMinhBoss":
            return {
                "engine": "edge",
                "edge_voice": "vi-VN-NamMinhNeural",
                "edge_rate": _format_rate_pct(rate_pct + 8),
                "edge_pitch": "-6Hz",
            }
        if voice_id in ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"):
            return {
                "engine": "edge",
                "edge_voice": voice_id,
                "edge_rate": base_rate,
                "edge_pitch": base_pitch,
            }
        return {
            "engine": "edge",
            "edge_voice": "vi-VN-HoaiMyNeural",
            "edge_rate": base_rate,
            "edge_pitch": base_pitch,
        }

    def assign_dialogue_voices(self, items: List[Dict[str, Any]]) -> List[str]:
        """
        Assigns consistent, natural multi-character voices across a conversation or comment thread
        just like @mozziele's viral TikTok chat videos.
        """
        male_cast = [
            "vclip-adam-beta-3",
            "vclip-adam-beta-2",
            "tiktok-adam",
            "vi-VN-NamMinhYouth",
            "tiktok-BV075",
            "vi-VN-NamMinhBoss",
        ]
        female_cast = [
            "tiktok-BV074",
            "vi-VN-HoaiMyNeural",
        ]

        speaker_map: Dict[str, str] = {}
        male_idx = 0
        female_idx = 0
        assigned: List[str] = []

        for idx, it in enumerate(items):
            raw_speaker = str(it.get("speaker") or "").strip()
            align = str(it.get("align") or "").strip().lower()

            if raw_speaker:
                key = raw_speaker.lower()
            elif align in ("left", "right"):
                key = f"__side_{align}__"
            else:
                key = f"__turn_{idx % 2}__"

            if key not in speaker_map:
                if raw_speaker and _is_female_speaker(raw_speaker):
                    speaker_map[key] = female_cast[female_idx % len(female_cast)]
                    female_idx += 1
                else:
                    speaker_map[key] = male_cast[male_idx % len(male_cast)]
                    male_idx += 1

            assigned.append(speaker_map[key])

        return assigned

    async def synthesize_item(
        self,
        item_id: str,
        text: str,
        voice: str = "vi-VN-HoaiMyNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> Dict[str, Any]:
        """
        Synthesizes speech for a single post/comment/chat-bubble text (with all dates stripped).
        Guarantees a valid, FFmpeg-verified MP3 file on disk in 100% of cases.
        """
        clean_tts_text = normalize_for_tts(text)
        out_path = self.output_dir / f"tts_{item_id}.mp3"

        if not PRONOUNCEABLE_RE.search(clean_tts_text):
            await asyncio.to_thread(generate_silent_mp3, str(out_path), 1.65)
            dur = await asyncio.to_thread(probe_valid_audio_duration, str(out_path)) or 1.65
            return {
                "id": item_id,
                "raw_text": text,
                "tts_text": "",
                "audio_path": str(out_path),
                "duration": round(dur + 0.08, 2),
                "voice": voice,
            }

        profile = self._resolve_voice_profile(voice, rate, pitch)
        sem = semaphore or self._global_sem
        probed_dur: Optional[float] = None

        async with sem:
            # 0. Optional Studio ElevenLabs Voice (if ELEVENLABS_API_KEY is configured)
            el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if probed_dur is None and el_key and profile.get("elevenlabs_voice_id"):
                try:
                    el_voice_id = profile["elevenlabs_voice_id"]
                    el_url = f"https://api.elevenlabs.io/v1/text-to-speech/{el_voice_id}"

                    def _call_eleven():
                        return requests.post(
                            el_url,
                            headers={"xi-api-key": el_key, "Content-Type": "application/json"},
                            json={
                                "text": clean_tts_text,
                                "model_id": "eleven_multilingual_v2",
                                "voice_settings": {"stability": 0.45, "similarity_boost": 0.85},
                            },
                            timeout=14,
                        )

                    el_resp = await asyncio.to_thread(_call_eleven)
                    if el_resp.status_code == 200 and len(el_resp.content) >= 300:
                        out_path.write_bytes(el_resp.content)
                        probed_dur = await asyncio.to_thread(probe_valid_audio_duration, str(out_path))
                except Exception:
                    probed_dur = None

            # 1. Primary Google "Chị Google" Voice (if selected)
            if profile["engine"] == "gtts":
                try:
                    if out_path.exists():
                        out_path.unlink()
                    await asyncio.to_thread(
                        lambda: gTTS(text=clean_tts_text, lang="vi").save(str(out_path))
                    )
                    if profile.get("rate_pct", 0) != 0:
                        tempo = max(0.8, min(1.5, 1.0 + profile["rate_pct"] / 100.0))
                        tmp_g = str(out_path) + ".tmp.mp3"
                        os.rename(str(out_path), tmp_g)
                        cmd = [_get_ffmpeg_exe(), "-y", "-i", tmp_g, "-filter:a", f"atempo={tempo:.2f}", str(out_path)]
                        subprocess.run(cmd, capture_output=True, check=True)
                        if os.path.exists(tmp_g): os.remove(tmp_g)
                    probed_dur = await asyncio.to_thread(probe_valid_audio_duration, str(out_path))
                except Exception:
                    probed_dur = None

            # 2. Primary TikTok Streaming Voice (if selected)
            if probed_dur is None and profile["engine"] == "tiktok":
                ok_tt = await asyncio.to_thread(
                    _synthesize_tiktok_sync,
                    clean_tts_text,
                    profile["tiktok_code"],
                    str(out_path),
                    profile["rate_pct"],
                )
                if ok_tt:
                    probed_dur = await asyncio.to_thread(
                        probe_valid_audio_duration, str(out_path)
                    )

            # 2. Primary or Fallback Edge-TTS Neural Voice
            if probed_dur is None:
                edge_v = profile.get("edge_voice") or profile.get("fallback_voice", "vi-VN-HoaiMyNeural")
                edge_r = profile.get("edge_rate") or profile.get("fallback_rate", rate)
                edge_p = profile.get("edge_pitch") or profile.get("fallback_pitch", pitch)

                for attempt in range(3):
                    try:
                        if out_path.exists():
                            out_path.unlink()
                        communicator = edge_tts.Communicate(
                            text=clean_tts_text,
                            voice=edge_v,
                            rate=edge_r,
                            pitch=edge_p,
                        )
                        await communicator.save(str(out_path))
                        probed_dur = await asyncio.to_thread(
                            probe_valid_audio_duration, str(out_path)
                        )
                        if probed_dur is not None:
                            break
                    except Exception:
                        await asyncio.sleep(0.3 * (attempt + 1))

            # 3. Secondary Fallback: gTTS
            if probed_dur is None:
                try:
                    if out_path.exists():
                        out_path.unlink()
                    await asyncio.to_thread(
                        lambda: gTTS(text=clean_tts_text, lang="vi").save(str(out_path))
                    )
                    probed_dur = await asyncio.to_thread(
                        probe_valid_audio_duration, str(out_path)
                    )
                except Exception:
                    probed_dur = None

            # 4. Ultimate Safety Net: Valid silent MP3
            if probed_dur is None:
                est_dur = max(1.8, min(5.0, len(clean_tts_text) * 0.07))
                await asyncio.to_thread(generate_silent_mp3, str(out_path), est_dur)
                probed_dur = (
                    await asyncio.to_thread(probe_valid_audio_duration, str(out_path))
                    or est_dur
                )

        duration = round(probed_dur + 0.10, 2)
        return {
            "id": item_id,
            "raw_text": text,
            "tts_text": clean_tts_text,
            "audio_path": str(out_path),
            "duration": duration,
            "voice": voice,
        }

    async def batch_synthesize(
        self,
        items: List[Dict[str, Any]],
        voice: str = "vi-VN-HoaiMyNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        progress_callback: Optional[Callable[[int, int], Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Synthesizes comments/chat-bubbles concurrently while preserving exact order and reporting live progress.
        Supports 'dialogue-auto' multi-character voice assignment automatically!
        """
        total = len(items)
        if total == 0:
            return []

        per_item_voices: List[str]
        if voice == "dialogue-auto":
            per_item_voices = self.assign_dialogue_voices(items)
        else:
            per_item_voices = [it.get("voice") or voice for it in items]

        completed_count = 0
        lock = asyncio.Lock()

        async def _worker(idx: int, item: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
            nonlocal completed_count
            item_id = str(item.get("id", f"item_{idx}"))
            res = await self.synthesize_item(
                item_id=item_id,
                text=item.get("text", ""),
                voice=per_item_voices[idx],
                rate=rate,
                pitch=pitch,
                semaphore=self._global_sem,
            )
            async with lock:
                completed_count += 1
                done_now = completed_count
            if progress_callback:
                try:
                    maybe_coro = progress_callback(done_now, total)
                    if asyncio.iscoroutine(maybe_coro):
                        await maybe_coro
                except Exception:
                    pass
            return idx, res

        pairs = await asyncio.gather(*[_worker(i, it) for i, it in enumerate(items)])
        pairs.sort(key=lambda p: p[0])
        ordered_results = [p[1] for p in pairs]

        current_time = 0.0
        timeline_items: List[Dict[str, Any]] = []
        for res in ordered_results:
            item_info = dict(res)
            item_info["start_time"] = round(current_time, 2)
            item_info["end_time"] = round(current_time + res["duration"], 2)
            current_time = item_info["end_time"]
            timeline_items.append(item_info)

        return timeline_items
