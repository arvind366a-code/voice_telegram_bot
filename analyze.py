"""Speech analysis: WPM, fillers, pauses, clarity, pace consistency.

Pure functions. Everything degrades gracefully when the words/segments lists are
empty (returns zeros and empty lists rather than raising).
"""

from __future__ import annotations

import re
from statistics import pstdev

# Multi-word phrases must come before their single-word substrings so the regex
# alternation prefers the longer match (e.g. "kind of" before a bare "kind").
FILLERS = [
    "you know",
    "kind of",
    "sort of",
    "um",
    "uh",
    "like",
    "basically",
    "literally",
    "right",
    "so",
    "actually",
    "honestly",
]

WINDOW_SECONDS = 10.0


def analyze(transcription: dict) -> dict:
    """Run the full analysis suite over a transcription dict."""
    text = transcription.get("text", "") or ""
    words = transcription.get("words", []) or []
    segments = transcription.get("segments", []) or []
    duration = float(transcription.get("duration", 0.0) or 0.0)

    return {
        "duration": round(duration, 2),
        "word_count": len(words),
        "wpm": _wpm(words, duration),
        "fillers": _fillers(text, len(words)),
        "pauses": _pauses(words),
        "clarity": _clarity(segments),
        "pace_consistency": _pace_consistency(words, duration),
    }


# --------------------------------------------------------------------------- #
# 1. WPM (+ rolling per-10s windows)
# --------------------------------------------------------------------------- #
def _wpm(words: list, duration: float) -> dict:
    total = len(words)
    avg = (total / (duration / 60.0)) if duration > 0 else 0.0

    rolling = _rolling_wpm(words, duration)

    return {
        "average": round(avg, 1),
        "flag": _wpm_flag(avg),
        "rolling": rolling,  # [{start, end, wpm}, ...]
        "window_seconds": WINDOW_SECONDS,
    }


def _rolling_wpm(words: list, duration: float) -> list:
    """Bucket words into fixed 10s windows by their start time."""
    if not words or duration <= 0:
        return []

    n_windows = max(1, int((duration + WINDOW_SECONDS - 1e-9) // WINDOW_SECONDS))
    counts = [0] * n_windows
    for w in words:
        idx = int(w["start"] // WINDOW_SECONDS)
        idx = min(idx, n_windows - 1)  # clamp the final partial window
        counts[idx] += 1

    # Merge a short trailing window (< half a window) into the previous one so a
    # tiny tail doesn't produce a degenerate bar or an extreme WPM that would
    # skew the pace-consistency std dev.
    if n_windows > 1 and (duration - (n_windows - 1) * WINDOW_SECONDS) < WINDOW_SECONDS / 2:
        counts[-2] += counts[-1]
        counts.pop()
        n_windows -= 1

    rolling = []
    for i, c in enumerate(counts):
        start = i * WINDOW_SECONDS
        end = min((i + 1) * WINDOW_SECONDS, round(duration, 2)) if i < n_windows - 1 else round(duration, 2)
        span = end - start
        wpm = (c / (span / 60.0)) if span > 0 else 0.0
        rolling.append({"start": round(start, 1), "end": round(end, 1), "wpm": round(wpm, 1)})
    return rolling


def _wpm_flag(avg: float) -> str:
    if avg <= 160:
        return "normal"
    if avg <= 200:
        return "fast"
    return "very_fast"


# --------------------------------------------------------------------------- #
# 2. Filler words
# --------------------------------------------------------------------------- #
def _fillers(text: str, total_words: int) -> dict:
    lowered = text.lower()
    breakdown: dict[str, int] = {}
    # (start_char_offset, filler) so we can order hits to derive word positions.
    hits: list[tuple[int, str]] = []

    for filler in FILLERS:
        pattern = r"\b" + re.escape(filler) + r"\b"
        for m in re.finditer(pattern, lowered):
            breakdown[filler] = breakdown.get(filler, 0) + 1
            hits.append((m.start(), filler))

    hits.sort()
    total = len(hits)

    # Approximate word index of each filler by counting words before its offset.
    positions = [len(lowered[:offset].split()) for offset, _ in hits]

    pct = (total / total_words * 100.0) if total_words else 0.0
    breakdown = dict(sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True))

    return {
        "total": total,
        "percentage": round(pct, 1),
        "breakdown": breakdown,
        "positions": positions,
    }


# --------------------------------------------------------------------------- #
# 3. Pauses
# --------------------------------------------------------------------------- #
def _pauses(words: list) -> dict:
    empty = {
        "short": 0,
        "medium": 0,
        "long": 0,
        "average_duration": 0.0,
        "total_pause_time": 0.0,
        "longest": {"duration": 0.0, "between": None},
    }
    if len(words) < 2:
        return empty

    durations: list[float] = []
    counts = {"short": 0, "medium": 0, "long": 0}
    longest = {"duration": 0.0, "between": None}

    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap < 0.3:
            continue  # ignored: natural inter-word spacing
        durations.append(gap)
        if gap <= 0.8:
            counts["short"] += 1
        elif gap <= 2.0:
            counts["medium"] += 1
        else:
            counts["long"] += 1

        if gap > longest["duration"]:
            longest = {
                "duration": round(gap, 2),
                "between": [words[i]["word"].strip(), words[i + 1]["word"].strip()],
            }

    total = sum(durations)
    avg = (total / len(durations)) if durations else 0.0

    return {
        **counts,
        "average_duration": round(avg, 2),
        "total_pause_time": round(total, 2),
        "longest": longest,
    }


# --------------------------------------------------------------------------- #
# 4. Clarity score (0-100)
# --------------------------------------------------------------------------- #
def _clarity(segments: list) -> dict:
    if not segments:
        return {"score": 0.0, "per_segment": []}

    per_segment = []
    weighted_sum = 0.0
    weight_total = 0

    for seg in segments:
        raw = (1 + seg.get("avg_logprob", 0.0)) * (1 - seg.get("no_speech_prob", 0.0)) * 100.0
        score = _clamp(raw, 0.0, 100.0)
        wc = max(1, len((seg.get("text") or "").split()))
        per_segment.append(
            {
                "start": round(seg.get("start", 0.0), 1),
                "end": round(seg.get("end", 0.0), 1),
                "score": round(score, 1),
                "text": (seg.get("text") or "").strip(),
            }
        )
        weighted_sum += score * wc
        weight_total += wc

    score = (weighted_sum / weight_total) if weight_total else 0.0
    return {"score": round(score, 1), "per_segment": per_segment}


# --------------------------------------------------------------------------- #
# 5. Speaking pace consistency
# --------------------------------------------------------------------------- #
def _pace_consistency(words: list, duration: float) -> dict:
    rolling = _rolling_wpm(words, duration)
    values = [w["wpm"] for w in rolling if w["wpm"] > 0]
    if len(values) < 2:
        return {"std_dev": 0.0, "label": "consistent"}

    std = pstdev(values)
    if std <= 25:
        label = "consistent"
    elif std <= 50:
        label = "moderate"
    else:
        label = "erratic"
    return {"std_dev": round(std, 1), "label": label}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
