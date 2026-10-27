"""Statistics screens: text reports, CSV/XLSX files, activity maps.

One report per place: a text block, a file, or one chart each; the hour
map of a sliding week pages forward and back by whole weeks.
"""

import os
import re
import shutil
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.bot.consts import PERIODS
from taskshed.bot.context import Ctx
from taskshed.core import stats as sagg
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows
from taskshed.stats import charts, export


class ReportsMixin:

    async def _cmd_stats(self, ctx: Ctx, arg: str) -> None:
        period, fmt = None, None
        for token in arg.lower().split():
            if token in PERIODS:
                period = token
            elif token in ("csv", "xlsx", "график", "chart"):
                fmt = token
        if period is None:
            # ask the period first: buttons, not required arguments
            ctx.suppress_menu = True
            await ctx.reply(t("stats_ask_period", ctx.lang), [
                [(t("btn_per_week", ctx.lang), "per:week"),
                 (t("btn_per_month", ctx.lang), "per:month")],
                [(t("btn_per_year", ctx.lang), "per:year"),
                 (t("btn_per_all", ctx.lang), "per:all")],
                [(t("btn_back", ctx.lang), "menu:back")],
            ])
            return
        await self._stats_show(ctx, period, fmt)

    async def _stats_show(self, ctx: Ctx, period: str, fmt: str | None) -> None:
        name, days = PERIODS[period]
        if fmt == "hours":
            # the hour map is a screen of its own: it holds one week and pages
            # between weeks, so it does not depend on the period window below
            await self._hours_week(ctx, 0)
            return
        now = datetime.now(UTC)
        start = (now - timedelta(days=days)) if days else datetime(2000, 1, 1, tzinfo=UTC)
        # charts look stats_horizon_days into the future, so planned tasks are
        # visible; text/CSV reports keep describing what already happened
        chart = fmt in ("chart", "график")
        # forward: exactly the user's horizon setting; back: the period, but
        # the map only ever reaches as far as real occurrences exist, and a
        # schedule is never planned before the date written inside it
        ahead = max(1, ctx.settings.stats_horizon_days) if chart else 1
        if chart:
            # whole weeks: a week the horizon touches is drawn to its Sunday,
            # otherwise the planned squares at its right edge are cut off even
            # though the cells themselves are drawn
            today_local = now.astimezone(ctx.tz).date()
            horizon = today_local + timedelta(days=ahead)
            horizon += timedelta(days=6 - horizon.weekday())
            end = datetime.combine(horizon, time(23, 59), tzinfo=ctx.tz)
            await self.ensure_materialized(ctx, (horizon - today_local).days,
                                           days or 365)
        else:
            end = now + timedelta(days=ahead)
        occs = await self.store.occurrences_in_range(ctx.user.id, start, end)
        # planned (future) occurrences are kept on purpose: they show as
        # pending/«в плане» in reports and as blue insets on the heatmap
        # every report is per place: one chart, one file, one text block each
        if fmt in ("csv", "xlsx", "chart", "график"):
            if not occs:
                await ctx.reply(t("stats_no_data", ctx.lang))
                return
            for place, group in await self._by_place(ctx.user.id, occs):
                await self._stats_file(ctx, group, name, fmt, start, end, place)
            # the files/charts end the answer: the menu follow-up must follow
            ctx.replied = True
            return
        # text screen + format buttons: the buttons stay even when this period
        # is empty — the plan may well sit one week further out
        ctx.suppress_menu = True
        body = (t("stats_no_data", ctx.lang) if not occs else
                self._stats_text(ctx, await self._by_place(ctx.user.id, occs), name))
        rows = [[(t("btn_fmt_chart", ctx.lang), f"fmt:chart:{name}")]]
        if name == "week":
            # short periods also get the hour-level rendering: this week, and
            # a button straight into next week's plan
            rows.append([(t("btn_fmt_hours", ctx.lang), f"fmt:hours:{name}"),
                         (t("btn_hours_next", ctx.lang), "hrs:7")])
        rows += [
            [(t("btn_fmt_csv", ctx.lang), f"fmt:csv:{name}"),
             (t("btn_fmt_xlsx", ctx.lang), f"fmt:xlsx:{name}")],
            [(t("btn_back", ctx.lang), "menu:back")],
        ]
        await ctx.reply(body, rows)

    async def _by_place(self, user_id: int,
                        occs: list) -> list[tuple[str, list]]:
        """Split occurrences into one group per place, in name order."""
        names = {p.id: p.name for p in await self.store.list_places(user_id)}
        groups: dict[int, list] = {}
        for occ, task in occs:
            groups.setdefault(task.place_id, []).append((occ, task))
        return [(names.get(pid, "?"), groups[pid])
                for pid in sorted(groups, key=lambda pid: names.get(pid, "").lower())]


    def _stats_text(self, ctx: Ctx, groups: list[tuple[str, list]],
                    period: str) -> str:
        """One block per place: the same numbers, split by location."""
        lines = [t("stats_header", ctx.lang, period=t(f"period_{period}", ctx.lang))]
        for place, past in groups:
            lines.append(t("stats_place_line", ctx.lang, place=place))
            agg: dict[int, dict] = {}
            for occ, task in past:
                a = agg.setdefault(task.id, {"title": task.title, "done": 0,
                                             "missed": 0, "skipped": 0, "total": 0})
                a["total"] += 1
                if occ.status == "done":
                    a["done"] += 1
                elif occ.status == "missed":
                    a["missed"] += 1
                elif occ.status == "skipped":
                    a["skipped"] += 1
            for a in agg.values():
                base = a["total"] - a["skipped"]
                pct = round(100 * a["done"] / base) if base else 0
                lines.append(t("stats_row", ctx.lang, title=a["title"], done=a["done"],
                               missed=a["missed"], skipped=a["skipped"],
                               total=a["total"], percent=pct))
        return "\n".join(lines)

    def _hour_window(self, period: str, grid: dict, start: datetime,
                     end: datetime, tzinfo: ZoneInfo) -> tuple[date, date]:
        """Day rows of the hour map.

        A week is always seven rows, but sliding: three days back, today, three
        days ahead — the week the user is living in, not the calendar one.
        Longer periods reach back only as far as their data goes and never grow
        past 24 rows, because beyond that it stops being an hour-level picture.
        """
        if period == "week":
            middle = datetime.now(tzinfo).date()
            return middle - timedelta(days=3), middle + timedelta(days=3)
        first, last = start.astimezone(tzinfo).date(), end.astimezone(tzinfo).date()
        if grid:
            oldest = min(d for d, _ in grid)
            if oldest > first:
                first = oldest
        if (last - first).days > 24:
            first = last - timedelta(days=24)
        return first, last

    def _hours_rows(self, ctx: Ctx) -> ButtonRows:
        return [[(t("btn_hours_prev", ctx.lang), "hrs:-7"),
                 (t("btn_hours_next", ctx.lang), "hrs:7")]] + self._back_only(ctx)

    async def _hours_nav(self, ctx: Ctx, first: date, last: date) -> None:
        """Say which week the hour map drew, and offer the neighbouring ones."""
        ctx.suppress_menu = True
        await ctx.reply(t("stats_hours_nav", ctx.lang,
                          span=f"{first:%d.%m} — {last:%d.%m}"),
                        self._hours_rows(ctx))

    async def _hours_week(self, ctx: Ctx, shift: int) -> None:
        """Hour maps of a week moved by whole weeks: the week ahead, or before."""
        tzinfo = ZoneInfo(ctx.settings.tz)
        today = datetime.now(tzinfo).date()
        first = today - timedelta(days=3 - shift)
        last = first + timedelta(days=6)
        # a week further out has to be materialized before it can be drawn:
        # its plan may lie past the horizon setting
        await self.ensure_materialized(ctx, (last - today).days,
                                       max(0, (today - first).days))
        start = datetime.combine(first, time.min, tzinfo=tzinfo)
        end = datetime.combine(last, time(23, 59), tzinfo=tzinfo)
        occs = await self.store.occurrences_in_range(ctx.user.id, start, end)
        ctx.suppress_menu = True
        if not occs:
            await ctx.reply(
                t("stats_no_data", ctx.lang) + "\n"
                + t("stats_week_range", ctx.lang,
                    span=f"{first:%d.%m} — {last:%d.%m}"),
                self._hours_rows(ctx))
            return
        for place, group in await self._by_place(ctx.user.id, occs):
            await self._stats_file(ctx, group, "week", "hours", start, end,
                                   place, (first, last))
        await self._hours_nav(ctx, first, last)

    async def _stats_file(self, ctx: Ctx, past, period: str, fmt: str,
                          start: datetime, end: datetime, place: str,
                          window: tuple[date, date] | None = None
                          ) -> tuple[date, date] | None:
        """One report of one kind for one place.

        Every place gets its own copy: its own chart, its own spreadsheet, its
        own file name — nothing of «Дом» is mixed into «Работа». Returns the day
        rows the hour map drew, so the caller can offer the neighbouring week.
        """
        slug = re.sub(r"[^\w]+", "-", place, flags=re.UNICODE).strip("-") or "place"
        where = "\n" + t("stats_place_line", ctx.lang, place=place)
        drew: tuple[date, date] | None = None
        tmp = tempfile.mkdtemp(prefix="taskshed_stats_")
        try:
            task_rows = sagg.per_task(past)
            place_names = {p.id: p.name for p in await self.store.list_places(ctx.user.id)}
            place_rows = sagg.per_place(past, place_names)
            days = sagg.per_day(past, ctx.settings.tz)
            if fmt == "csv":
                path = os.path.join(tmp, f"stats-{slug}.csv")
                export.write_csv(task_rows, path)
                await ctx.messenger.send_document(
                    ctx.account, path, f"stats-{slug}.csv",
                    t("stats_sent_doc", ctx.lang) + where)
            elif fmt == "xlsx":
                path = os.path.join(tmp, f"stats-{slug}.xlsx")
                export.write_xlsx(task_rows, place_rows, path)
                await ctx.messenger.send_document(
                    ctx.account, path, f"stats-{slug}.xlsx",
                    t("stats_sent_doc", ctx.lang) + where)
            elif fmt == "hours":
                # day rows × hour columns: what a short period really looked like
                grid = sagg.activity_by_hour(past, ctx.settings.tz)
                tzinfo = ZoneInfo(ctx.settings.tz)
                first, last = window or self._hour_window(
                    period, grid, start, end, tzinfo)
                path = os.path.join(tmp, f"hours-{slug}.png")
                if not charts.hourly_heatmap(
                        grid, ctx.lang, path, (first, last),
                        datetime.now(tzinfo).date(), datetime.now(tzinfo)):
                    await ctx.reply(t("stats_no_data", ctx.lang))
                    return None
                await ctx.messenger.send_image(
                    ctx.account, path,
                    t("stats_hours_caption", ctx.lang)
                    + "\n" + t("stats_week_range", ctx.lang,
                               span=f"{first:%d.%m} — {last:%d.%m}") + where)
                drew = (first, last)
            else:  # chart: GitHub-style activity map (daily bars retired)
                heat = os.path.join(tmp, f"activity-{slug}.png")
                tzinfo = ZoneInfo(ctx.settings.tz)
                # the window reaches into the future, so the days ahead are
                # drawn even before anything is planned in them
                if not charts.github_heatmap(
                        days, ctx.lang, heat,
                        end=end.astimezone(tzinfo).date(),
                        today=datetime.now(tzinfo).date()):
                    await ctx.reply(t("stats_no_data", ctx.lang))
                    return
                await ctx.messenger.send_image(
                    ctx.account, heat, t("stats_heat_caption", ctx.lang) + where)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return drew
