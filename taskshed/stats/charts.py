"""Matplotlib PNG charts. Agg backend — safe for headless servers/CI."""

from __future__ import annotations

from datetime import date, datetime

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

# print/phone resolution: the maps get zoomed on screen, so they are rendered
# far above the old 110 dpi, but kept inside Telegram's photo pixel budget
DPI = 300
PIXEL_BUDGET = 2_400_000
# one grid unit (a cell is 0.86 of it) in inches: the picture therefore grows
# with the number of squares instead of being blown up to a fixed frame
CELL_IN = 0.20
# the legend needs horizontal room; narrow maps rely on the caption instead
LEGEND_MIN_W = 3.0


def _dpi_for(width_in: float, height_in: float) -> int:
    """Dpi that fills the picture with pixels without exceeding the budget."""
    import math

    return max(110, min(DPI, int(math.sqrt(PIXEL_BUDGET / (width_in * height_in)))))


def _grid_figsize(x_span: float, y_span: float) -> tuple[float, float]:
    """Figure size for a grid whose axis limits span x_span × y_span units.

    Both axes get the same inches-per-unit, so cells are square and the image
    grows with the number of squares instead of being stretched to a fixed frame.
    """
    return (CELL_IN * x_span, CELL_IN * y_span)


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
    fig.savefig(out_path, dpi=_dpi_for(*fig.get_size_inches()))
    plt.close(fig)
    return out_path


def hourly_heatmap(grid: dict[tuple[date, int], dict], lang: str, out_path: str,
                   day_range: tuple[date, date] | None = None,
                   today: date | None = None,
                   now: datetime | None = None) -> str:
    """Day rows × 24 hour columns — the short-period view of the same data.

    Same visual language as the daily map: the base square is the number of
    tasks finished in that hour, and inside it a red inset marks missed ones,
    an orange one skipped, a blue one still planned. `day_range` draws every
    day of the period, including the empty days ahead. `now` outlines the hour
    the user is standing in and ticks the exact minute — the past/future line
    of an hour-level picture.
    """
    from datetime import timedelta

    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    if day_range:
        first, last = day_range
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    else:
        days = sorted({d for d, _ in grid})
    if not days:
        return ""
    n_days = len(days)

    # one unit per row/column on both axes (labels live inside the limits), so
    # cells are square and the picture grows with the number of squares
    x_lo, x_hi = -2.2, 24.0
    y_hi, y_lo = n_days + 0.9, -0.6
    figsize = _grid_figsize(x_hi - x_lo, y_hi - y_lo)
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(HEAT_BG)
    ax.set_facecolor(HEAT_BG)
    for row, d in enumerate(days):
        for hour in range(24):
            agg = grid.get((d, hour), {})
            done = int(agg.get("done", 0))
            ax.add_patch(Rectangle((hour, row), 0.86, 0.86,
                                   facecolor=HEAT_GREENS[min(done, 4)],
                                   edgecolor=HEAT_BG, linewidth=0.4))
            if int(agg.get("missed", 0)):
                ax.add_patch(Rectangle((hour + 0.5, row), 0.36, 0.36,
                                       facecolor=HEAT_MISSED, edgecolor="none"))
            if int(agg.get("pending", 0)):
                ax.add_patch(Rectangle((hour + 0.5, row + 0.5), 0.36, 0.36,
                                       facecolor=HEAT_PENDING, edgecolor="none"))
            if int(agg.get("skipped", 0)):
                ax.add_patch(Rectangle((hour, row + 0.5), 0.36, 0.36,
                                       facecolor=HEAT_SKIPPED, edgecolor="none"))

    # the hour the user is standing in: outlined, with a tick at the exact
    # minute — everything left of it already happened
    row_of = {d: i for i, d in enumerate(days)}
    if now is not None and now.date() in row_of:
        row, hour = row_of[now.date()], now.hour
        ax.add_patch(Rectangle((hour, row), 0.86, 0.86, facecolor="none",
                               edgecolor=HEAT_TEXT, linewidth=1.1))
        ax.plot([hour + now.minute / 60] * 2, [row - 0.12, row + 0.98],
                color=HEAT_TEXT, linewidth=0.8, zorder=5)

    # day labels on the left (today in green), hour labels every third hour
    for row, d in enumerate(days):
        ax.text(-0.35, row + 0.43, d.strftime("%d.%m"), fontsize=6.5, ha="right",
                va="center",
                color=HEAT_GREENS[3] if d == today else HEAT_TEXT)
    for hour in range(0, 24, 3):
        ax.text(hour + 0.43, n_days + 0.3, f"{hour:02d}", fontsize=6.5,
                ha="center", va="center", color=HEAT_TEXT)

    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_hi, y_lo)  # inverted: the first day on top
    ax.axis("off")

    handles = [
        Line2D([0], [0], marker="s", linestyle="none", markersize=6,
               markerfacecolor=HEAT_GREENS[4], markeredgecolor="none",
               label=_legend(lang)[0]),
        Line2D([0], [0], marker="s", linestyle="none", markersize=6,
               markerfacecolor=HEAT_MISSED, markeredgecolor="none",
               label=_legend(lang)[1]),
        Line2D([0], [0], marker="s", linestyle="none", markersize=6,
               markerfacecolor=HEAT_PENDING, markeredgecolor="none",
               label=_legend(lang)[2]),
        Line2D([0], [0], marker="s", linestyle="none", markersize=6,
               markerfacecolor=HEAT_SKIPPED, markeredgecolor="none",
               label=_legend(lang)[3]),
    ]
    if figsize[0] >= LEGEND_MIN_W:
        leg = ax.legend(handles=handles, loc="upper center", fontsize=7, ncols=4,
                        frameon=False, bbox_to_anchor=(0.5, -0.02))
        for txt in leg.get_texts():
            txt.set_color(HEAT_TEXT)
    fig.savefig(out_path, dpi=_dpi_for(*fig.get_size_inches()),
              facecolor=HEAT_BG, bbox_inches="tight")
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
    fig.savefig(out_path, dpi=_dpi_for(*fig.get_size_inches()))
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
    fig.savefig(out_path, dpi=_dpi_for(*fig.get_size_inches()))
    plt.close(fig)
    return out_path


