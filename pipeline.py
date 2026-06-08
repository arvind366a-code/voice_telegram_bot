"""Core analysis pipeline: transcribe -> analyze -> report.

Purely functional and Telegram-free. Both bot.py and test_local.py call
``analyze_audio_file`` so there is a single source of truth.
"""

from __future__ import annotations

from analyze import analyze
from report import format_report
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
