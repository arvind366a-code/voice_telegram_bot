# syntax=docker/dockerfile:1

# uv + Python 3.12 (Debian slim).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# ffmpeg: required by pydub for audio handling.
# ca-certificates: outbound HTTPS (Telegram / OpenAI / Turso).
# tzdata: IST timezone for the daily reminders (zoneinfo Asia/Kolkata).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Don't buffer stdout/stderr so logs show up live in Coolify.
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first, against the lockfile, for layer caching.
# Source is copied afterwards so code changes don't bust the dep layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Application source. README.md satisfies the readme declared in pyproject.toml.
COPY README.md ./
COPY transcribe.py analyze.py report.py pipeline.py db.py bot.py ./

# Secrets and runtime config come from the environment (Coolify env vars),
# never from a baked-in .env.
ENV TZ=Asia/Kolkata

# Outbound-only Telegram long-polling worker: no port, no HTTP server.
CMD ["uv", "run", "--no-sync", "--no-dev", "python", "bot.py"]
