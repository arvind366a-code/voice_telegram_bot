"""Render a progress chart (PNG bytes) from the analysis history.

Headless: forces the Agg backend so it works inside a container with no display.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (must follow backend selection)


def render_progress_chart(history: list) -> bytes:
    """Render WPM / clarity / filler trends over time as a single PNG.

    Args:
        history: chronological list of {date, avg_wpm, filler_pct, clarity}.

    Returns:
        PNG image bytes.
    """
    x = list(range(len(history)))
    wpm = [r["avg_wpm"] for r in history]
    clarity = [r["clarity"] for r in history]
    filler = [r["filler_pct"] for r in history]
    labels = [r["date"][5:] for r in history]  # MM-DD

    fig, (ax_wpm, ax_clarity, ax_filler) = plt.subplots(
        3, 1, figsize=(8, 9), sharex=True
    )
    # Plain ASCII title — matplotlib's default font can't render emoji.
    fig.suptitle("Speech Progress", fontsize=15, fontweight="bold")

    _plot(ax_wpm, x, wpm, "WPM (pace)", "#2563eb")
    ax_wpm.axhspan(0, 160, color="#22c55e", alpha=0.08)   # normal zone
    ax_wpm.axhspan(160, 200, color="#eab308", alpha=0.08)  # fast zone

    _plot(ax_clarity, x, clarity, "Clarity (/100)", "#16a34a")
    ax_clarity.set_ylim(0, 100)

    _plot(ax_filler, x, filler, "Filler words (%)", "#dc2626")

    # X labels: thin them out so they stay readable for longer histories.
    step = max(1, len(x) // 10)
    ax_filler.set_xticks(x[::step])
    ax_filler.set_xticklabels(labels[::step], rotation=45, ha="right", fontsize=8)
    ax_filler.set_xlabel("Session date")

    fig.tight_layout(rect=(0, 0, 1, 0.97))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _plot(ax, x, y, title: str, color: str) -> None:
    ax.plot(x, y, marker="o", color=color, linewidth=2, markersize=4)
    ax.set_title(title, fontsize=11, loc="left")
    ax.grid(True, alpha=0.25)
    # Trend line (least-squares) when there are enough points.
    if len(x) >= 3:
        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        denom = sum((xi - mean_x) ** 2 for xi in x)
        if denom:
            slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / denom
            intercept = mean_y - slope * mean_x
            ax.plot(x, [slope * xi + intercept for xi in x], "--", color=color, alpha=0.4)
