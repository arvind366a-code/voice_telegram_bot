"""Pitch-based 2-speaker diarization for call recordings.

The bot's calls always have exactly two speakers: the owner (male, lower pitch)
and the other person (female, higher pitch). We don't run a real diarization
model — instead we estimate the fundamental frequency (F0) of each Whisper
segment via numpy autocorrelation and label the lower-pitch turns as the owner.

This is intentionally coarse (no overlap handling, no clustering) but cheap:
pydub decodes the audio (ffmpeg) and numpy does the rest — no heavy deps.
"""

from __future__ import annotations

import tempfile

import numpy as np
from pydub import AudioSegment

# Voiced-speech pitch ranges: male ~85–180 Hz, female ~165–255 Hz. We search
# F0 within this band and split owner (lower) from other (higher) around ~165 Hz.
_F0_MIN = 70.0
_F0_MAX = 300.0
_SPLIT_HZ = 165.0


def _load_mono(filepath: str) -> AudioSegment:
    return AudioSegment.from_file(filepath).set_channels(1)


def _segment_samples(audio: AudioSegment, start: float, end: float) -> np.ndarray:
    """Float samples (mono) for the [start, end] second window of ``audio``."""
    clip = audio[int(start * 1000): int(end * 1000)]
    samples = np.array(clip.get_array_of_samples(), dtype=np.float64)
    return samples


def _estimate_f0(samples: np.ndarray, sr: int) -> float:
    """Median fundamental frequency (Hz) of a voiced clip via autocorrelation.

    Returns 0.0 when no clear pitch is found (silence / unvoiced noise).
    """
    if samples.size < sr // 20:  # need at least ~50ms to find a pitch
        return 0.0

    samples = samples - samples.mean()
    if not np.any(samples):
        return 0.0

    # Lag bounds (in samples) corresponding to the F0 search band.
    min_lag = int(sr / _F0_MAX)
    max_lag = int(sr / _F0_MIN)
    if max_lag <= min_lag or samples.size <= max_lag:
        return 0.0

    corr = np.correlate(samples, samples, mode="full")[samples.size - 1:]
    if corr[0] <= 0:
        return 0.0

    window = corr[min_lag:max_lag]
    if window.size == 0:
        return 0.0

    peak_lag = min_lag + int(np.argmax(window))
    # Reject weak peaks (likely unvoiced) — require decent periodicity.
    if corr[peak_lag] < 0.3 * corr[0]:
        return 0.0

    return sr / peak_lag


def classify_segments(filepath: str, segments: list[dict]) -> list[dict]:
    """Tag each Whisper segment with a speaker label by pitch.

    Returns a copy of each segment dict with added keys:
        "speaker": "owner" | "other"
        "f0": float   # estimated Hz (0.0 if undetected)
    """
    if not segments:
        return []

    audio = _load_mono(filepath)
    sr = audio.frame_rate

    labeled: list[dict] = []
    for seg in segments:
        f0 = _estimate_f0(_segment_samples(audio, seg["start"], seg["end"]), sr)
        labeled.append({**seg, "f0": f0})

    voiced = [s["f0"] for s in labeled if s["f0"] > 0]
    # Relative fallback: if every detected pitch falls on one side of the fixed
    # split, use the median so the lower cluster is still treated as the owner.
    if voiced:
        lo, hi = min(voiced), max(voiced)
        split = _SPLIT_HZ if (lo < _SPLIT_HZ <= hi) else float(np.median(voiced))
    else:
        split = _SPLIT_HZ

    last_speaker = "owner"
    for s in labeled:
        if s["f0"] <= 0:
            s["speaker"] = last_speaker  # inherit across silent/unvoiced gaps
        else:
            s["speaker"] = "owner" if s["f0"] < split else "other"
            last_speaker = s["speaker"]

    return labeled


def build_owner_audio(filepath: str, owner_segments: list[dict]) -> str:
    """Concatenate the owner's segment slices into a temp WAV; return its path.

    Re-transcribing this contiguous owner-only audio lets the existing analysis
    run unchanged, with no inter-turn gaps masquerading as the owner's pauses.
    """
    audio = _load_mono(filepath)
    owner = AudioSegment.empty()
    for seg in owner_segments:
        owner += audio[int(seg["start"] * 1000): int(seg["end"] * 1000)]

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        out_path = tmp.name
    owner.export(out_path, format="wav")
    return out_path
