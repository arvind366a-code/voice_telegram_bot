"""Standalone pipeline test — no Telegram, no database.

Run with: uv run test_local.py
Downloads its own speech sample, runs the full pipeline, prints the report, and
dumps the raw analysis to test_samples/analysis_output.json.
"""

from __future__ import annotations

import json
import os
import urllib.request

from pipeline import analyze_audio_file

SAMPLE_URL = "https://www.voiptroubleshooter.com/open_speech/american/OSR_us_000_0010_8k.wav"
SAMPLE_DIR = "test_samples"
SAMPLE_PATH = os.path.join(SAMPLE_DIR, "sample_speech.wav")
OUTPUT_PATH = os.path.join(SAMPLE_DIR, "analysis_output.json")


def _download_sample() -> None:
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    if os.path.exists(SAMPLE_PATH):
        print(f"Sample already present: {SAMPLE_PATH}")
        return
    print(f"Downloading sample speech to {SAMPLE_PATH} ...")
    # The host's mod_security rejects requests lacking a browser-like User-Agent
    # and Referer (HTTP 406), so send both.
    req = urllib.request.Request(
        SAMPLE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "*/*",
            "Referer": "https://www.voiptroubleshooter.com/open_speech/american.html",
        },
    )
    with urllib.request.urlopen(req) as resp, open(SAMPLE_PATH, "wb") as out:
        out.write(resp.read())
    print("Download complete.")


def main() -> None:
    _download_sample()

    print("\nRunning pipeline...\n")
    result = analyze_audio_file(SAMPLE_PATH)

    print(result["report"])

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"transcription": result["transcription"], "analysis": result["analysis"]},
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"\n✅ Local test complete. Check {OUTPUT_PATH} for raw data.\n"
        "   Now send a voice note in Telegram to test the bot."
    )


if __name__ == "__main__":
    main()
