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

HEAT_BG = "#0d1117"      # GitHub dark canvas
HEAT_EMPTY = "#161b22"   # day without completions
HEAT_TEXT = "#c9d1d9"
# dark-theme green scale (GitHub contributions): index = completions 0..4+
HEAT_GREENS = [HEAT_EMPTY, "#0e4429", "#006d32", "#26a641", "#39d353"]
HEAT_MISSED = "#f85149"
HEAT_PENDING = "#58a6ff"
HEAT_SKIPPED = "#e3b341"


def _legend(lang: str):
    return (t("w_done", lang), t("w_missed", lang), t("w_pending", lang),
            t("w_skipped", lang))


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


def hourly_heatmap(grid: dict[tuple[date, int], int], lang: str, out_path: str) -> str:
    """Day rows × 24 hour columns — the short-period view of the same data.

    Same dark palette as the daily map: the base square is the number of
    completions finished inside that hour, empty hours stay dark gray.
    """
    from matplotlib.patches import Rectangle

    if not grid:
        return ""
    days = sorted({d for d, _ in grid})
    n_days = len(days)

    fig, ax = plt.subplots(figsize=(0.36 * 24 + 3.0, 0.46 * n_days + 1.3))
    fig.patch.set_facecolor(HEAT_BG)
    ax.set_facecolor(HEAT_BG)
    for row, d in enumerate(days):
        for hour in range(24):
            done = int(grid.get((d, hour), 0))
            ax.add_patch(Rectangle((hour, row), 0.86, 0.86,
                                   facecolor=HEAT_GREENS[min(done, 4)],
                                   edgecolor=HEAT_BG, linewidth=0.4))

    # day labels on the left, hour labels under the grid every third hour
    for row, d in enumerate(days):
        ax.text(-0.35, row + 0.43, d.strftime("%d.%m"), fontsize=6.5, ha="right",
                va="center", color=HEAT_TEXT)
    for hour in range(0, 24, 3):
        ax.text(hour + 0.43, n_days + 0.3, f"{hour:02d}", fontsize=6.5,
                ha="center", va="center", color=HEAT_TEXT)

    ax.set_xlim(-2.2, 24)
    ax.set_ylim(n_days + 0.9, -0.6)  # inverted: the first day on top
    ax.axis("off")
    fig.savefig(out_path, dpi=110, facecolor=HEAT_BG, bbox_inches="tight")
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

    Cell base color = number of completions (green scale, GitHub dark
    palette on a dark canvas); every day of the window gets a cell, empty
    ones stay dark gray. Inside a cell a small red square marks missed
    occurrences (top-right), a blue one still-planned ones (bottom-right)
    and an orange one skipped ones (bottom-left).
    """
    from datetime import timedelta

    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    if not days:
        return ""
    first_monday, n_weeks = _heatmap_window(days, end, min_weeks, max_weeks)

    fig, ax = plt.subplots(figsize=(0.26 * n_weeks + 2.6, 2.6))
    fig.patch.set_facecolor(HEAT_BG)
    ax.set_facecolor(HEAT_BG)
    d = first_monday
    for wi in range(n_weeks):
        for row in range(7):
            agg = days.get(d, {})
            done = int(agg.get("done", 0))
            ax.add_patch(mpatches.Rectangle((wi, row), 0.86, 0.86,
                                            facecolor=HEAT_GREENS[min(done, 4)],
                                            edgecolor=HEAT_BG, linewidth=0.4))
            if int(agg.get("missed", 0)):
                ax.add_patch(mpatches.Rectangle((wi + 0.5, row), 0.36, 0.36,
                                                facecolor=HEAT_MISSED,
                                                edgecolor="none"))
            if int(agg.get("pending", 0)):
                ax.add_patch(mpatches.Rectangle((wi + 0.5, row + 0.5), 0.36,
                                                0.36, facecolor=HEAT_PENDING,
                                                edgecolor="none"))
            if int(agg.get("skipped", 0)):
                ax.add_patch(mpatches.Rectangle((wi, row + 0.5), 0.36, 0.36,
                                                facecolor=HEAT_SKIPPED,
                                                edgecolor="none"))
            d += timedelta(days=1)

    # month labels above the column where a month first appears
    seen: set[tuple[int, int]] = set()
    for wi in range(n_weeks):
        monday = first_monday + timedelta(weeks=wi)
        key = (monday.year, monday.month)
        if key not in seen:
            seen.add(key)
            ax.text(wi + 0.1, -0.75, t(f"hm_m{monday.month}", lang), fontsize=7,
                    ha="left", va="center", color=HEAT_TEXT)
    # weekday labels centered on their rows (cells span row..row+0.86)
    for row, key in ((0, "hm_mon"), (2, "hm_wed"), (4, "hm_fri")):
        ax.text(-0.25, row + 0.43, t(key, lang), fontsize=7, ha="right",
                va="center", color=HEAT_TEXT)

    ax.set_xlim(-2.4, n_weeks)
    # bottom must clear the Sunday row: cells span row..row+0.86, Sunday is
    # row 6 → 6.86, anything less clips those squares
    ax.set_ylim(6.9, -1.3)
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
        Line2D([0], [0], marker="s", linestyle="none", markersize=7,
               markerfacecolor=HEAT_SKIPPED, markeredgecolor="none",
               label=_legend(lang)[3]),
    ]
    leg = ax.legend(handles=handles, loc="upper center", fontsize=7, ncols=4,
                    frameon=False, bbox_to_anchor=(0.5, -0.02))
    for txt in leg.get_texts():
        txt.set_color(HEAT_TEXT)
    fig.savefig(out_path, dpi=110, facecolor=HEAT_BG, bbox_inches="tight")
    plt.close(fig)
    return out_path
