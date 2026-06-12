"""Format an analysis dict into a Telegram-friendly plain-text report."""

from __future__ import annotations

BARS = "▁▂▃▄▅▆▇█"
LINE = "──────────────────"


def format_report(transcription: dict, analysis: dict) -> str:
    text = (transcription.get("text") or "").strip()

    preview = text[:120] + ("…" if len(text) > 120 else "")
    if not preview:
        preview = "(no speech detected)"

    parts = [
        "🎙️ Speech Analysis",
        LINE,
        f"📝 {preview}",
        "",
        *_metrics_block(analysis),
    ]
    return "\n".join(parts)


def format_call_report(labeled_segments: list, analysis: dict) -> str:
    """Format a 2-speaker call: labeled transcript + owner-only metrics."""
    parts = [
        "📞 Call Analysis",
        LINE,
        "🧑 You vs 👩 Aardra — metrics below reflect your speech only.",
        "",
        "💬 Transcript",
        _labeled_transcript(labeled_segments),
        "",
        *_metrics_block(analysis),
    ]
    return "\n".join(parts)


def _metrics_block(analysis: dict) -> list:
    """Shared Core Metrics / Pace / Top Issues lines used by both reports."""
    wpm = analysis["wpm"]
    fillers = analysis["fillers"]
    pauses = analysis["pauses"]
    clarity = analysis["clarity"]
    pace = analysis["pace_consistency"]
    duration = analysis["duration"]

    return [
        "📊 Core Metrics",
        f"- WPM: {wpm['average']:.0f} {_wpm_emoji(wpm['flag'])} — pace: {pace['label']}",
        f"- Duration: {duration:.0f}s",
        f"- Filler words: {fillers['total']} ({fillers['percentage']:.1f}%)"
        + _top_fillers_suffix(fillers["breakdown"]),
        f"- Pauses: {pauses['short']}s / {pauses['medium']}m / {pauses['long']}l"
        f" — total pause time: {pauses['total_pause_time']:.1f}s",
        f"- Clarity: {clarity['score']:.0f}/100 {_clarity_emoji(clarity['score'])}",
        "",
        "⚡ Pace Breakdown (per 10s window)",
        _pace_chart(wpm["rolling"]),
        "",
        "💡 Top Issues",
        _top_issues(analysis),
    ]


def _labeled_transcript(labeled_segments: list) -> str:
    """Render diarized segments as turns, collapsing consecutive same-speaker runs."""
    if not labeled_segments:
        return "(no speech detected)"

    labels = {"owner": "🧑 You", "other": "👩 Aardra"}
    turns = []  # (speaker, [text, ...])
    for seg in labeled_segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg["speaker"]
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))

    lines = [f"{labels.get(sp, sp)}: {' '.join(chunks)}" for sp, chunks in turns]
    return "\n".join(lines) if lines else "(no speech detected)"


def _wpm_emoji(flag: str) -> str:
    return {"normal": "🟢", "fast": "🟡", "very_fast": "🔴"}.get(flag, "🟢")


def _clarity_emoji(score: float) -> str:
    if score >= 80:
        return "🟢"
    if score >= 55:
        return "🟡"
    return "🔴"


def _top_fillers_suffix(breakdown: dict) -> str:
    if not breakdown:
        return ""
    top = list(breakdown.items())[:3]
    rendered = ", ".join(f'"{word}" x{count}' for word, count in top)
    return f" → top fillers: {rendered}"


def _pace_chart(rolling: list) -> str:
    if not rolling:
        return "(no timing data)"

    peak = max((w["wpm"] for w in rolling), default=0.0)
    if peak <= 0:
        return "(no timing data)"

    cells = []
    for w in rolling:
        level = int(round((w["wpm"] / peak) * (len(BARS) - 1)))
        bar = BARS[level] * 4 if w["wpm"] > 0 else BARS[0]
        cells.append(f"{w['start']:.0f}–{w['end']:.0f}s {bar} {w['wpm']:.0f}wpm")
    return "  |  ".join(cells)


def _top_issues(analysis: dict) -> str:
    """Auto-pick the worst 2 metrics and give one actionable tip each."""
    wpm = analysis["wpm"]
    fillers = analysis["fillers"]
    clarity = analysis["clarity"]
    pace = analysis["pace_consistency"]

    candidates = []  # (severity, message)

    # Speed
    if wpm["flag"] != "normal":
        sev = 2 if wpm["flag"] == "very_fast" else 1
        candidates.append(
            (
                sev,
                f"Speed: You averaged {wpm['average']:.0f} WPM — aim for ~150. "
                "Try pausing after each key point.",
            )
        )

    # Fillers
    if fillers["total"] > 0:
        sev = 2 if fillers["percentage"] >= 4 else 1
        top = next(iter(fillers["breakdown"].items()), None)
        top_note = f' — "{top[0]}" appeared {top[1]} times.' if top else "."
        candidates.append(
            (
                sev,
                f"Fillers: {fillers['total']} filler words "
                f"({fillers['percentage']:.1f}%){top_note}",
            )
        )

    # Clarity
    if clarity["score"] < 80:
        sev = 2 if clarity["score"] < 55 else 1
        candidates.append(
            (
                sev,
                f"Clarity: scored {clarity['score']:.0f}/100 — slow down on mumbled "
                "sections and articulate word endings.",
            )
        )

    # Pace consistency
    if pace["label"] != "consistent":
        sev = 2 if pace["label"] == "erratic" else 1
        candidates.append(
            (
                sev,
                f"Pacing: {pace['label']} (std {pace['std_dev']:.0f}) — keep a steadier "
                "rhythm instead of rushing then slowing.",
            )
        )

    if not candidates:
        return "  - 🎉 Solid delivery — no major issues detected."

    candidates.sort(key=lambda c: c[0], reverse=True)
    return "\n".join(f"  - {msg}" for _, msg in candidates[:2])
