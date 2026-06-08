"""Telegram bot wrapping the speech-analysis pipeline.

Responsibilities (all side-effects live here, not in the pipeline):
  - Only the OWNER's voice notes are analyzed; everyone else is ignored.
  - Each analysis is persisted to Turso.
  - Daily reminders: 6 AM IST always; 8 PM IST only if the owner hasn't
    recorded that day.
  - /stats shows the owner's progress trends.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import time

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from db import IST
from pipeline import analyze_audio_file

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_USER_ID = int(os.environ["OWNER_USER_ID"])
# Optional: reminders are scheduled only when a group chat id is configured.
_group = os.environ.get("GROUP_CHAT_ID")
GROUP_CHAT_ID = int(_group) if _group else None

REMINDER_MORNING = (
    "⏰ Good morning! Time for your daily speech practice. "
    "Send a voice note and I'll analyze it. 🎙️"
)
REMINDER_EVENING = (
    "🌙 You haven't done your speech practice today. "
    "Send a voice note before the day ends! 🎙️"
)


# --------------------------------------------------------------------------- #
# Voice / video-note handler
# --------------------------------------------------------------------------- #
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    # Access control: only the owner's voice notes are analyzed.
    if user.id != OWNER_USER_ID:
        logger.info("Ignoring voice note from non-owner user %s", user.id)
        return

    media = message.voice or message.video_note
    if media is None:
        return

    status = await message.reply_text("Analyzing... 🎙️")

    tmp_path = None
    try:
        tg_file = await media.get_file()
        suffix = ".ogg" if message.voice else ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        result = analyze_audio_file(tmp_path)
        await status.edit_text(result["report"])

        if db.is_configured():
            try:
                await db.save_analysis(
                    user.id, message.chat_id, result["analysis"], result["transcription"]
                )
            except Exception:  # persistence must never break the user-facing reply
                logger.exception("Failed to persist analysis to Turso")

    except Exception as exc:
        logger.exception("Analysis failed")
        await status.edit_text(f"❌ Analysis failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# --------------------------------------------------------------------------- #
# /stats command (owner only)
# --------------------------------------------------------------------------- #
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or user.id != OWNER_USER_ID:
        return

    if not db.is_configured():
        await message.reply_text("📊 Stats are unavailable — Turso is not configured.")
        return

    try:
        stats = await db.get_stats(OWNER_USER_ID)
    except Exception as exc:
        await message.reply_text(f"❌ Could not load stats: {exc}")
        return

    if not stats:
        await message.reply_text("No analyses recorded yet. Send a voice note to start! 🎙️")
        return

    lines = [
        "📈 Your Progress",
        "──────────────────",
        f"Total notes: {stats['total']} (showing last {stats['window']})",
        f"Span: {stats['first_date']} → {stats['last_date']}",
        "",
        f"Avg WPM: {stats['avg_wpm']:.0f}",
        f"Avg fillers: {stats['avg_filler_pct']:.1f}%",
        f"Avg clarity: {stats['avg_clarity']:.0f}/100",
        "",
        "Recent (newest first):",
    ]
    for r in stats["recent"]:
        lines.append(
            f"  {r['date']}: {r['avg_wpm']:.0f}wpm, "
            f"{r['filler_pct']:.1f}% fillers, clarity {r['clarity']:.0f}"
        )
    await message.reply_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# Scheduled reminders
# --------------------------------------------------------------------------- #
async def remind_morning(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=REMINDER_MORNING)


async def remind_evening(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        done = await db.has_done_today(OWNER_USER_ID)
    except Exception:
        logger.exception("Could not check today's status; sending reminder anyway")
        done = False
    if not done:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=REMINDER_EVENING)


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
async def _post_init(app: Application) -> None:
    if db.is_configured():
        await db.init_db()
        logger.info("Database initialized.")
    else:
        logger.warning("TURSO_DATABASE_URL not set — history/stats disabled.")


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.VIDEO_NOTE, handle_voice))

    if GROUP_CHAT_ID is not None:
        app.job_queue.run_daily(remind_morning, time=time(6, 0, tzinfo=IST))
        app.job_queue.run_daily(remind_evening, time=time(20, 0, tzinfo=IST))
        logger.info("Daily reminders scheduled (06:00 / 20:00 IST).")
    else:
        logger.warning("GROUP_CHAT_ID not set — daily reminders disabled.")

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
