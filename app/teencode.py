"""
Teencode & Vietnamese Text Normalizer for TTS & Video Automation.
Translates Vietnamese social media slangs, teencodes, abbreviations, currency,
strips dates/months/years & timestamps so TTS never reads dates out loud,
and cleans links / text emoticons / spam emojis for natural Speech synthesis.
"""

import re
import sys
from typing import Dict, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Comprehensive Teencode & Acronym Dictionary (Threads / TikTok / Facebook Vietnam)
TEENCODE_DICT: Dict[str, str] = {
    # Negation
    "ko": "không",
    "k": "không",
    "kh": "không",
    "khg": "không",
    "hông": "không",
    "hong": "không",
    "hem": "không",
    "hok": "không",
    "kô": "không",
    "đ": "không",
    "éo": "không",

    # Common verbs & prepositions
    "đc": "được",
    "dc": "được",
    "dk": "được",
    "dx": "được",
    "chx": "chưa",
    "ch": "chưa",
    "bt": "biết",
    "bít": "biết",
    "tl": "trả lời",
    "rep": "trả lời",
    "trloi": "trả lời",
    "ib": "nhắn tin",
    "inbox": "nhắn tin",
    "cmt": "bình luận",
    "cm": "bình luận",
    "bl": "bình luận",
    "fb": "phây búc",
    "tt": "tóp tóp",
    "tiktok": "tóp tóp",
    "threads": "thờ-rét",
    "ig": "in-sta-gram",
    "ytb": "du-túp",
    "zl": "da-lô",
    "vs": "với",
    "với": "với",

    # Identification, Banking, Transport & Legal abbreviations
    "cccd": "căn cước công dân",
    "cmnd": "chứng minh nhân dân",
    "vneid": "vê nê ai đi",
    "gplx": "giấy phép lái xe",
    "bhyt": "bảo hiểm y tế",
    "bhxh": "bảo hiểm xã hội",
    "đkxm": "đăng ký xe máy",
    "stk": "số tài khoản",
    "sdt": "số điện thoại",
    "sđt": "số điện thoại",
    "atm": "a tê mờ",
    "th": "thôi",
    "ns": "nói",
    "nt": "nhắn tin",
    "ms": "mới",

    # Pronouns, locations & nouns
    "vn": "Việt Nam",
    "tq": "Trung Quốc",
    "hn": "Hà Nội",
    "sg": "Sài Gòn",
    "hcm": "Hồ Chí Minh",
    "pbvm": "phân biệt vùng miền",
    "mn": "mọi người",
    "mng": "mọi người",
    "mk": "mình",
    "mik": "mình",
    "mìn": "mình",
    "mềnh": "mình",
    "tui": "tôi",
    "t": "tao",
    "m": "mày",
    "bả": "bà ấy",
    "ổng": "ông ấy",
    "ngta": "người ta",
    "ng": "người",
    "ny": "người yêu",
    "nyc": "người yêu cũ",
    "cr": "cờ rớt",
    "crush": "cờ rớt",
    "ck": "chồng",
    "vk": "vợ",
    "thg": "thằng",
    "b": "bạn",
    "ae": "anh em",
    "ad": "át-min",
    "admin": "át-min",

    # Adjectives, exclamations & adverbs
    "tr": "trời",
    "troi": "trời",
    "thui": "thôi",
    "thoi": "thôi",
    "bth": "bình thường",
    "bthg": "bình thường",
    "hqua": "hôm qua",
    "hqa": "hôm qua",
    "hnay": "hôm nay",
    "mmai": "ngày mai",
    "ntn": "như thế nào",
    "s": "sao",
    "z": "vậy",
    "zậy": "vậy",
    "v": "vậy",
    "vay": "vậy",
    "thik": "thích",
    "thx": "thích",
    "nhìu": "nhiều",
    "nhiu": "nhiều",
    "wa": "quá",
    "qúa": "quá",
    "nma": "nhưng mà",
    "nhma": "nhưng mà",
    "nhg": "nhưng",
    "r": "rồi",
    "rùi": "rồi",
    "roy": "rồi",
    "j": "gì",
    "ji": "gì",
    "gud": "tốt",
    "oke": "ô kê",
    "oki": "ô kê",
    "okela": "ô kê",
    "okey": "ô kê",
    "tks": "cảm ơn",
    "camon": "cảm ơn",
    "cmon": "cảm ơn",
    "kb": "kết bạn",

    # Slang & Reactions (softened for natural viral video TTS)
    "vcl": "vãi chưởng",
    "vkl": "vãi chưởng",
    "vch": "vãi chưởng",
    "clgt": "cái quái gì thế",
    "đm": "trời đất",
    "dm": "trời đất",
    "đcm": "trời đất",
    "dcm": "trời đất",
    "vl": "vãi thật",
    "vler": "vãi thật",
    "xịn": "xịn sò",
}

