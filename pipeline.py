"""Core analysis pipeline: transcribe -> analyze -> report.

Purely functional and Telegram-free. Both bot.py and test_local.py call
``analyze_audio_file`` so there is a single source of truth.
"""

from __future__ import annotations

import os

import diarize
from analyze import analyze
from report import format_call_report, format_report
from transcribe import transcribe


def analyze_audio_file(filepath: str) -> dict:
    """Run the full pipeline on an audio file.

    Returns:
        {
          "transcription": <transcribe output>,
          "analysis": <analyze output>,
          "report": <formatted string>,
        }
    """
    transcription = transcribe(filepath)
    analysis = analyze(transcription)
    report = format_report(transcription, analysis)
    return {
        "transcription": transcription,
        "analysis": analysis,
        "report": report,
    }


def analyze_call_file(filepath: str) -> dict:
    """Run the pipeline on a 2-speaker call recording.

    Splits speakers by pitch (owner = lower), then analyzes ONLY the owner's
    turns. The other person's words appear in the transcript but are not scored.

    Returns the same shape as ``analyze_audio_file`` plus:
        "labeled": [{**segment, "speaker", "f0"}, ...]   # full diarized call
    """
    full = transcribe(filepath)
    labeled = diarize.classify_segments(filepath, full["segments"])

    owner_segments = [s for s in labeled if s["speaker"] == "owner"]
    # If diarization found essentially one speaker, fall back to the whole audio.
    if not owner_segments or len(owner_segments) == len(labeled):
        owner_tx = full
    else:
        owner_wav = diarize.build_owner_audio(filepath, owner_segments)
        try:
            owner_tx = transcribe(owner_wav)
        finally:
            if os.path.exists(owner_wav):
                os.remove(owner_wav)

    analysis = analyze(owner_tx)
    report = format_call_report(labeled, analysis)
    return {
        "transcription": owner_tx,
        "analysis": analysis,
        "report": report,
        "labeled": labeled,
    }
