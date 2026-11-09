"""
License Key & User Management Engine for Telegram Bot & Web Studio.
Enforces strict 1-Key-1-User binding, real-time revocation when a key is deleted,
Admin privilege verification (@citywin2026), and persistent per-user video settings.
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

ADMIN_USERNAME = "citywin2026"


class KeyManager:
    """
    Thread-safe SQLite manager for license keys and per-user video rendering preferences.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS license_keys (
                        key_code TEXT PRIMARY KEY,
                        note TEXT DEFAULT '',
                        created_by TEXT DEFAULT 'admin',
                        created_at TEXT NOT NULL,
                        bound_user_id INTEGER UNIQUE,
                        bound_username TEXT,
                        bound_full_name TEXT,
                        bound_at TEXT,
                        videos_created INTEGER DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT DEFAULT '',
                        voice TEXT DEFAULT 'vi-VN-HoaiMyNeural',
                        speech_rate TEXT DEFAULT '+10%',
                        platform TEXT DEFAULT 'auto',
                        dark_mode INTEGER DEFAULT 0,
                        background_file TEXT DEFAULT 'minecraft_parkour.mp4',
                        bgm_file TEXT DEFAULT 'meme_tiktok_viral.wav',
                        bgm_volume REAL DEFAULT 0.16,
                        sfx_volume REAL DEFAULT 0.70,
                        running_text TEXT DEFAULT '@Vietcaigido?',
                        running_text_mode TEXT DEFAULT 'top_left',
                        channel_badge INTEGER DEFAULT 0,
                        min_likes INTEGER DEFAULT 0,
                        auto_render INTEGER DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_config (
                        config_key TEXT PRIMARY KEY,
                        config_value TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Bot Config (Token, Admin IDs)
    # ------------------------------------------------------------------
    def set_config(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO bot_config (config_key, config_value) VALUES (?, ?) "
                    "ON CONFLICT(config_key) DO UPDATE SET config_value = excluded.config_value",
                    (key, value),
                )
                conn.commit()
            finally:
                conn.close()

    def get_config(self, key: str, default: str = "") -> str:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT config_value FROM bot_config WHERE config_key = ?", (key,)
                ).fetchone()
                return row["config_value"] if row else default
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Admin Verification (@citywin2026)
    # ------------------------------------------------------------------
    def is_admin(self, user_id: Optional[int], username: Optional[str]) -> bool:
        """
        Checks if the user is @citywin2026, @Tama839 or matches a stored admin user_id.
        Automatically records admin's numeric Telegram user_id on first seen.
        """
        clean_username = (username or "").lstrip("@").strip().lower()
        if clean_username in (ADMIN_USERNAME.lower(), "tama839"):
            if user_id:
                self.set_config("admin_user_id", str(user_id))
            return True

        saved_admin_id = self.get_config("admin_user_id", "")
        if user_id and saved_admin_id and str(user_id) == saved_admin_id:
            return True

        env_admin_id = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
        if user_id and env_admin_id and str(user_id) == env_admin_id:
            return True

        return False

    # ------------------------------------------------------------------
    # License Key Operations (1 Key = 1 User, Instant Revocation)
    # ------------------------------------------------------------------
    def create_key(
        self, note: str = "", created_by: str = "@citywin2026", custom_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generates a unique single-user license key."""
        with self._lock:
            conn = self._get_conn()
            try:
                if custom_key and custom_key.strip():
                    key_code = custom_key.strip().upper()
                else:
                    part1 = secrets.token_hex(2).upper()
                    part2 = secrets.token_hex(2).upper()
                    key_code = f"CW26-{part1}-{part2}"

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    """
                    INSERT INTO license_keys (key_code, note, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key_code, note.strip(), created_by, now_str),
                )
                conn.commit()
                return {
                    "key_code": key_code,
                    "note": note.strip(),
                    "created_by": created_by,
                    "created_at": now_str,
                    "bound_user_id": None,
                    "bound_username": None,
                    "bound_full_name": None,
                    "bound_at": None,
                    "videos_created": 0,
                }
            finally:
                conn.close()

    def activate_key(
        self, key_code: str, user_id: int, username: str = "", full_name: str = ""
    ) -> Tuple[bool, str]:
        """
        Binds a key to a single Telegram user_id.
        - If the key does not exist (or was deleted): fails.
        - If the key is already bound to a DIFFERENT user_id: fails (1 key = 1 user).
        - If the user already has another key bound: unbinds/replaces or informs them.
        """
        clean_key = key_code.strip().upper()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM license_keys WHERE UPPER(key_code) = ?", (clean_key,)
                ).fetchone()

                if not row:
                    return False, "❌ Mã Key không tồn tại hoặc đã bị Admin thu hồi/xóa!"

                bound_uid = row["bound_user_id"]
                if bound_uid is not None and int(bound_uid) != int(user_id):
                    return (
                        False,
                        "🚫 Mã Key này đã được kích hoạt bởi một người dùng khác! (Mỗi Key chỉ dùng cho 1 tài khoản duy nhất).",
                    )

                if bound_uid is not None and int(bound_uid) == int(user_id):
                    return True, f"✅ Bạn đã kích hoạt mã Key `{clean_key}` này rồi!"

                # If this user previously held a different key, unbind the old key or delete it
                conn.execute(
                    "UPDATE license_keys SET bound_user_id = NULL, bound_username = NULL, bound_full_name = NULL, bound_at = NULL WHERE bound_user_id = ?",
                    (user_id,),
                )

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    """
                    UPDATE license_keys
                    SET bound_user_id = ?, bound_username = ?, bound_full_name = ?, bound_at = ?
                    WHERE key_code = ?
                    """,
                    (
                        user_id,
                        (username or "").lstrip("@"),
                        full_name or "",
                        now_str,
                        row["key_code"],
                    ),
                )
                conn.commit()
                return (
                    True,
                    f"🎉 Kích hoạt thành công mã Key `{row['key_code']}`!\nTài khoản của bạn đã được mở khóa toàn bộ tính năng tạo video.",
                )
            finally:
                conn.close()

    def verify_user_access(
        self, user_id: int, username: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Real-time check executed before ANY bot action.
        Admin (@citywin2026) always has access.
        Regular users must have an active, non-deleted key bound to their user_id.
        """
        if self.is_admin(user_id, username):
            return True, "ADMIN"

        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT key_code FROM license_keys WHERE bound_user_id = ?",
                    (user_id,),
                ).fetchone()
                if row:
                    return True, row["key_code"]
                return False, None
            finally:
                conn.close()

    def delete_key(self, key_code: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Immediately deletes a key from the database.
        Any user bound to this key immediately loses access on their very next action.
        Returns (deleted_bool, previous_key_info).
        """
        clean_key = key_code.strip().upper()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM license_keys WHERE UPPER(key_code) = ?", (clean_key,)
                ).fetchone()
                if not row:
                    return False, None
                info = dict(row)
                conn.execute(
                    "DELETE FROM license_keys WHERE key_code = ?", (row["key_code"],)
                )
                conn.commit()
                return True, info
            finally:
                conn.close()

    def revoke_user(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Deletes the key belonging to a specific Telegram user_id."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT key_code FROM license_keys WHERE bound_user_id = ?",
                    (user_id,),
                ).fetchone()
                if not row:
                    return False, None
                key_code = row["key_code"]
                conn.execute(
                    "DELETE FROM license_keys WHERE bound_user_id = ?", (user_id,)
                )
                conn.commit()
                return True, key_code
            finally:
                conn.close()

    def increment_video_count(self, user_id: int) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE license_keys SET videos_created = videos_created + 1 WHERE bound_user_id = ?",
                    (user_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def list_keys(self) -> List[Dict[str, Any]]:
        """Returns all keys ordered by creation time descending."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM license_keys ORDER BY created_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Per-User Video Settings Management
    # ------------------------------------------------------------------
    def get_user_settings(self, user_id: int, username: str = "") -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
                ).fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO user_settings (user_id, username) VALUES (?, ?)",
                        (user_id, (username or "").lstrip("@")),
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
                    ).fetchone()
                data = dict(row)
                data["dark_mode"] = bool(data["dark_mode"])
                data["channel_badge"] = bool(data["channel_badge"])
                data["auto_render"] = bool(data["auto_render"])
                return data
            finally:
                conn.close()

    def update_user_setting(self, user_id: int, field: str, value: Any) -> Dict[str, Any]:
        allowed_fields = {
            "voice",
            "speech_rate",
            "platform",
            "dark_mode",
            "background_file",
            "bgm_file",
            "bgm_volume",
            "sfx_volume",
            "running_text",
            "running_text_mode",
            "channel_badge",
            "min_likes",
            "auto_render",
            "username",
        }
        if field not in allowed_fields:
            raise ValueError(f"Invalid setting field: {field}")

        # Ensure row exists first
        self.get_user_settings(user_id)

        with self._lock:
            conn = self._get_conn()
            try:
                if isinstance(value, bool):
                    value = 1 if value else 0
                conn.execute(
                    f"UPDATE user_settings SET {field} = ? WHERE user_id = ?",
                    (value, user_id),
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_user_settings(user_id)

    # ------------------------------------------------------------------
    # User Directory & Lookup (For Admin Operations)
    # ------------------------------------------------------------------
    def list_all_users(self) -> List[Dict[str, Any]]:
        """
        Returns a list of all distinct users known to the bot from user_settings and license_keys.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                users_map: Dict[int, str] = {}

                # 1. From user_settings
                rows = conn.execute(
                    "SELECT user_id, username FROM user_settings ORDER BY user_id DESC"
                ).fetchall()
                for r in rows:
                    uid = r["user_id"]
                    if uid:
                        users_map[int(uid)] = (r["username"] or "").strip()

                # 2. From license_keys (bound_user_id & bound_username)
                lic_rows = conn.execute(
                    "SELECT DISTINCT bound_user_id, bound_username FROM license_keys WHERE bound_user_id IS NOT NULL"
                ).fetchall()
                for lr in lic_rows:
                    b_uid = lr["bound_user_id"]
                    if b_uid:
                        uid_int = int(b_uid)
                        if uid_int not in users_map or not users_map[uid_int]:
                            users_map[uid_int] = (lr["bound_username"] or "").strip()

                result = [
                    {"user_id": uid, "username": uname}
                    for uid, uname in users_map.items()
                ]
                result.sort(key=lambda x: x["user_id"], reverse=True)
                return result
            finally:
                conn.close()

    def list_all_user_ids(self) -> List[int]:
        """Returns all distinct user IDs known to the system."""
        return [u["user_id"] for u in self.list_all_users()]

    def find_user_id(self, query: str) -> Optional[int]:
        """
        Resolves a user ID from either a numeric string ('12345678')
        or a Telegram username ('@Tama839' or 'Tama839').
        """
        clean = query.strip()
        if not clean:
            return None
        if clean.isdigit():
            return int(clean)

        clean_uname = clean.lstrip("@").lower()
        all_users = self.list_all_users()
        for u in all_users:
            if u["username"].lower() == clean_uname:
                return u["user_id"]
        return None