# Regex Patterns for prices (e.g. 10k -> 10 nghìn, 2tr -> 2 triệu)
PRICE_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (re.compile(r'(\d+)\s*(k|K)\b'), r'\1 nghìn '),
    (re.compile(r'(\d+)\s*(tr|củ)\b', re.IGNORECASE), r'\1 triệu '),
    (re.compile(r'(\d+)\s*(lốp|lít)\b', re.IGNORECASE), r'\1 trăm nghìn '),
    (re.compile(r'(\d+)\s*(vé)\b', re.IGNORECASE), r'\1 nghìn '),
]

# Date / Month / Year & Timestamp patterns to strip completely from TTS (and scraped metadata)
DATE_PATTERNS_FOR_TTS: list[re.Pattern] = [
    # Parenthesized dates like (14/8/25), (12-10-2026), (14/08)
    re.compile(r'\(\s*\d{1,4}\s*[/\-\.]\s*\d{1,2}(?:\s*[/\-\.]\s*\d{1,4})?\s*\)'),
    # Parenthesized relative timestamps like (2 giờ trước), (1 ngày), (22h)
    re.compile(r'\(\s*\d+\s*(?:giờ|ngày|phút|giây|tuần|tháng|năm|h|d|m|w)\s*(?:trước)?\s*\)', re.IGNORECASE),
    # YYYY-MM-DD or YYYY/MM/DD
    re.compile(r'\b(?:ngày\s+)?(?:19|20)\d{2}\s*[/\-\.]\s*(?:0?[1-9]|1[0-2])\s*[/\-\.]\s*(?:0?[1-9]|[12]\d|3[01])\b', re.IGNORECASE),
    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY or DD/MM/YY
    re.compile(r'\b(?:ngày\s+|hôm\s+)?(?:0?[1-9]|[12]\d|3[01])\s*[/\-\.]\s*(?:0?[1-9]|1[0-2])\s*[/\-\.]\s*(?:\d{2}|\d{4})\b', re.IGNORECASE),
    # DD/MM slash dates like 14/8, 02/09, 30/4
    re.compile(r'\b(?:ngày\s+|hôm\s+)?(?:0?[1-9]|[12]\d|3[01])\s*/\s*(?:0?[1-9]|1[0-2])\b', re.IGNORECASE),
    # Vietnamese full/partial spoken date phrases: "ngày 14 tháng 8 năm 2025", "ngày 14 tháng 8", "tháng 8 năm 2025"
    re.compile(
        r'\bngày\s+\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{2,4})?\b',
        re.IGNORECASE,
    ),
    re.compile(r'\btháng\s+\d{1,2}\s+năm\s+\d{2,4}\b', re.IGNORECASE),
    re.compile(r'\bngày\s+\d{1,2}\s*[/\-]\s*\d{1,2}(?:\s*[/\-]\s*\d{2,4})?\b', re.IGNORECASE),
    # Threads / Facebook localized date strings: "14 thg 8, 2025" or "14 thg 8"
    re.compile(r'\b\d{1,2}\s*thg\s*\d{1,2}(?:\s*,?\s*\d{2,4})?\b', re.IGNORECASE),
    # Relative UI timestamps leaked into text: "2 giờ trước", "3 ngày trước", "5 phút trước"
    re.compile(r'\b\d+\s*(?:giờ|ngày|phút|giây|tuần|tháng|năm)\s+trước\b', re.IGNORECASE),
    # Clock timestamps & phone status times like 16:42, 19:38, 09:15 SA, 16h42, 16.42
    re.compile(r'\b\d{1,2}\s*[:.hH]\s*\d{2}(?::\d{2})?(?:\s*(?:AM|PM|SA|CH))?(?:\s*[✓✔dDđĐ\-_]{1,3})?\b', re.IGNORECASE),
    # Corrupted OCR clock times (:a:42, :ạ:42, a:42, x:42, ::42, :42, :a:41)
    re.compile(r'(?:[:\s]|^)[a-zA-ZÀ-ỹ0-9_]{0,2}:\d{2}(?:\s*(?:AM|PM|SA|CH))?(?:\s*[✓✔dDđĐ\-_]{1,3})?\b', re.IGNORECASE),
    re.compile(r'\b\d{1,2}h(?:\d{2})?\b', re.IGNORECASE),
    # Dangling OCR noise timestamps and colons like :ạ, :a, :
    re.compile(r'(?:\s|^):[a-zA-ZÀ-ỹ0-9_]{0,3}(?:\s|$)', re.IGNORECASE),
    re.compile(r'\b:\d{2}\b'),
    # Trailing chat status indicators like double checkmarks ✓✓, dd, đt, etc.
    re.compile(r'[\s•\-_]+(?:[✓✔]{1,2}|dd|đt|đã xem|đã nhận|đã gửi)\s*$', re.IGNORECASE),
    re.compile(r'(?:\s|^)[✓✔dDđĐ\-_~¿„›\<\>]{1,4}(?:\s|$)'),
]

