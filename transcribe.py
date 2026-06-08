"""OpenAI Whisper API transcription.

Returns a normalized dict so downstream analysis never has to know about the
OpenAI SDK response shape.
"""

from __future__ import annotations

from openai import OpenAI


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