def _heatmap_window(days: dict[date, dict], end: date | None,
                    min_weeks: int, max_weeks: int) -> tuple[date, int]:
    """(first Monday, week count) — the window starts at the week holding the
    oldest day there is data for and ends at the week holding `end` (the
    horizon), so the days ahead are drawn even while empty."""
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
                   max_weeks: int = 53, min_weeks: int = 1,
                   end: date | None = None, today: date | None = None) -> str:
    """GitHub-contribution-style grid: week columns, Mon-Sun rows.

    Cell base color = number of completions (green scale, GitHub dark
    palette on a dark canvas); every day of the window gets a cell, empty
    ones stay dark gray. Inside a cell a small red square marks missed
    occurrences (top-right), a blue one still-planned ones (bottom-right)
    and an orange one skipped ones (bottom-left). `end` extends the window
    up to that date, so the days ahead are drawn even when nothing is
    planned in them; `today` outlines the current day as the past/future
    boundary.
    """
    from datetime import timedelta

    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    if not days:
        return ""
    first_monday, n_weeks = _heatmap_window(days, end, min_weeks, max_weeks)

    # one unit per week column and per weekday row on both axes (the labels sit
    # inside the limits): cells are square, the picture grows with their count
    x_lo, x_hi = -2.4, float(n_weeks)
    y_hi, y_lo = 6.9, -1.3
    figsize = _grid_figsize(x_hi - x_lo, y_hi - y_lo)
    fig, ax = plt.subplots(figsize=figsize)
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

    # outline today: everything below/right of it is the plan for ahead
    if today and 0 <= (today - first_monday).days < n_weeks * 7:
        wi, row = divmod((today - first_monday).days, 7)
        ax.add_patch(mpatches.Rectangle((wi, row), 0.86, 0.86, facecolor="none",
                                        edgecolor=HEAT_TEXT, linewidth=1.1))

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

    ax.set_xlim(x_lo, x_hi)
    # bottom must clear the Sunday row: cells span row..row+0.86, Sunday is
    # row 6 → 6.86, anything less clips those squares
    ax.set_ylim(y_hi, y_lo)
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
    if figsize[0] >= LEGEND_MIN_W:
        leg = ax.legend(handles=handles, loc="upper center", fontsize=7, ncols=4,
                        frameon=False, bbox_to_anchor=(0.5, -0.02))
        for txt in leg.get_texts():
            txt.set_color(HEAT_TEXT)
    fig.savefig(out_path, dpi=_dpi_for(*fig.get_size_inches()),
                facecolor=HEAT_BG, bbox_inches="tight")
    plt.close(fig)
    return out_path
