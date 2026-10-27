"""The task wizard: questions, typed answers, confirmation cards, finish.

Four kinds of tasks — once, every N days, by weekdays, every M of the month —
and all four finish with the same two attributes: the **deadline** (one
moment for a one-off, a clock on each repeat day for the rest) and **how long
before it** the user wants to be warned. Every moment is asked and stored in
the user's own wall-clock, never in host time.
"""

from datetime import date, datetime, time, timedelta

from taskshed.bot.consts import TM_PRESETS, WD_KEYS, at, defang
from taskshed.bot.context import Ctx
from taskshed.bot.timeparse import (
    When,
    as_clock,
    as_day,
    day_ymd,
    hhmm,
    parse_lead,
    parse_when,
    read_clock,
)
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows

DEFAULT_LEAD_MIN = 60
DEFAULT_CLOCK = time(18, 0)
# clocks offered for the deadline of a repeating task
CLOCK_PRESETS = [[(f"⏎ {DEFAULT_CLOCK:%H:%M}", "tm:18:00")],
                 [("09:00", "tm:09:00"), ("12:00", "tm:12:00")],
                 [("15:00", "tm:15:00"), ("21:00", "tm:21:00")]]
# how long before the deadline to warn, as buttons (minutes in the callback)
LEAD_KEYS = [("btn_lead_5", 5), ("btn_lead_15", 15), ("btn_lead_30", 30),
             ("btn_lead_60", 60), ("btn_lead_120", 120), ("btn_lead_360", 360),
             ("btn_lead_1440", 1440), ("btn_lead_2880", 2880),
             ("btn_lead_10080", 10080)]