# Text emoticons like =)))), :)))), :(((, =]], :v
TEXT_EMOTICON_PATTERN = re.compile(r'(?:[=:;8xX][\-\^]?[\)\(\]\[dDpPvVoO3><]+|=+\)+|:+\)+|:+\(+)')

URL_PATTERN = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
EMOJI_PATTERN = re.compile(
    r'[\U00010000-\U0010ffff]'
    r'|[\u2600-\u27BF]'
    r'|[\uD83C-\uDBFF\uDC00-\uDFFF]',
    flags=re.UNICODE
)


def strip_dates_and_timestamps(text: str) -> str:
    """Removes all date/month/year formats and timestamps so TTS never reads them aloud."""
    if not text:
        return ""
    cleaned = text
    for pattern in DATE_PATTERNS_FOR_TTS:
        cleaned = pattern.sub(" ", cleaned)
    # Strip any trailing time/status tokens
    cleaned = re.sub(r'\s+đt\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    return cleaned.strip()


def clean_for_display(text: str) -> str:
    """Cleans raw text for UI comment card display (preserves emoji & style, removes junk URLs)."""
    if not text:
        return ""
    cleaned = URL_PATTERN.sub('[link]', text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r' +', ' ', cleaned)
    return cleaned.strip()


def normalize_for_tts(text: str) -> str:
    """
    Normalizes Vietnamese text specifically for TTS voices to pronounce naturally:
    - Strips ALL dates, months, years, and UI timestamps.
    - Strips text emoticons (=)))), :)))) so TTS doesn't read punctuation names.
    - Replaces teencode/acronyms with full Vietnamese words.
    - Expands currency (10k -> 10 nghìn).
    - Removes URLs and excessive emojis.
    """
    if not text:
        return ""

    # 1. Remove URLs
    text = URL_PATTERN.sub(' ', text)

    # 2. Remove all date/month/year & timestamp strings BEFORE number/teencode expansion
    text = strip_dates_and_timestamps(text)

    # 3. Separate glued text emoticons like "nhé tr=))))))" -> "nhé tr "
    text = TEXT_EMOTICON_PATTERN.sub(' ', text)

    # 4. Expand prices / currency numbers (e.g. 50k, 2tr)
    for pattern, replacement in PRICE_PATTERNS:
        text = pattern.sub(replacement, text)

    # 5. Replace Teencode (case-insensitive word boundary match)
    def replace_teencode(match: re.Match) -> str:
        word = match.group(0)
        lower_word = word.lower()
        if lower_word in TEENCODE_DICT:
            replacement = TEENCODE_DICT[lower_word]
            if word.istitle():
                return replacement.capitalize()
            elif word.isupper() and len(word) > 2:
                return replacement
            return replacement
        return word

    word_regex = re.compile(r'\b[A-Za-z0-9_À-ỹ]+\b', re.UNICODE)
    text = word_regex.sub(replace_teencode, text)

    # 6. Remove Unicode emojis & rating stars so TTS doesn't say "ngôi sao trắng"
    text = EMOJI_PATTERN.sub(' ', text)
    text = re.sub(r'[★☆⭐]+', ' ', text)

    # 7. Clean up leftover brackets, quotes, symbols & spaces (including any leftover colons)
    text = re.sub(r'[:~`^*_={}\[\]<>|\\()]+', ' ', text)
    text = strip_dates_and_timestamps(text)
    # Ensure natural comma, period, question mark breathing pauses for coherent speech
    text = re.sub(r'([,\.\?!;])(?=[A-Za-z0-9À-ỹ])', r'\1 ', text)
    text = re.sub(r'\s+([,\.\?!;])', r'\1', text)
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


if __name__ == "__main__":
    samples = [
        "Quân Nguyễn (14/8/25): cần phải đăng nhập ngày 12/10/2026 mà k bt đăng nhập",
        "thề VN mình có tỉ quả joke hài vãi, làm ơn đừng biến mất nhé tr=))))))",
        "k phải pbvm, chỉ là cái cmt hôm 14 thg 8, 2025 này làm t đ nhịn cười nổi 😂😂😂",
    ]
    for s in samples:
        print("RAW:", s)
        print("TTS:", normalize_for_tts(s))
        print("-" * 50)
