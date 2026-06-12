"""Telegram bot wrapping the speech-analysis pipeline.

Responsibilities (all side-effects live here, not in the pipeline):
  - Only the OWNER's voice notes are analyzed; everyone else is ignored.
  - Each analysis is persisted to Turso.
  - Daily reminders: 6 AM IST always; 8 PM IST only if the owner hasn't
    recorded that day.
  - /stats shows the owner's progress trends.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import time

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import charts
import db
from db import IST
from pipeline import analyze_audio_file, analyze_call_file
from transcribe import OpenAIAuthError, check_openai_key

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

# Where failure alerts go: the group if configured, otherwise the owner's DM.
ALERT_CHAT_ID = GROUP_CHAT_ID if GROUP_CHAT_ID is not None else OWNER_USER_ID

# Optional reviewer — tagged in reminders to nudge the owner. This is also "the
# other person" (female) in call recordings and the only user allowed to press
# the "Mark complete" button.
_reviewer = os.environ.get("REVIEWER_USER_ID")
REVIEWER_USER_ID = int(_reviewer) if _reviewer else None
OTHER_USER_ID = REVIEWER_USER_ID
_reviewer_mention = (
    f'<a href="tg://user?id={REVIEWER_USER_ID}">Aardra</a>'
    if REVIEWER_USER_ID
    else "Aardra"
)
_REVIEWER_LINE = f"\n👀 {_reviewer_mention}, please remind Karneeshkar to do it!"

REMINDER_MORNING = (
    "⏰ Good morning! Time for your daily speech practice. "
    "Send a voice note and I'll analyze it. 🎙️" + _REVIEWER_LINE
)
REMINDER_EVENING = (
    "🌙 You haven't done your speech practice today. "
    "Send a voice note before the day ends! 🎙️" + _REVIEWER_LINE
)


async def _alert(bot, text: str) -> None:
    """Best-effort failure alert to the owner (group if set, else owner DM)."""
    try:
        await bot.send_message(chat_id=ALERT_CHAT_ID, text=text)
    except Exception:
        logger.exception("Failed to deliver alert")


def _complete_keyboard() -> InlineKeyboardMarkup | None:
    """The 'Mark complete' button — only useful when a reviewer is configured."""
    if OTHER_USER_ID is None:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Mark complete for today", callback_data="done")]]
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
        await status.edit_text(result["report"], reply_markup=_complete_keyboard())

        if db.is_configured():
            try:
                await db.save_analysis(
                    user.id, message.chat_id, result["analysis"], result["transcription"]
                )
            except Exception as exc:  # persistence must never break the user-facing reply
                logger.exception("Failed to persist analysis to Turso")
                await _alert(context.bot, f"🚨 Saved the report but FAILED to store metrics: {exc}")

    except OpenAIAuthError as exc:
        logger.error("OpenAI auth failure: %s", exc)
        await status.edit_text(
            f"🚨 OpenAI key problem — analysis is down.\n{exc}\n\n"
            "Update OPENAI_API_KEY and redeploy."
        )
    except Exception as exc:
        logger.exception("Analysis failed")
        await status.edit_text(f"❌ Analysis failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# --------------------------------------------------------------------------- #
# Call-recording handler (audio files — 2 speakers, owner analyzed)
# --------------------------------------------------------------------------- #
async def handle_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    # Access control: only the owner may submit call recordings.
    if user.id != OWNER_USER_ID:
        logger.info("Ignoring call recording from non-owner user %s", user.id)
        return

    media = message.audio or message.document
    if media is None:
        return

    status = await message.reply_text("Analyzing call... 📞")

    tmp_path = None
    try:
        tg_file = await media.get_file()
        name = getattr(media, "file_name", None) or ""
        suffix = os.path.splitext(name)[1] or ".mp3"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        result = analyze_call_file(tmp_path)
        await status.edit_text(result["report"], reply_markup=_complete_keyboard())

        if db.is_configured():
            try:
                await db.save_analysis(
                    user.id, message.chat_id, result["analysis"],
                    result["transcription"], source="call",
                )
            except Exception as exc:  # persistence must never break the user-facing reply
                logger.exception("Failed to persist call analysis to Turso")
                await _alert(context.bot, f"🚨 Saved the report but FAILED to store metrics: {exc}")

    except OpenAIAuthError as exc:
        logger.error("OpenAI auth failure: %s", exc)
        await status.edit_text(
            f"🚨 OpenAI key problem — analysis is down.\n{exc}\n\n"
            "Update OPENAI_API_KEY and redeploy."
        )
    except Exception as exc:
        logger.exception("Call analysis failed")
        await status.edit_text(f"❌ Call analysis failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# --------------------------------------------------------------------------- #
# "Mark complete" button — only the other person (reviewer) may press it
# --------------------------------------------------------------------------- #
async def mark_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    if OTHER_USER_ID is None or query.from_user.id != OTHER_USER_ID:
        await query.answer("Only Aardra can mark this complete 🙂", show_alert=True)
        return

    if not db.is_configured():
        await query.answer("Storage unavailable — can't record completion.", show_alert=True)
        return

    try:
        marked = await db.mark_today_complete(OWNER_USER_ID, OTHER_USER_ID)
    except Exception:
        logger.exception("Failed to mark today complete")
        await query.answer("Something went wrong — try again.", show_alert=True)
        return

    if not marked:
        await query.answer("No practice recorded yet today.", show_alert=True)
        return

    await query.answer("✅ Marked complete!")
    done_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Completed for today", callback_data="noop")]]
    )
    try:
        await query.edit_message_reply_markup(reply_markup=done_markup)
    except Exception:
        logger.exception("Failed to update button after completion")


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
        streaks = await db.get_streaks(OWNER_USER_ID)
        history = await db.get_history(OWNER_USER_ID, 30)
        completion = await db.completion_status_today(OWNER_USER_ID)
    except Exception as exc:
        await message.reply_text(f"❌ Could not load stats: {exc}")
        return

    if not stats:
        await message.reply_text("No analyses recorded yet. Send a voice note to start! 🎙️")
        return

    caption = _format_stats_caption(stats, streaks, completion)

    # Send a single progress graph (needs at least 2 points for a trend).
    if len(history) >= 2:
        try:
            png = charts.render_progress_chart(history)
            await message.reply_photo(photo=png, caption=caption)
            return
        except Exception:
            logger.exception("Failed to render progress chart; sending text only")

    await message.reply_text(
        caption + "\n\n(Send a couple more notes to unlock the trend graph 📈)"
    )


def _format_stats_caption(stats: dict, streaks: dict, completion: dict | None = None) -> str:
    today_mark = "✅ done today" if streaks["done_today"] else "⬜ not yet today"
    lines = [
        "📈 Your Progress",
        "──────────────────",
        f"🔥 Current streak: {streaks['current_streak']} day(s) — {today_mark}",
        f"🏆 Longest streak: {streaks['longest_streak']} day(s)",
        f"🗓️ Span: {stats['first_date']} → {stats['last_date']} ({stats['total']} notes)",
    ]
    if completion and completion["has_entry"]:
        if completion["completed_by"]:
            lines.append("✅ Confirmed complete by Aardra")
        else:
            lines.append("⬜ Awaiting Aardra's confirmation")
    if streaks["missed_count"]:
        shown = ", ".join(streaks["missed_days"][-5:])
        extra = streaks["missed_count"] - 5
        more = f" (+{extra} more)" if extra > 0 else ""
        lines.append(f"⚠️ Missed {streaks['missed_count']} day(s) in last 30: {shown}{more}")
    else:
        lines.append("✅ No missed days in the last 30 days!")
    lines += [
        "",
        f"Avg WPM: {stats['avg_wpm']:.0f}",
        f"Avg fillers: {stats['avg_filler_pct']:.1f}%",
        f"Avg clarity: {stats['avg_clarity']:.0f}/100",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Scheduled reminders
# --------------------------------------------------------------------------- #
async def remind_morning(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID, text=REMINDER_MORNING, parse_mode=ParseMode.HTML
    )


async def remind_evening(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        done = await db.has_done_today(OWNER_USER_ID)
    except Exception:
        logger.exception("Could not check today's status; sending reminder anyway")
        done = False
    if not done:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID, text=REMINDER_EVENING, parse_mode=ParseMode.HTML
        )


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all: alert the owner on ANY otherwise-unhandled failure."""
    logger.exception("Unhandled error", exc_info=context.error)
    await _alert(context.bot, f"🚨 Bot error: {context.error}")


async def _post_init(app: Application) -> None:
    if db.is_configured():
        await db.init_db()
        logger.info("Database initialized.")
    else:
        logger.warning("TURSO_DATABASE_URL not set — history/stats disabled.")

    # Validate the OpenAI key at startup so an expired/invalid key is flagged
    # immediately on (re)deploy, not only when the first voice note arrives.
    try:
        await asyncio.to_thread(check_openai_key)
        logger.info("OpenAI key validated.")
    except OpenAIAuthError as exc:
        logger.error("OpenAI key check failed: %s", exc)
        await _alert(app.bot, f"🚨 Startup alert: {exc}\nUpdate OPENAI_API_KEY and redeploy.")


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.VIDEO_NOTE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_call))
    app.add_handler(CallbackQueryHandler(mark_complete_callback, pattern="^done$"))
    app.add_error_handler(error_handler)

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
