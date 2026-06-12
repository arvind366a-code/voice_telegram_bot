"""Turso / libSQL persistence for historical speech metrics.

Isolated here so the analysis pipeline stays pure. The bot is the only caller.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
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
        # Idempotent migrations for existing deployments (CREATE IF NOT EXISTS
        # above won't add columns to a pre-existing table).
        for ddl in (
            "ALTER TABLE analyses ADD COLUMN source TEXT",
            "ALTER TABLE analyses ADD COLUMN completed_by INTEGER",
            "ALTER TABLE analyses ADD COLUMN completed_at TEXT",
        ):
            try:
                await client.execute(ddl)
            except Exception:
                pass  # column already exists


async def save_analysis(
    user_id: int, chat_id: int, analysis: dict, transcription: dict, source: str = "voice"
) -> None:
    """Flatten an analysis dict and insert one row.

    ``source`` records which flow produced the row: "voice" (solo note) or
    "call" (2-speaker recording).
    """
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
                clarity, pace_std, pace_label, transcript, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source,
            ],
        )


async def mark_today_complete(owner_id: int, reviewer_id: int) -> bool:
    """Mark today's (IST) entries for ``owner_id`` as confirmed by the reviewer.

    Returns True if at least one row was updated (i.e. there was an entry today).
    """
    async with _client() as client:
        rs = await client.execute(
            """
            UPDATE analyses
            SET completed_by = ?, completed_at = ?
            WHERE user_id = ? AND created_date_ist = ?
            """,
            [reviewer_id, datetime.now(timezone.utc).isoformat(), owner_id, _today_ist()],
        )
        return bool(getattr(rs, "rows_affected", 0))


async def completion_status_today(owner_id: int) -> dict:
    """Today's (IST) entry + confirmation status for ``owner_id``.

    Returns {"has_entry": bool, "completed_by": int | None}.
    """
    async with _client() as client:
        rs = await client.execute(
            """
            SELECT MAX(completed_by) FROM analyses
            WHERE user_id = ? AND created_date_ist = ?
            """,
            [owner_id, _today_ist()],
        )
        count_rs = await client.execute(
            "SELECT COUNT(*) FROM analyses WHERE user_id = ? AND created_date_ist = ?",
            [owner_id, _today_ist()],
        )
    has_entry = bool(count_rs.rows and int(count_rs.rows[0][0]) > 0)
    completed_by = rs.rows[0][0] if rs.rows else None
    return {"has_entry": has_entry, "completed_by": completed_by}


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


async def get_history(user_id: int, limit: int = 30) -> list:
    """Return the user's last ``limit`` analyses in chronological order (oldest
    first) for plotting a progress graph."""
    async with _client() as client:
        rs = await client.execute(
            """
            SELECT created_at, created_date_ist, avg_wpm, filler_pct, clarity
            FROM analyses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            [user_id, limit],
        )
    rows = [
        {
            "created_at": r[0],
            "date": r[1],
            "avg_wpm": r[2] or 0.0,
            "filler_pct": r[3] or 0.0,
            "clarity": r[4] or 0.0,
        }
        for r in rs.rows
    ]
    rows.reverse()  # chronological
    return rows


async def get_streaks(user_id: int, lookback_days: int = 30) -> dict:
    """Compute practice streaks and missed days from distinct practice dates (IST).

    Returns:
        {
          "done_today": bool,
          "current_streak": int,   # consecutive days up to today (or yesterday)
          "longest_streak": int,
          "missed_days": [iso, ...],   # days with no practice in the window
          "missed_count": int,
        }
    """
    async with _client() as client:
        rs = await client.execute(
            "SELECT DISTINCT created_date_ist FROM analyses WHERE user_id = ? ORDER BY created_date_ist",
            [user_id],
        )

    days = set()
    for r in rs.rows:
        try:
            days.add(date.fromisoformat(r[0]))
        except (TypeError, ValueError):
            continue

    today = datetime.now(IST).date()
    if not days:
        return {
            "done_today": False,
            "current_streak": 0,
            "longest_streak": 0,
            "missed_days": [],
            "missed_count": 0,
        }

    done_today = today in days

    # Current streak: count back from today; if today isn't done yet, the streak
    # is still "alive" if yesterday was done.
    current = 0
    cursor = today if done_today else today - timedelta(days=1)
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)

    # Longest streak across all history.
    longest = 0
    for d in days:
        if (d - timedelta(days=1)) not in days:  # start of a run
            run = 1
            nxt = d + timedelta(days=1)
            while nxt in days:
                run += 1
                nxt += timedelta(days=1)
            longest = max(longest, run)

    # Missed days within the lookback window (from the later of first-practice or
    # window start, up to yesterday — today isn't "missed" until it ends).
    window_start = max(min(days), today - timedelta(days=lookback_days))
    missed = []
    d = window_start
    while d < today:
        if d not in days:
            missed.append(d.isoformat())
        d += timedelta(days=1)

    return {
        "done_today": done_today,
        "current_streak": current,
        "longest_streak": longest,
        "missed_days": missed,
        "missed_count": len(missed),
    }
