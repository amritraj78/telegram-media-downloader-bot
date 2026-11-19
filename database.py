import sqlite3
import time
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "bot_data.db"


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Enable WAL mode for high concurrency
            cursor.execute("PRAGMA journal_mode=WAL;")
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    joined_at REAL,
                    total_downloads INTEGER DEFAULT 0,
                    is_banned INTEGER DEFAULT 0
                )
            """)

            # Downloads log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    platform TEXT,
                    media_type TEXT,
                    downloaded_at REAL
                )
            """)
            conn.commit()

    def add_or_update_user(self, user_id: int, username: Optional[str], first_name: Optional[str]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (user_id, username, first_name, joined_at, total_downloads, is_banned)
                VALUES (?, ?, ?, ?, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
            """, (user_id, username or "", first_name or "", time.time()))
            conn.commit()

    def increment_download(self, user_id: int, platform: str, media_type: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET total_downloads = total_downloads + 1 WHERE user_id = ?
            """, (user_id,))
            cursor.execute("""
                INSERT INTO download_logs (user_id, platform, media_type, downloaded_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, platform, media_type, time.time()))
            conn.commit()

    def is_user_banned(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row and row["is_banned"] == 1)

    def set_user_ban(self, user_id: int, ban_status: bool):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if ban_status else 0, user_id))
            conn.commit()

    def get_all_user_ids(self) -> List[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
            rows = cursor.fetchall()
            return [row["user_id"] for row in rows]

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = cursor.fetchone()["total_users"]

            cursor.execute("SELECT COUNT(*) as banned_users FROM users WHERE is_banned = 1")
            banned_users = cursor.fetchone()["banned_users"]

            cursor.execute("SELECT COUNT(*) as total_downloads FROM download_logs")
            total_downloads = cursor.fetchone()["total_downloads"]

            # Today's downloads
            start_of_today = time.time() - 86400
            cursor.execute("SELECT COUNT(*) as today_downloads FROM download_logs WHERE downloaded_at >= ?", (start_of_today,))
            today_downloads = cursor.fetchone()["today_downloads"]

            return {
                "total_users": total_users,
                "banned_users": banned_users,
                "total_downloads": total_downloads,
                "today_downloads": today_downloads
            }


db = Database()
