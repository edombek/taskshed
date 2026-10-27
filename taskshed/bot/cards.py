"""The task card: schedule lines, next occurrence, edit buttons."""

from datetime import UTC, date, datetime, timedelta

from taskshed.bot.consts import WD_KEYS
from taskshed.bot.context import Ctx
from taskshed.core.stats import occurrence_moment
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows


class TaskCardsMixin:

    async def _task_lines(self, ctx: Ctx, task) -> list[str]:
        """Where/when lines of a task: the when, the reminder, the next date."""
        tz, fmt = ctx.tz, "%d.%m %H:%M"
        if task.schedule_type == "once" and task.deadline:
            due = task.deadline.astimezone(tz)
            if task.start_at is not None:
                when = t("save_when_range", ctx.lang,
                         start=task.start_at.astimezone(tz).strftime(fmt),
                         due=due.strftime(fmt))
            else:
                when = t("save_when_once", ctx.lang, due=due.strftime(fmt))
        elif task.schedule_type == "once":
            # no ready-by: a plain reminder at one moment, not a window
            moment = task.start_at or task.deadline
            if moment is not None:
                when = t("save_when_moment", ctx.lang,
                         when=moment.astimezone(tz).strftime(fmt))
            else:
                when = t("save_when_soon", ctx.lang)
        elif task.schedule_type == "every_n_days":
            anchor = str(task.schedule_data.get("anchor", ""))
            try:
                anchor = date.fromisoformat(anchor).strftime("%d.%m")
            except ValueError:
                pass
            when = t("save_when_every", ctx.lang, n=task.schedule_data.get("n", 1),
                     anchor=anchor)
        elif task.schedule_type == "weekdays":
            when = t("save_when_weekdays", ctx.lang,
                     days=", ".join(t(WD_KEYS[i], ctx.lang)
                                    for i in sorted(task.schedule_data.get("weekdays", []))))
        else:
            when = t("save_when_monthly", ctx.lang, day=task.schedule_data.get("day", 1))
        lines = [when, t("save_reminder", ctx.lang,
                         time=task.reminder_time.strftime("%H:%M"))]
        # the first date it will actually appear on — the useful part of a rule
        horizon = max(self.cfg.occurrence_horizon_days,
                      ctx.settings.stats_horizon_days)
        soon = await self.store.occurrences_in_range(
            ctx.user.id, datetime.now(UTC),
            datetime.now(UTC) + timedelta(days=horizon))
        moments = [occurrence_moment(o).astimezone(tz)
                   for o, tk in soon if tk.id == task.id]
        if moments:
            lines.append(t("save_next", ctx.lang,
                           when=min(moments).strftime(fmt)))
        return lines

    async def _next_moments(self, ctx: Ctx, days: int) -> dict[int, datetime]:
        """task_id → nearest open occurrence moment (UTC) in the next `days`."""
        now = datetime.now(UTC)
        occs = await self.store.occurrences_in_range(
            ctx.user.id, now, now + timedelta(days=days))
        out: dict[int, datetime] = {}
        for occ, task in occs:
            if occ.status not in ("pending", "in_progress"):
                continue
            moment = occurrence_moment(occ)
            if task.id not in out or moment < out[task.id]:
                out[task.id] = moment
        return out


    def _task_open_rows(self, ctx: Ctx, task) -> ButtonRows:
        """Edit buttons of one task: every field, delete, back to the list."""
        tab = self._edit_tab.get(ctx.user.id, "future")
        rows = [[(t("edit_f_title", ctx.lang), f"edf:{task.id}:title"),
                 (t("edit_f_place", ctx.lang), f"edf:{task.id}:place")],
                [(t("edit_f_schedule", ctx.lang), f"edf:{task.id}:schedule")]]
        if task.schedule_type == "weekdays":
            rows.append([(t("edit_f_weekdays", ctx.lang), f"edf:{task.id}:weekdays")])
        if task.schedule_type == "once":
            rows.append([(t("edit_f_when", ctx.lang), f"edf:{task.id}:when")])
        rows.append([(t("edit_f_reminder", ctx.lang), f"edf:{task.id}:reminder")])
        rows.append([(t("edit_delete", ctx.lang), f"eddel:{tab}:{task.id}")])
        rows.append([(t("btn_back", ctx.lang), f"etab:{tab}")])
        return rows
