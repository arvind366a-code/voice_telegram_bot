"""OpenAI Whisper API transcription.

Returns a normalized dict so downstream analysis never has to know about the
OpenAI SDK response shape.
"""

from __future__ import annotations

import os

from openai import (
    OpenAI,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)


class OpenAIAuthError(RuntimeError):
    """Raised when the OpenAI key is missing, invalid, expired, or out of quota.

    Distinct from generic RuntimeError so the bot can fire a dedicated alert.
    """


def check_openai_key() -> None:
    """Validate the OpenAI key. Best-effort: only raises on a definitive
    auth/quota failure, not on transient network errors.

    Raises:
        OpenAIAuthError: if the key is unset, invalid/expired, or out of quota.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise OpenAIAuthError("OPENAI_API_KEY is not set.")
    try:
        OpenAI().models.list()
    except (AuthenticationError, PermissionDeniedError) as exc:
        raise OpenAIAuthError(f"OpenAI API key invalid or expired: {exc}") from exc
    except RateLimitError as exc:
        if "insufficient_quota" in str(exc).lower() or "quota" in str(exc).lower():
            raise OpenAIAuthError(f"OpenAI quota exhausted / billing issue: {exc}") from exc
        return  # transient rate limit — not a key problem
    except Exception:
        return  # network/transient — don't false-alarm at startup


def transcribe(filepath: str) -> dict:
    """Transcribe an audio file with word + segment level timestamps.

    Accepts wav/ogg/mp3/mp4 (the Whisper API handles all of these directly).

    Returns:
        {
          "text": str,
          "segments": [{start, end, text, avg_logprob, no_speech_prob}, ...],
          "words": [{word, start, end}, ...],
          "duration": float,   # last word end, else last segment end, else 0.0
        }

    Raises:
        RuntimeError: with a clear message if the API call fails.
    """
    client = OpenAI()  # reads OPENAI_API_KEY from the environment

    try:
        with open(filepath, "rb") as audio:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
    except FileNotFoundError:
        raise RuntimeError(f"Audio file not found: {filepath}")
    except (AuthenticationError, PermissionDeniedError) as exc:
        raise OpenAIAuthError(f"OpenAI API key invalid or expired: {exc}") from exc
    except RateLimitError as exc:
        if "insufficient_quota" in str(exc).lower() or "quota" in str(exc).lower():
            raise OpenAIAuthError(f"OpenAI quota exhausted / billing issue: {exc}") from exc
        raise RuntimeError(f"OpenAI rate limited — try again shortly: {exc}") from exc
    except Exception as exc:  # surface a clean, actionable error upstream
        raise RuntimeError(f"Whisper transcription failed: {exc}") from exc

    return _normalize(resp)


def _normalize(resp) -> dict:
    """Flatten the SDK response (pydantic objects) into plain dicts."""
    segments = []
    for seg in getattr(resp, "segments", None) or []:
        segments.append(
            {
                "start": _num(getattr(seg, "start", None)),
                "end": _num(getattr(seg, "end", None)),
                "text": getattr(seg, "text", "") or "",
                "avg_logprob": _num(getattr(seg, "avg_logprob", None)),
                "no_speech_prob": _num(getattr(seg, "no_speech_prob", None)),
            }
        )

    words = []
    for w in getattr(resp, "words", None) or []:
        words.append(
            {
                "word": getattr(w, "word", "") or "",
                "start": _num(getattr(w, "start", None)),
                "end": _num(getattr(w, "end", None)),
            }
        )

    # Prefer the API-reported duration, then last word end, then last segment end.
    duration = _num(getattr(resp, "duration", None))
    if not duration and words:
        duration = words[-1]["end"]
    if not duration and segments:
        duration = segments[-1]["end"]

    return {
        "text": getattr(resp, "text", "") or "",
        "segments": segments,
        "words": words,
        "duration": float(duration or 0.0),
    }


def _num(value) -> float:
    """Coerce a possibly-None numeric field to float (0.0 fallback)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
