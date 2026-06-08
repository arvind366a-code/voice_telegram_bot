# Voice Telegram Bot — Speech Analysis

A Telegram bot that lives in a group chat. When the owner sends a voice note, the
bot transcribes it with the OpenAI Whisper API, analyzes the speech (WPM, fillers,
pauses, clarity, pace consistency), and replies with a structured report. Every
analysis is stored in Turso so you can track progress over time, and the bot
sends daily practice reminders.

The analysis pipeline (`transcribe → analyze → report`) is fully modular and runs
**standalone** without Telegram — see `test_local.py`.

## Features
- 🎙️ Whisper transcription with word + segment timestamps (`whisper-1`, `verbose_json`)
- 📊 WPM (+ per-10s rolling windows), filler words, pauses, clarity score, pace consistency
- 🔒 Access control: **only the owner's** voice notes are analyzed; an optional reviewer just sees the reports
- 🗄️ History persisted to **Turso (libSQL)**
- ⏰ Daily reminders: **6 AM IST** always, **8 PM IST** only if you haven't recorded that day
- 📈 `/stats` command for progress trends

## Project structure
```
bot.py          Telegram wrapper: handlers, reminders, access control, /stats
pipeline.py     analyze_audio_file() — pure: transcribe → analyze → report
transcribe.py   OpenAI Whisper API
analyze.py      WPM, fillers, pauses, clarity, pace consistency
report.py       Formats the analysis into a Telegram message
db.py           Turso/libSQL persistence
test_local.py   Standalone CLI test (no Telegram, no DB)
```

## Prerequisites
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- `ffmpeg` installed at system level (for `pydub`):
  - Arch: `sudo pacman -S ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`
- An OpenAI API key
- A Telegram bot token
- A Turso database + auth token (`turso db create`, `turso db show --url`, `turso db tokens create`)

## Setup

1. **Install dependencies**
   ```bash
   uv sync
   ```

2. **Create the bot with @BotFather**
   - `/newbot` → follow prompts → copy the token
   - `/setprivacy` → select your bot → **Disable** (required so it can read group voice notes)

3. **Find the IDs you need**
   - Your user id & the group chat id: add [@userinfobot](https://t.me/userinfobot) or [@RawDataBot](https://t.me/RawDataBot) to the group, or check the bot logs after sending a message. Group chat ids are negative numbers.

4. **Configure `.env`**
   ```bash
   cp .env.example .env
   ```
   Fill in `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `OWNER_USER_ID`,
   (optional) `REVIEWER_USER_ID`, `GROUP_CHAT_ID`, `TURSO_DATABASE_URL`,
   `TURSO_AUTH_TOKEN`.

## Run the local test first
This validates your OpenAI key and the whole pipeline **before** touching Telegram.
It downloads its own speech sample and needs nothing else.
```bash
uv run test_local.py
```
It prints the report and writes raw numbers to `test_samples/analysis_output.json`.

## Run the bot
```bash
uv run bot.py
```
Then add the bot to your group, send a voice note, and get your analysis.

## Deploy on Coolify (Docker)

This is an **outbound-only Telegram long-polling worker** — no published ports, no
healthcheck. Files: `Dockerfile` and `docker-compose.yaml`.

1. Push this repo to GitHub (or your Git provider).
2. In Coolify: **New Resource → Docker Compose** (or Dockerfile), point it at the repo.
3. Add these **environment variables** in the Coolify UI:
   `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `OWNER_USER_ID`, `REVIEWER_USER_ID`,
   `GROUP_CHAT_ID`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
4. Deploy. Logs stream live (`PYTHONUNBUFFERED=1`).

⚠️ **One poller per bot token.** Telegram allows only one `getUpdates` consumer at
a time. Stop any locally-running `bot.py` before the Coolify instance goes live, or
you'll get HTTP 409 conflicts.

Build/run locally to test:
```bash
docker build -t voice-telegram-bot .
docker run --rm --env-file .env voice-telegram-bot
```

## Notes
- **Reviewer**: set `REVIEWER_USER_ID` so a second person can read reports in the
  group. Their own voice notes are ignored — only the owner's are analyzed.
- **Reminders** are posted to `GROUP_CHAT_ID`: 6 AM IST every day, plus 8 PM IST
  only when no voice note was recorded that day (timezone: `Asia/Kolkata`).
- **`/stats`** (owner only) replies with a **progress graph** (WPM / clarity /
  filler trends over time) plus a caption showing your **current & longest
  streak**, **missed days** in the last 30 days, and running averages.
- **Failure alerts**: the bot validates the OpenAI key at startup and flags it if
  invalid/expired/out-of-quota, and a global error handler alerts you (in the
  group, or your DM) on any other failure — including a failed metrics save.