class TaskWizardMixin:

    async def _cmd_task(self, ctx: Ctx, _: str) -> None:
        self.wizards[ctx.user.id] = {"kind": "task", "step": "title", "data": {},
                                     "days": set()}
        await self._ask_step(ctx, self.wizards[ctx.user.id])

    def _goto(self, w: dict, step: str) -> None:
        """Advance the wizard, remembering where we came from (⬅️ Назад)."""
        w.setdefault("hist", []).append(w["step"])
        w["step"] = step

    def _back_row(self, ctx: Ctx, w: dict) -> ButtonRows:
        return [[(t("btn_back", ctx.lang), "wz:back")]] if w.get("hist") else []

    async def _ask_step(self, ctx: Ctx, w: dict) -> None:
        """Ask (or re-ask after Назад) the question for the current step."""
        step, back = w["step"], self._back_row(ctx, w)
        if step == "title":
            await ctx.reply(t("wizard_ask_title", ctx.lang),
                            (back or []) + [[(t("btn_wz_cancel", ctx.lang), "wz:cancel")]])
        elif step == "place":
            await ctx.place_buttons("wizard_ask_place", back)
        elif step == "schedule":
            await ctx.reply(t("wizard_ask_schedule", ctx.lang), [
                [(t("sched_once", ctx.lang), "sched:once"),
                 (t("sched_every_n", ctx.lang), "sched:every_n_days")],
                [(t("sched_weekdays", ctx.lang), "sched:weekdays"),
                 (t("sched_monthly", ctx.lang), "sched:monthly_day")]] + back)
        elif step == "when":
            await ctx.reply(t("wizard_ask_when", ctx.lang), [
                [(t("btn_today", ctx.lang), "dt:0"),
                 (t("btn_tomorrow", ctx.lang), "dt:1"),
                 (t("btn_in_2_days", ctx.lang), "dt:2")]] + back)
        elif step == "whentime":
            await ctx.reply(t("wizard_ask_time", ctx.lang), TM_PRESETS + back)
        elif step == "anchor":
            await ctx.reply(t("wizard_ask_anchor_date", ctx.lang), [
                [(t("btn_today", ctx.lang), "dt:0"),
                 (t("btn_tomorrow", ctx.lang), "dt:1")]] + back)
        elif step == "n":
            await ctx.reply(t("wizard_ask_n", ctx.lang), back or None)
        elif step == "monthday":
            await ctx.reply(t("wizard_ask_month_day", ctx.lang), back or None)
        elif step == "days":
            await self._wd_buttons(ctx, w)
        elif step == "dtime":
            await ctx.reply(t("wizard_ask_dtime", ctx.lang), CLOCK_PRESETS + back)
        elif step == "lead":
            await ctx.reply(t("wizard_ask_lead", ctx.lang),
                            self._lead_rows(ctx) + back)

    def _lead_rows(self, ctx: Ctx) -> ButtonRows:
        """Warning-lead buttons: ⏎ takes the advertised default (1 час)."""
        rows = [[(f"⏎ {t('btn_lead_60', ctx.lang)}", "ln:60")]]
        row: ButtonRows = []
        for key, mins in LEAD_KEYS:
            if mins == DEFAULT_LEAD_MIN:
                continue
            row.append((t(key, ctx.lang), f"ln:{mins}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    # ---------- typed answers ----------

    async def _wizard_input(self, ctx: Ctx, w: dict, text: str) -> None:
        if w["kind"] == "addplace":
            wanted = defang(text.strip())[:80]
            if await self._find_place_by_name(ctx.user.id, wanted):
                note = t("place_exists", ctx.lang)
                if not await self._note_on_places(ctx, note):
                    await ctx.reply(note)
                return
            place = await self.store.add_place(ctx.user.id, wanted)
            if place is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("no_places", ctx.lang)))
                return
            note = t("place_added", ctx.lang, name=place.name)
            # while the places screen lives, names are added onto it:
            # the screen (and its add-by-text mode) stays open until ✔️
            if not await self._note_on_places(ctx, note):
                self.wizards.pop(ctx.user.id, None)
                await ctx.reply(note)
            return
        if w["kind"] != "task":
            self.wizards.pop(ctx.user.id, None)
            return
        step = w["step"]
        if step == "title":
            w["data"]["title"] = defang(text.strip())[:200]
            if w.get("only") == "title":
                # editor: renaming is the whole change
                await self._wizard_finish(ctx, w, w["data"].get("lead",
                                                               DEFAULT_LEAD_MIN))
                return
            self._goto(w, "place")
            await self._ask_step(ctx, w)
        elif step == "place":
            # typing is a first-class answer here: with no places saved the bot
            # literally says "add one by text", and a new name is how the place
            # for this task gets born
            name = defang(text.strip())[:80]
            place = await self._find_or_add_place(ctx, name)
            if place is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_place", ctx.lang)))
                return
            await self._place_picked(ctx, w, place)
        elif step == "n":
            if not text.lstrip("-").isdigit() or int(text) <= 0:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_n", ctx.lang)))
                return
            w["data"]["n"] = int(text)
            self._goto(w, "anchor")
            await self._ask_step(ctx, w)
        elif step == "monthday":
            if not text.isdigit() or not 1 <= int(text) <= 31:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_month_day", ctx.lang)))
                return
            w["data"]["day"] = int(text)
            self._goto(w, "dtime")
            await self._ask_step(ctx, w)
        elif step == "lead":
            mins = parse_lead(text)
            if mins is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_lead", ctx.lang)))
                return
            w["data"]["lead"] = mins
            await self._wizard_finish(ctx, w, mins)
        elif step in self.WHEN_STEPS:
            # one reader for every moment the wizard asks about (the deadline,
            # the first repeat, the deadline clock), then a "did I get that
            # right?" step whenever the parser had to fill anything in
            await self._when_input(ctx, w, text)

    WHEN_STEPS = ("when", "whentime", "anchor", "dtime")
    WHEN_HINTS = {"when": "wizard_ask_when", "whentime": "wizard_ask_time",
                  "anchor": "wizard_ask_anchor_date", "dtime": "wizard_ask_dtime"}

    async def _when_input(self, ctx: Ctx, w: dict, text: str) -> None:
        """Typed answer to any date/time question: read, resolve, confirm."""
        step = w["step"]
        if not text.strip():
            await self._when_default(ctx, w)      # Enter = the offered default
            return
        got = parse_when(text, datetime.now(ctx.tz).date())
        value, confirm, err = self._resolve_when(ctx, w, got)
        if err:
            await ctx.reply(t(err, ctx.lang, hint=t(self.WHEN_HINTS[step], ctx.lang)))
            return
        if value is None:
            # only a day was given for a deadline that also needs a clock
            w["data"]["picked_date"] = as_day(got).day
            self._goto(w, "whentime")
            await self._ask_step(ctx, w)
            return
        if confirm:
            w["pending_when"] = {"step": step, "value": value}
            ctx.suppress_menu = True
            await ctx.reply(t("when_confirm", ctx.lang,
                              when=self._when_label(ctx, value)),
                            [[(t("when_yes", ctx.lang), "cfm:yes"),
                              (t("when_no", ctx.lang), "cfm:no")]]
                            + self._back_row(ctx, w))
            return
        await self._apply_when(ctx, w, step, value)

    def _resolve_when(self, ctx: Ctx, w: dict, got: When | None
                      ) -> tuple[object | None, bool, str | None]:
        """(value, needs_confirm, error) for the step being answered.

        A datetime for the one-off deadline, a date for the first repeat of an
        every-N-days rule, a clock for the deadline of a repeating task.
        """
        step = w["step"]
        if got is None:
            return None, False, "wizard_bad_input"
        day_w, clock_w = as_day(got), as_clock(got)
        if step == "anchor":
            return ((day_w.day, day_w.guessed, None) if day_w
                    else (None, False, "wizard_bad_input"))
        if step == "dtime":
            return ((clock_w.clock, clock_w.guessed, None) if clock_w
                    else (None, False, "wizard_bad_input"))
        day = day_w.day if day_w else None
        clock = clock_w.clock if clock_w else None
        confirm = bool(got.guessed)
        if clock and not day:
            # a bare time still needs a day: today if the moment is still
            # ahead of us, tomorrow otherwise
            picked = w["data"].get("picked_date")
            if picked:            # the day was already chosen by button
                day = picked
            else:
                now = datetime.now(ctx.tz)
                day = now.date()
                if at(day, clock, ctx.tz) <= now:
                    day += timedelta(days=1)
            confirm = True
        if not clock:
            return (None, False, None if day else "wizard_bad_input")
        value = at(day, clock, ctx.tz)
        # nonsense is settled before the "did I read that right?" card:
        # a deadline in the past would materialize nothing and never show up
        if value <= datetime.now(ctx.tz):
            return None, False, "wizard_deadline_past"
        return value, confirm, None

    async def _when_default(self, ctx: Ctx, w: dict) -> None:
        """An empty message: take the default the question advertises."""
        step = w["step"]
        if step == "anchor":
            await self._apply_when(ctx, w, step, datetime.now(ctx.tz).date())
        elif step == "dtime":
            await self._apply_when(ctx, w, step, DEFAULT_CLOCK)
        else:
            await ctx.reply(t("wizard_bad_input", ctx.lang,
                              hint=t(self.WHEN_HINTS[step], ctx.lang)))

    async def _apply_when(self, ctx: Ctx, w: dict, step: str, value) -> None:
        """Store a resolved moment and move the wizard on."""
        d = w["data"]
        if step in ("when", "whentime"):
            if value <= datetime.now(ctx.tz):
                await ctx.reply(t("wizard_deadline_past", ctx.lang,
                                  hint=t(self.WHEN_HINTS[step], ctx.lang)))
                return
            d["deadline"] = value
            if w.get("only") == "when":
                # editor: moving the deadline of a one-off is the whole change
                await self._wizard_finish(ctx, w, d.get("lead", DEFAULT_LEAD_MIN))
                return
            self._goto(w, "lead")
            await self._ask_step(ctx, w)
        elif step == "anchor":
            d["anchor"] = value.isoformat()
            self._goto(w, "dtime")
            await self._ask_step(ctx, w)
        elif step == "dtime":
            d["dtime"] = value
            if w.get("only") == "dtime":
                # editor: changing the deadline clock is the whole change
                await self._wizard_finish(ctx, w, d.get("lead", DEFAULT_LEAD_MIN))
                return
            self._goto(w, "lead")
            await self._ask_step(ctx, w)

    def _when_label(self, ctx: Ctx, value) -> str:
        """How a resolved moment is read back to the user, in their timezone."""
        if isinstance(value, datetime):
            local = value.astimezone(ctx.tz)
            return f"{local.strftime('%d.%m.%Y')} {hhmm(local.time())}"
        if isinstance(value, date):
            return day_ymd(value)
        return hhmm(value)

    async def _cb_cfm(self, ctx: Ctx, yes: bool) -> None:
        """✅/✖ on the "did I read that right?" card."""
        w = self.wizards.get(ctx.user.id)
        pend = (w or {}).get("pending_when")
        if not w or not pend:
            return
        del w["pending_when"]
        if not yes:
            await self._ask_step(ctx, w)   # same question, typed again
            return
        await self._apply_when(ctx, w, pend["step"], pend["value"])

    async def _place_picked(self, ctx: Ctx, w: dict, place) -> None:
        """A place was chosen by button or by name: continue the wizard."""
        w["data"]["place_id"] = place.id
        if w.get("only") == "place":
            # editor: moving the task to another place is the whole change
            await self._wizard_finish(ctx, w, w["data"].get("lead", DEFAULT_LEAD_MIN))
            return
        if w.get("pending_lead") is not None:
            # we only stopped to get a place back, the rest was already answered
            await self._wizard_finish(ctx, w, w.pop("pending_lead"))
            return
        self._goto(w, "schedule")
        await self._ask_step(ctx, w)

    async def _after_days(self, ctx: Ctx, w: dict) -> None:
        """Weekdays picked: a repeating rule still needs its deadline clock."""
        self._goto(w, "dtime")
        await self._ask_step(ctx, w)

    async def _cb_sched(self, ctx: Ctx, stype: str) -> None:
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") != "schedule":
            return
        w["data"]["stype"] = stype
        if stype == "once":
            self._goto(w, "when")
            await self._ask_step(ctx, w)
        elif stype == "every_n_days":
            self._goto(w, "n")
            await self._ask_step(ctx, w)
        elif stype == "monthly_day":
            self._goto(w, "monthday")
            await self._ask_step(ctx, w)
        elif stype == "weekdays":
            self._goto(w, "days")
            await self._wd_buttons(ctx, w)

    async def _cb_date(self, ctx: Ctx, offset: int) -> None:
        """Date picked via buttons (today/tomorrow/…) in the task wizard."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") not in ("when", "anchor"):
            return
        d = datetime.now(ctx.tz).date() + timedelta(days=offset)
        if w["step"] == "anchor":
            w["data"]["anchor"] = d.isoformat()
            self._goto(w, "dtime")
            await self._ask_step(ctx, w)
            return
        w["data"]["picked_date"] = d
        self._goto(w, "whentime")
        await self._ask_step(ctx, w)

    async def _cb_time(self, ctx: Ctx, arg: str) -> None:
        """Clock picked via buttons (deadline of a one-off or of a repeat)."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") not in ("whentime", "dtime"):
            return
        tval = read_clock(arg)
        if tval is None:
            return
        if w["step"] == "dtime":
            await self._apply_when(ctx, w, "dtime", tval)
            return
        # a tapped button needs no "did I read that right?" step
        picked = w["data"].get("picked_date") or self._next_day_for(ctx, tval)
        await self._apply_when(ctx, w, "whentime", at(picked, tval, ctx.tz))

    def _next_day_for(self, ctx: Ctx, tval: time) -> date:
        """Today if this clock is still ahead of us, tomorrow if it is not."""
        today = datetime.now(ctx.tz).date()
        return today if at(today, tval, ctx.tz) > datetime.now(ctx.tz) \
            else today + timedelta(days=1)

    async def _cb_lead(self, ctx: Ctx, arg: str) -> None:
        """Warning lead picked by button ("ln:120" = two hours before)."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") != "lead" or not arg.isdigit():
            return
        w["data"]["lead"] = int(arg)
        await self._wizard_finish(ctx, w, int(arg))

    def _wizard_task_fields(self, ctx: Ctx, w: dict) -> tuple[str, dict,
                                                              datetime | None, time]:
        """(schedule kind, its data, one-off deadline, deadline clock)."""
        d = w["data"]
        stype = d["stype"]
        sdata: dict = {}
        deadline = None
        if stype == "once":
            deadline = d.get("deadline")
        elif stype == "every_n_days":
            sdata = {"n": d["n"], "anchor": d["anchor"]}
        elif stype == "weekdays":
            sdata = {"weekdays": sorted(w["days"])}
        elif stype == "monthly_day":
            sdata = {"day": d["day"]}
        clock = d.get("dtime")
        if clock is None and deadline is not None:
            clock = deadline.astimezone(ctx.tz).time()
        return stype, sdata, deadline, clock or DEFAULT_CLOCK

    async def _wizard_finish(self, ctx: Ctx, w: dict, lead_min: int) -> None:
        if w.get("edit"):
            await self._wizard_update(ctx, w, lead_min)
            return
        d = w["data"]
        if await self.store.get_place(ctx.user.id, d.get("place_id") or 0) is None:
            # the place went away while this wizard was open (deleted from
            # another screen): ask again instead of writing a dangling FK
            d["place_id"] = None
            w["pending_lead"] = lead_min
            w["step"] = "place"
            await self._ask_step(ctx, w)
            return
        stype, sdata, deadline, clock = self._wizard_task_fields(ctx, w)
        task = await self.store.create_task(
            ctx.user.id, d["place_id"], d["title"], stype, sdata,
            deadline_time=clock, notify_before_min=lead_min, deadline=deadline)
        await self.store.materialize_occurrences(
            task, datetime.now(ctx.tz).date(), self.cfg.occurrence_horizon_days)
        place = await self.store.get_place(ctx.user.id, d["place_id"])
        self.wizards.pop(ctx.user.id, None)
        await ctx.reply("\n".join([t("wizard_saved", ctx.lang, title=task.title,
                                     place=place.name if place else "?")]
                                  + await self._task_lines(ctx, task)),
                        self._task_open_rows(ctx, task))

    async def _wizard_update(self, ctx: Ctx, w: dict, lead_min: int) -> None:
        """Apply one edited field to an existing task and rebuild its future."""
        from taskshed.core.models import ScheduleType

        task = await self.store.get_task(ctx.user.id, int(w["edit"]))
        if task is None:
            self.wizards.pop(ctx.user.id, None)
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        stype, sdata, deadline, clock = self._wizard_task_fields(ctx, w)
        place_id = int(w["data"].get("place_id") or task.place_id)
        if await self.store.get_place(ctx.user.id, place_id) is None:
            # someone deleted the place while this edit dialog was open
            w["data"]["place_id"] = None
            w["pending_lead"] = lead_min
            w["step"] = "place"
            await self._ask_step(ctx, w)
            return
        task.title = w["data"].get("title", task.title)
        task.place_id = place_id
        task.schedule_type = ScheduleType(stype)
        task.schedule_data = sdata
        task.deadline_time = clock
        task.notify_before_min = lead_min
        task.deadline = deadline
        if stype != "once":
            task.start_at = None   # a repeat has one deadline per day
        await self.store.save_task(task)
        # open repeats are recomputed from the new rule; finished history stays
        await self.store.drop_open_occurrences(task.id)
        await self.store.materialize_occurrences(
            task, datetime.now(ctx.tz).date(), self.cfg.occurrence_horizon_days)
        self.wizards.pop(ctx.user.id, None)
        ctx.suppress_menu = True  # the card below carries the edit buttons
        await ctx.reply("\n".join([t("edit_saved", ctx.lang, title=task.title)]
                                  + await self._task_lines(ctx, task)),
                        self._task_open_rows(ctx, task))

    async def _wd_buttons(self, ctx: Ctx, w: dict) -> None:
        # chosen days carry a ✅; on Telegram the message is edited in place
        # instead of sending a fresh one per tap. Days split 4+3: seven tiny
        # buttons per row get elided to "…" by clients once ✅ is added.
        day_btns = [(("✅ " if i in w["days"] else "") + t(k, ctx.lang), f"wd:{i}")
                    for i, k in enumerate(WD_KEYS)]
        rows = [day_btns[:4], day_btns[4:],
                [(t("wd_done", ctx.lang), "wdsubmit")]] + self._back_row(ctx, w)
        text = t("wizard_ask_days", ctx.lang)
        mid = w.get("wd_msg")
        await self._strip_keyboards(ctx.messenger, ctx.account, ctx.user.id,
                                    keep=mid or "")
        if mid and await ctx.messenger.edit_buttons(ctx.account, mid, text, rows):
            self._track_kb(ctx.user.id, ctx.account, mid, text)
            ctx.replied = True
            return
        new_id = await ctx.messenger.send_buttons(ctx.account, text, rows)
        ctx.replied = True
        if new_id:
            w["wd_msg"] = new_id
            self._track_kb(ctx.user.id, ctx.account, new_id, text)
