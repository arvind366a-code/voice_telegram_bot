"""Turso / libSQL persistence for historical speech metrics.

Isolated here so the analysis pipeline stays pure. The bot is the only caller.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import libsql_client

IST = ZoneInfo("Asia/Kolkata")


def is_configured() -> bool:
    """True if Turso credentials are present; otherwise persistence is skipped."""
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def _client():
    url = os.environ["TURSO_DATABASE_URL"]
    token = os.environ.get("TURSO_AUTH_TOKEN")
    # Use the HTTPS transport (libsql:// would use a WebSocket that some Turso
    # endpoints reject with a 400 handshake). https:// speaks hrana-over-HTTP.
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    # create_client returns an async client usable as an async context manager.
    return libsql_client.create_client(url=url, auth_token=token)


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


async def init_db() -> None:
    """Create the analyses table if it does not exist."""
    async with _client() as client:
        await client.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                created_date_ist TEXT NOT NULL,
                user_id INTEGER,
                chat_id INTEGER,
                duration REAL,
                word_count INTEGER,
                avg_wpm REAL,
                wpm_flag TEXT,
                filler_count INTEGER,
                filler_pct REAL,
                pause_short INTEGER,
                pause_medium INTEGER,
                pause_long INTEGER,
                total_pause_time REAL,
                clarity REAL,
                pace_std REAL,
                pace_label TEXT,
                transcript TEXT
            )
            """
        )


async def save_analysis(user_id: int, chat_id: int, analysis: dict, transcription: dict) -> None:
    """Flatten an analysis dict and insert one row."""
    wpm = analysis["wpm"]
    fillers = analysis["fillers"]
    pauses = analysis["pauses"]

    async with _client() as client:
        await client.execute(
            """
            INSERT INTO analyses (
                created_at, created_date_ist, user_id, chat_id, duration,
                word_count, avg_wpm, wpm_flag, filler_count, filler_pct,
                pause_short, pause_medium, pause_long, total_pause_time,
                clarity, pace_std, pace_label, transcript
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now(timezone.utc).isoformat(),
                _today_ist(),
                user_id,
                chat_id,
                analysis["duration"],
                analysis["word_count"],
                wpm["average"],
                wpm["flag"],
                fillers["total"],
                fillers["percentage"],
                pauses["short"],
                pauses["medium"],
                pauses["long"],
                pauses["total_pause_time"],
                analysis["clarity"]["score"],
                analysis["pace_consistency"]["std_dev"],
                analysis["pace_consistency"]["label"],
                (transcription.get("text") or "").strip(),
            ],
        )


async def has_done_today(user_id: int) -> bool:
    """True if the user has at least one analysis stored for today (IST)."""
    async with _client() as client:
        rs = await client.execute(
            "SELECT 1 FROM analyses WHERE user_id = ? AND created_date_ist = ? LIMIT 1",
            [user_id, _today_ist()],
        )
        return len(rs.rows) > 0


async def get_stats(user_id: int, n: int = 10) -> dict | None:
    """Summarize the user's last ``n`` analyses for the /stats command."""
    async with _client() as client:
        total_rs = await client.execute(
            "SELECT COUNT(*) FROM analyses WHERE user_id = ?", [user_id]
        )
        total = int(total_rs.rows[0][0]) if total_rs.rows else 0
        if total == 0:
            return None

        rs = await client.execute(
            """
            SELECT created_date_ist, avg_wpm, filler_pct, clarity, pace_label
            FROM analyses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            [user_id, n],
        )

    rows = [
        {
            "date": r[0],
            "avg_wpm": r[1] or 0.0,
            "filler_pct": r[2] or 0.0,
            "clarity": r[3] or 0.0,
            "pace_label": r[4] or "",
        }
        for r in rs.rows
    ]

    def avg(key: str) -> float:
        return sum(r[key] for r in rows) / len(rows) if rows else 0.0

    return {
        "total": total,
        "window": len(rows),
        "first_date": rows[-1]["date"],
        "last_date": rows[0]["date"],
        "avg_wpm": round(avg("avg_wpm"), 1),
        "avg_filler_pct": round(avg("filler_pct"), 1),
        "avg_clarity": round(avg("clarity"), 1),
        "recent": rows,
    }
