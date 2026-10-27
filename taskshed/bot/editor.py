"""The task editor: tabbed pickers, per-field editing, delete."""

from datetime import UTC, datetime, timedelta

from taskshed.bot.context import Ctx
from taskshed.i18n import t


class EditorMixin:

    async def _cmd_delete(self, ctx: Ctx, arg: str) -> None:
        if not arg.isdigit():
            # bare /удалить: the editor lists tasks and deletes with a confirm
            await self._edit_picker(ctx, "all")
            return
        tasks = await self.store.list_tasks(ctx.user.id, active_only=False)
        idx = int(arg) - 1
        if 0 <= idx < len(tasks) and await self.store.delete_task(tasks[idx].id, ctx.user.id):
            await ctx.reply(t("task_deleted", ctx.lang))
        else:
            await ctx.reply(t("task_not_found", ctx.lang))


    EDIT_TABS = ("future", "recurring", "past", "all")
    # field → wizard step it is edited with
    EDIT_STEPS = {"title": "title", "place": "place", "schedule": "schedule",
                  "when": "when", "dtime": "dtime", "lead": "lead",
                  "weekdays": "days"}
    # fields that end right after the value (the rest keep the wizard chain)
    EDIT_SINGLE = ("title", "place", "lead", "weekdays", "dtime", "when")

    def _edit_wizard(self, task, tab: str) -> dict:
        """Wizard state pre-filled from an existing task (edit mode)."""
        d: dict = {"title": task.title, "place_id": task.place_id,
                   "stype": str(task.schedule_type),
                   "lead": task.notify_before_min, "dtime": task.deadline_time}
        if task.schedule_type == "once":
            if task.deadline:
                d["deadline"] = task.deadline
            if task.start_at:
                d["start"] = task.start_at
        elif task.schedule_type == "every_n_days":
            d["n"] = task.schedule_data.get("n", 1)
            d["anchor"] = task.schedule_data.get("anchor", "")
        elif task.schedule_type == "monthly_day":
            d["day"] = task.schedule_data.get("day", 1)
        return {"kind": "task", "step": "title", "data": d, "hist": [],
                "days": set(task.schedule_data.get("weekdays", [])),
                "edit": task.id, "tab": tab}

    async def _cmd_edit(self, ctx: Ctx, arg: str) -> None:
        tab = (arg or "").strip().lower()
        await self._edit_picker(ctx, tab if tab in self.EDIT_TABS else "future")

    async def _edit_picker(self, ctx: Ctx, tab: str, note: str = "") -> None:
        await self.ensure_materialized(ctx)
        self._edit_tab[ctx.user.id] = tab
        horizon = max(self.cfg.occurrence_horizon_days,
                      ctx.settings.stats_horizon_days)
        tasks = await self.store.list_tasks(ctx.user.id, active_only=False)
        upcoming = await self._next_moments(ctx, horizon)
        past_ids = await self._tasks_with_past(ctx.user.id)
        tz = ctx.tz
        picked = []
        for task in tasks:
            nxt = upcoming.get(task.id)
            if tab == "future" and (nxt is None or not task.is_active):
                continue
            if tab == "recurring" and (task.schedule_type == "once"
                                       or not task.is_active):
                continue
            if tab == "past" and task.id not in past_ids:
                continue
            picked.append((task, nxt))
        picked.sort(key=lambda p: (p[1] or datetime.max.replace(tzinfo=UTC),
                                   p[0].title.lower()))
        rows = []
        for task, nxt in picked[:20]:
            label = f"✏️ {task.title[:32]}"
            if nxt:
                label += f" · {nxt.astimezone(tz):%d.%m}"
            if task.schedule_type != "once":
                label += " 🔁"
            if not task.is_active:
                label += " ⏸"
            rows.append([(label, f"edt:{tab}:{task.id}")])
        tabs = [[(t(f"edit_tab_{x}", ctx.lang), f"etab:{x}")
                 for x in self.EDIT_TABS[:2]],
                [(t(f"edit_tab_{x}", ctx.lang), f"etab:{x}")
                 for x in self.EDIT_TABS[2:]]]
        text = t("edit_picker_head", ctx.lang, tab=t(f"edit_tab_{tab}", ctx.lang))
        if note:
            text = note + "\n\n" + text
        if not rows:
            text += "\n" + t("edit_none", ctx.lang)
        ctx.suppress_menu = True
        await ctx.reply(text, rows + tabs + [[(t("btn_back", ctx.lang), "menu:back")]])

    async def _tasks_with_past(self, user_id: int) -> set[int]:
        """Tasks that already have a finished or missed repeat behind them."""
        now = datetime.now(UTC)
        occs = await self.store.occurrences_in_range(
            user_id, now - timedelta(days=730), now)
        return {task.id for _, task in occs}

    async def _cb_edit_open(self, ctx: Ctx, tab: str, task_id: int) -> None:
        task = await self.store.get_task(ctx.user.id, task_id)
        if task is None:
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        self._edit_tab[ctx.user.id] = tab
        ctx.suppress_menu = True
        await ctx.reply("\n".join([f"✏️ {task.title}"] + await self._task_lines(ctx, task)),
                        self._task_open_rows(ctx, task))


    async def _cb_edit_field(self, ctx: Ctx, task_id: int, field: str) -> None:
        if field not in self.EDIT_STEPS:
            return
        task = await self.store.get_task(ctx.user.id, task_id)
        if task is None:
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        w = self._edit_wizard(task, self._edit_tab.get(ctx.user.id, "future"))
        w["step"] = self.EDIT_STEPS[field]
        if field in self.EDIT_SINGLE:
            w["only"] = field
        self.wizards[ctx.user.id] = w
        if w["step"] == "days":
            await self._wd_buttons(ctx, w)
        else:
            await self._ask_step(ctx, w)

    async def _cb_edit_delete(self, ctx: Ctx, tab: str, task_id: int) -> None:
        task = await self.store.get_task(ctx.user.id, task_id)
        if task is None:
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        ctx.suppress_menu = True
        await ctx.reply(t("edit_delete_ask", ctx.lang, title=task.title),
                        [[(t("edit_delete_yes", ctx.lang), f"edyes:{tab}:{task_id}")],
                         [(t("btn_back", ctx.lang), f"edt:{tab}:{task_id}")]])

    async def _cb_edit_delete_yes(self, ctx: Ctx, tab: str, task_id: int) -> None:
        task = await self.store.get_task(ctx.user.id, task_id)
        self.wizards.pop(ctx.user.id, None)
        if task is None or not await self.store.delete_task(task_id, ctx.user.id):
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        await self._edit_picker(ctx, tab,
                                note=t("task_deleted", ctx.lang))
