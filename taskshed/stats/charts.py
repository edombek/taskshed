"""Matplotlib PNG charts. Agg backend — safe for headless servers/CI."""

from __future__ import annotations

from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from taskshed.i18n import t  # noqa: E402

COLOR_DONE = "#2e8b57"
COLOR_MISSED = "#c0392b"
COLOR_PENDING = "#95a5a6"


def _legend(lang: str):
    return (t("w_done", lang), t("w_missed", lang), t("w_pending", lang))


def daily_bar(days: dict[date, dict], lang: str, out_path: str) -> str:
    labels = sorted(days)
    done = [days[d].get("done", 0) for d in labels]
    missed = [days[d].get("missed", 0) for d in labels]
    pending = [days[d].get("pending", 0) for d in labels]
    fig, ax = plt.subplots(figsize=(9, 4))
    x = range(len(labels))
    ax.bar(x, done, color=COLOR_DONE, label=_legend(lang)[0])
    ax.bar(x, missed, bottom=done, color=COLOR_MISSED, label=_legend(lang)[1])
    ax.bar(x, pending,
           bottom=[d + m for d, m in zip(done, missed, strict=False)],
           color=COLOR_PENDING, label=_legend(lang)[2])
    ax.set_xticks(list(x), [d.strftime("%d.%m") for d in labels], fontsize=8)
    ax.set_title(t("chart_daily", lang))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def top_tasks(task_rows: list[dict], lang: str, out_path: str, limit: int = 10) -> str:
    rows = task_rows[:limit]
    labels = [r["title"][:28] for r in rows][::-1]
    percents = [r["percent"] for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(8, max(2.5, 0.4 * len(rows) + 1)))
    ax.barh(labels, percents, color=COLOR_DONE)
    ax.set_xlim(0, 100)
    ax.set_title(t("chart_top", lang))
    for i, p in enumerate(percents):
        ax.text(p + 1, i, f"{p}%", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def trend_line(points: list[tuple[date, int, int]], lang: str, out_path: str) -> str:
    if not points:
        return ""
    xs = [p[0] for p in points]
    pcts = [round(100 * d / tot) if tot else 0 for _, d, tot in points]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(xs, pcts, marker="o", color=COLOR_DONE)
    ax.set_ylim(0, 105)
    ax.set_title(t("chart_trend", lang))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def calendar_heatmap(month_percents: dict[date, float], year: int, month: int,
                     lang: str, out_path: str) -> str:
    """Simple monotone heatmap: weekdays × weeks grid for one month."""
    import datetime as dt

    import numpy as np

    days_in_month = list(month_percents)
    if not days_in_month:
        return ""
    weeks = sorted({d - dt.timedelta(days=d.weekday()) for d in days_in_month})
    mat = np.full((7, len(weeks)), np.nan)
    for d, pct in month_percents.items():
        wi = weeks.index(d - dt.timedelta(days=d.weekday()))
        mat[d.weekday(), wi] = pct
    fig, ax = plt.subplots(figsize=(max(4, len(weeks) * 0.9), 3.2))
    ax.imshow(mat, cmap="Greens", vmin=0, vmax=100, origin="lower")
    weekday_names = [dt.date(2024, 1, 1 + w).strftime("%a") for w in range(7)]
    ax.set_yticks(range(7), weekday_names, fontsize=8)
    ax.set_xticks(range(len(weeks)), [w.strftime("%d.%m") for w in weeks], fontsize=8)
    ax.set_title(t("chart_heat", lang) + f" {month:02d}.{year}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
