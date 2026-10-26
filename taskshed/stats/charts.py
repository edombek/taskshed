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

HEAT_GREENS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
HEAT_MISSED = "#d73027"
HEAT_PENDING = "#4a90d9"


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


def _heatmap_window(days: dict[date, dict], end: date | None,
                    min_weeks: int, max_weeks: int) -> tuple[date, int]:
    """(first Monday, week count) — always at least `min_weeks` weeks back
    from the newest day (or today), so sparse periods still show a month."""
    from datetime import timedelta

    lo, hi = min(days), max(days)
    anchor = max(hi, end or date.today())
    first_monday = lo - timedelta(days=lo.weekday())
    last_monday = anchor - timedelta(days=anchor.weekday())
    if last_monday < first_monday:
        last_monday = first_monday
    n_weeks = (last_monday - first_monday).days // 7 + 1
    if n_weeks < min_weeks:
        first_monday = last_monday - timedelta(weeks=min_weeks - 1)
        n_weeks = min_weeks
    if n_weeks > max_weeks:  # keep the most recent window, GitHub-like
        first_monday = last_monday - timedelta(weeks=max_weeks - 1)
        n_weeks = max_weeks
    return first_monday, n_weeks


def github_heatmap(days: dict[date, dict], lang: str, out_path: str,
                   max_weeks: int = 53, min_weeks: int = 5,
                   end: date | None = None) -> str:
    """GitHub-contribution-style grid: week columns, Mon-Sun rows.

    Cell base color = number of completions (green scale, GitHub palette);
    every day of the window gets a cell, empty ones stay gray. Inside a
    cell a small red square marks missed occurrences (top-right) and a
    blue square marks still-planned ones (bottom-right).
    """
    from datetime import timedelta

    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    if not days:
        return ""
    first_monday, n_weeks = _heatmap_window(days, end, min_weeks, max_weeks)

    fig, ax = plt.subplots(figsize=(0.26 * n_weeks + 2.2, 2.8))
    d = first_monday
    for wi in range(n_weeks):
        for row in range(7):
            agg = days.get(d, {})
            done = int(agg.get("done", 0))
            ax.add_patch(mpatches.Rectangle((wi, row), 0.86, 0.86,
                                            facecolor=HEAT_GREENS[min(done, 4)],
                                            edgecolor="white", linewidth=0.4))
            if int(agg.get("missed", 0)):
                ax.add_patch(mpatches.Rectangle((wi + 0.5, row), 0.36, 0.36,
                                                facecolor=HEAT_MISSED,
                                                edgecolor="none"))
            if int(agg.get("pending", 0)):
                ax.add_patch(mpatches.Rectangle((wi + 0.5, row + 0.5), 0.36,
                                                0.36, facecolor=HEAT_PENDING,
                                                edgecolor="none"))
            d += timedelta(days=1)

    # month labels above the column where a month first appears
    seen: set[tuple[int, int]] = set()
    for wi in range(n_weeks):
        monday = first_monday + timedelta(weeks=wi)
        key = (monday.year, monday.month)
        if key not in seen:
            seen.add(key)
            ax.text(wi, -0.9, monday.strftime("%b"), fontsize=7,
                    ha="left", va="center")
    for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        ax.text(-0.4, row, name, fontsize=7, ha="right", va="center")

    ax.set_xlim(-2.2, n_weeks)
    ax.set_ylim(6.6, -1.6)
    ax.axis("off")

    handles = [
        Line2D([0], [0], marker="s", linestyle="none", markersize=7,
               markerfacecolor=HEAT_GREENS[4], markeredgecolor="none",
               label=_legend(lang)[0]),
        Line2D([0], [0], marker="s", linestyle="none", markersize=7,
               markerfacecolor=HEAT_MISSED, markeredgecolor="none",
               label=_legend(lang)[1]),
        Line2D([0], [0], marker="s", linestyle="none", markersize=7,
               markerfacecolor=HEAT_PENDING, markeredgecolor="none",
               label=_legend(lang)[2]),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=7, ncols=3,
              frameon=False, bbox_to_anchor=(1.0, -0.12))
    ax.set_title(t("chart_heat", lang), fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
