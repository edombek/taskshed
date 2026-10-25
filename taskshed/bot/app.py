"""BotApp: dispatches RawEvents to commands / wizards / buttons.

Platform-independent: Telegram and VK adapters only translate their wire
formats into RawEvent + implement IMessenger.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.config import BotCfg
from taskshed.core.models import Account, User, UserSettings
from taskshed.core.models import Place as PlaceDC
from taskshed.i18n import normalize, t
from taskshed.messaging.base import ButtonRows, IMessenger, RawEvent
from taskshed.storage.repositories import Store

log = logging.getLogger(__name__)

COMMANDS = {
    "start": "start", "помощь": "help", "help": "help",
    "гдея": "where", "where": "where",
    "задачи": "tasks", "tasks": "tasks",
    "задача": "task", "task": "task",
    "места": "places", "places": "places",
    "удалить": "delete", "delete": "delete",
    "напоминания": "reminders", "reminders": "reminders",
    "статистика": "stats", "stats": "stats",
    "привязать": "link", "link": "link",
    "отвязать": "unlink", "unlink": "unlink",
    "lang": "lang", "язык": "lang",
    "отмена": "cancel", "cancel": "cancel",
}

PERIODS = {"неделя": 7, "week": 7, "месяц": 30, "month": 30,
           "год": 365, "year": 365, "всё": None, "все": None, "all": None}
WD_KEYS = ["wd_mo", "wd_tu", "wd_we", "wd_th", "wd_fr", "wd_sa", "wd_su"]
SNOOZE_MIN = 60

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})$")


def _parse_hhmm(s: str) -> time | None:
    m = _TIME_RE.match(s.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return time(h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


class Ctx:
    """Per-event context passed to all handlers."""

    def __init__(self, app: BotApp, ev: RawEvent, user: User, account: Account,
                 settings: UserSettings, messenger: IMessenger):
        self.app, self.ev, self.user, self.account = app, ev, user, account
        self.settings = settings
        self.messenger = messenger
        self.lang = user.language
        self.tz = ZoneInfo(settings.tz)

    async def reply(self, text: str, buttons: ButtonRows | None = None) -> None:
        if buttons:
            await self.messenger.send_buttons(self.account, text, buttons)
        else:
            await self.messenger.send_text(self.account, text)

    async def place_buttons(self, question_key: str) -> None:
        places = await self.app.store.list_places(self.user.id)
        if not places:
            await self.reply(t("no_places", self.lang))
            return
        rows = [[(p.name, f"place:{p.id}")] for p in places]
        await self.reply(t(question_key, self.lang), rows)


class BotApp:
    def __init__(self, store: Store, cfg: BotCfg, messengers: dict[str, IMessenger]):
        self.store = store
        self.cfg = cfg
        self.messengers = messengers
        # in-memory transient dialogs: wizard / one-shot text inputs
        self.wizards: dict[int, dict] = {}
        self._pending_inputs: dict[int, str] = {}

    # ---------- entry ----------

    async def handle(self, ev: RawEvent) -> None:
        messenger = self.messengers[ev.platform]
        account = await self.store.get_account_by_key(ev.platform, ev.platform_user_id)
        if account is None:
            user, account = await self.store.register_account(
                ev.platform, ev.platform_user_id, language=self.cfg.default_language,
            )
        else:
            user = await self.store.get_user(account.user_id)
            if user is None:  # orphan account after a merge — re-register
                user, _ = await self.store.register_account(
                    ev.platform, ev.platform_user_id, language=self.cfg.default_language,
                )
                account = await self.store.get_account_by_key(ev.platform, ev.platform_user_id)
        settings = await self.store.get_settings(user.id)
        await self.store.touch_account(account.id)
        ctx = Ctx(self, ev, user, account, settings, messenger)
        try:
            if ev.callback:
                await self._on_callback(ctx, ev.callback)
            else:
                await self._on_text(ctx, (ev.text or "").strip())
        except Exception:
            log.exception("event handling failed: %r", ev)

    async def ensure_materialized(self, ctx: Ctx) -> None:
        today = datetime.now(ctx.tz).date()
        for task in await self.store.list_tasks(ctx.user.id):
            await self.store.materialize_occurrences(
                task, today, self.cfg.occurrence_horizon_days, ctx.settings.tz,
            )

    async def _on_text(self, ctx: Ctx, text: str) -> None:
        wizard = self.wizards.get(ctx.user.id)
        if not text and not wizard:
            return
        if text.startswith("/"):
            name, _, rest = text[1:].partition(" ")
            name = name.split("@")[0].lower()
            if name == "отмена" or COMMANDS.get(name) == "cancel":
                self.wizards.pop(ctx.user.id, None)
                self._pending_inputs.pop(ctx.user.id, None)
                await ctx.reply(t("wizard_cancelled", ctx.lang))
                return
            cmd = COMMANDS.get(name)
            if wizard and cmd is None:
                pass  # "/something" unknown while wizard active → below
            if cmd:
                handler = getattr(self, f"_cmd_{cmd}")
                await handler(ctx, rest.strip())
                return
            await ctx.reply(t("unknown_cmd", ctx.lang))
            return
        if wizard:
            await self._wizard_input(ctx, wizard, text)
            return
        pending = self._pending_inputs.get(ctx.user.id)
        if pending and pending.startswith("reminput:"):
            self._pending_inputs.pop(ctx.user.id, None)
            await self._reminput(ctx, pending.split(":", 1)[1], text)
            return
        # bare text with no places at all → treat as place name (first-run UX)
        if not await self.store.list_places(ctx.user.id):
            if await self.store.add_place(ctx.user.id, text):
                await ctx.reply(t("place_added", ctx.lang, name=text))
            return
        await ctx.reply(t("unknown_cmd", ctx.lang))

    # ---------- text ----------

    async def _cmd_start(self, ctx: Ctx, _: str) -> None:
        await ctx.reply(t("greet", ctx.lang))

    async def _cmd_help(self, ctx: Ctx, _: str) -> None:
        await ctx.reply(t("help_text", ctx.lang))

    async def _cmd_where(self, ctx: Ctx, _: str) -> None:
        self._pending_inputs[ctx.user.id] = "where"
        await ctx.place_buttons("where_question")

    async def _cmd_tasks(self, ctx: Ctx, _: str) -> None:
        await self.ensure_materialized(ctx)
        loc = await self.store.get_location(ctx.user.id)
        if loc is None:
            self._pending_inputs[ctx.user.id] = "tasks"
            await ctx.place_buttons("where_question")
            return
        place, _ = loc
        await self._send_task_list(ctx, place)

    async def _cmd_task(self, ctx: Ctx, _: str) -> None:
        self.wizards[ctx.user.id] = {"kind": "task", "step": "title", "data": {}, "days": set()}
        await ctx.reply(t("wizard_ask_title", ctx.lang))

    async def _cmd_places(self, ctx: Ctx, arg: str) -> None:
        places = await self.store.list_places(ctx.user.id)
        if places:
            rows = [[(f"🗑 {p.name}", f"pdel:{p.id}")] for p in places]
            await ctx.reply(
                t("places_list", ctx.lang, names=", ".join(p.name for p in places))
                + "\n" + t("no_places", ctx.lang),
                rows,
            )
        else:
            await ctx.reply(t("no_places", ctx.lang))
        self.wizards[ctx.user.id] = {"kind": "addplace"}

    async def _cmd_delete(self, ctx: Ctx, arg: str) -> None:
        if not arg.isdigit():
            return
        tasks = await self.store.list_tasks(ctx.user.id, active_only=False)
        idx = int(arg) - 1
        if 0 <= idx < len(tasks) and await self.store.delete_task(tasks[idx].id, ctx.user.id):
            await ctx.reply(t("task_deleted", ctx.lang))
        else:
            await ctx.reply(t("task_not_found", ctx.lang))

    async def _cmd_stats(self, ctx: Ctx, arg: str) -> None:
        period, fmt = "month", "text"
        for token in arg.lower().split():
            if token in PERIODS:
                period = token
            elif token in ("csv", "xlsx", "график", "chart"):
                fmt = token
        days = PERIODS[period]
        now = datetime.now(UTC)
        # +1 day so today's still-future EOD deadlines belong to the period
        end = now + timedelta(days=1)
        start = (end - timedelta(days=days)) if days else datetime(2000, 1, 1, tzinfo=UTC)
        occs = await self.store.occurrences_in_range(ctx.user.id, start, end)
        past = [(o, task) for o, task in occs if o.due_at <= now or o.status == "done"]
        if not past:
            await ctx.reply(t("stats_no_data", ctx.lang))
            return
        if fmt in ("csv", "xlsx", "график", "chart"):
            await self._stats_file(ctx, past, period, fmt)
        else:
            await ctx.reply(self._stats_text(ctx, past, period))
            if ctx.settings.stats_format != "text":
                await self._stats_file(ctx, past, period, "chart")

    async def _cmd_link(self, ctx: Ctx, arg: str) -> None:
        if not arg:
            code = await self.store.create_link_code(ctx.user.id, ctx.ev.platform)
            await ctx.reply(t("link_code_msg", ctx.lang, code=code))
            return
        owner = await self.store.redeem_link_code(arg)
        if owner is None:
            await ctx.reply(t("link_bad_code", ctx.lang))
            return
        if owner == ctx.user.id:
            await ctx.reply(t("link_ok", ctx.lang))
            return
        # merge current user's data into the code owner, then move account over
        await self.store.attach_account(ctx.account, owner)
        await self.store.merge_user(ctx.user.id, owner)
        await ctx.reply(t("link_ok", ctx.lang))

    async def _cmd_unlink(self, ctx: Ctx, _: str) -> None:
        accounts = await self.store.get_accounts(ctx.user.id)
        if len(accounts) < 2:
            return
        await self.store.detach_account(ctx.account.id, ctx.user.id)
        await ctx.reply(t("unlink_done", ctx.lang))

    async def _cmd_lang(self, ctx: Ctx, arg: str) -> None:
        lang = normalize(arg) if arg in ("ru", "en", "русский", "russian") else None
        if lang:
            await self._set_lang(ctx, lang)
            return
        await ctx.reply(t("lang_menu", ctx.lang),
                        [[("Русский", "lang:ru"), ("English", "lang:en")]])

    async def _set_lang(self, ctx: Ctx, lang: str) -> None:
        await self.store.set_language(ctx.user.id, lang)
        ctx.user.language = lang
        await ctx.reply(t("lang_set", lang))

    async def _cmd_reminders(self, ctx: Ctx, _: str) -> None:
        await self._rem_status(ctx)

    def _stats_text(self, ctx: Ctx, past, period: str) -> str:
        agg: dict[int, dict] = {}
        for occ, task in past:
            a = agg.setdefault(task.id, {"title": task.title, "done": 0, "missed": 0, "total": 0})
            a["total"] += 1
            if occ.status == "done":
                a["done"] += 1
            elif occ.status == "missed":
                a["missed"] += 1
        lines = [t("stats_header", ctx.lang, period=t(f"period_{period}", ctx.lang))]
        for a in agg.values():
            pct = round(100 * a["done"] / a["total"]) if a["total"] else 0
            lines.append(t("stats_row", ctx.lang, title=a["title"], done=a["done"],
                           missed=a["missed"], total=a["total"], percent=pct))
        return "\n".join(lines)

    async def _stats_file(self, ctx: Ctx, past, period: str, fmt: str) -> None:
        import os
        import shutil
        import tempfile

        from taskshed.core import stats as sagg
        from taskshed.stats import charts, export

        tmp = tempfile.mkdtemp(prefix="taskshed_stats_")
        try:
            task_rows = sagg.per_task(past)
            place_names = {p.id: p.name for p in await self.store.list_places(ctx.user.id)}
            place_rows = sagg.per_place(past, place_names)
            days = sagg.per_day(past, ctx.settings.tz)
            if fmt == "csv":
                path = os.path.join(tmp, "stats.csv")
                export.write_csv(task_rows, path)
                await ctx.messenger.send_document(
                    ctx.account, path, "stats.csv", t("stats_sent_doc", ctx.lang))
            elif fmt == "xlsx":
                path = os.path.join(tmp, "stats.xlsx")
                export.write_xlsx(task_rows, place_rows, path)
                await ctx.messenger.send_document(
                    ctx.account, path, "stats.xlsx", t("stats_sent_doc", ctx.lang))
            else:  # chart
                png = os.path.join(tmp, "stats.png")
                produced = charts.daily_bar(days, ctx.lang, png)
                if not produced:
                    await ctx.reply(t("stats_no_data", ctx.lang))
                    return
                await ctx.messenger.send_image(ctx.account, produced,
                                               t("stats_sent_doc", ctx.lang))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- wizard ----------

    async def _wizard_input(self, ctx: Ctx, w: dict, text: str) -> None:
        if w["kind"] == "addplace":
            if await self.store.add_place(ctx.user.id, text):
                self.wizards.pop(ctx.user.id, None)
                await ctx.reply(t("place_added", ctx.lang, name=text.strip()))
            else:
                await ctx.reply(t("place_exists", ctx.lang))
            return
        if w["kind"] != "task":
            self.wizards.pop(ctx.user.id, None)
            return
        step = w["step"]
        if step == "title":
            w["data"]["title"] = text.strip()[:200]
            w["step"] = "place"
            await ctx.place_buttons("wizard_ask_place")
        elif step == "n":
            if not text.lstrip("-").isdigit() or int(text) <= 0:
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=t("wizard_ask_n", ctx.lang)))
                return
            w["data"]["n"] = int(text)
            w["step"] = "anchor"
            await ctx.reply(t("wizard_ask_anchor_date", ctx.lang))
        elif step == "anchor":
            anchor = text.strip() or datetime.now(ctx.tz).date().isoformat()
            try:
                datetime.strptime(anchor, "%Y-%m-%d")
            except ValueError:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_anchor_date", ctx.lang)))
                return
            w["data"]["anchor"] = anchor
            await self._wizard_ask_reminder(ctx, w)
        elif step == "monthday":
            if not text.isdigit() or not 1 <= int(text) <= 31:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_month_day", ctx.lang)))
                return
            w["data"]["day"] = int(text)
            await self._wizard_ask_reminder(ctx, w)
        elif step == "when":
            m = _DT_RE.match(text.strip())
            hint = t("wizard_ask_when", ctx.lang)
            if not m:
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=hint))
                return
            y, mo, d, h, mi = (int(g) for g in m.groups())
            try:
                w["data"]["deadline"] = datetime(y, mo, d, h, mi, tzinfo=ctx.tz)
            except ValueError:
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=hint))
                return
            await self._wizard_ask_reminder(ctx, w)
        elif step == "reminder":
            rt = time(9, 0)
            if text.strip():
                parsed = _parse_hhmm(text)
                if parsed is None:
                    await ctx.reply(t("wizard_bad_input", ctx.lang,
                                      hint=t("wizard_ask_reminder", ctx.lang)))
                    return
                rt = parsed
            await self._wizard_finish(ctx, w, rt)

    async def _wizard_ask_reminder(self, ctx: Ctx, w: dict) -> None:
        w["step"] = "reminder"
        await ctx.reply(t("wizard_ask_reminder", ctx.lang))

    async def _wizard_finish(self, ctx: Ctx, w: dict, rt: time) -> None:
        d = w["data"]
        stype = d["stype"]
        sdata: dict = {}
        deadline = None
        if stype == "once":
            deadline = d["deadline"]
        elif stype == "every_n_days":
            sdata = {"n": d["n"], "anchor": d["anchor"]}
        elif stype == "weekdays":
            sdata = {"weekdays": sorted(w["days"])}
        elif stype == "monthly_day":
            sdata = {"day": d["day"]}
        task = await self.store.create_task(
            ctx.user.id, d["place_id"], d["title"], stype, sdata, rt, deadline,
        )
        await self.store.materialize_occurrences(
            task, datetime.now(ctx.tz).date(), self.cfg.occurrence_horizon_days, ctx.settings.tz,
        )
        place = await self.store.get_place(ctx.user.id, d["place_id"])
        self.wizards.pop(ctx.user.id, None)
        await ctx.reply(t("wizard_saved", ctx.lang, title=task.title,
                          place=place.name if place else "?"))

    # ---------- callbacks ----------

    async def _on_callback(self, ctx: Ctx, cb: str) -> None:
        action, _, arg = cb.partition(":")
        if action == "place":
            await self._cb_place(ctx, int(arg))
        elif action == "sched":
            await self._cb_sched(ctx, arg)
        elif action == "wd":
            w = self.wizards.get(ctx.user.id)
            if w and w.get("step") == "days":
                w["days"].symmetric_difference_update({int(arg)})
                await self._wd_buttons(ctx, w)
        elif cb == "wdsubmit":
            w = self.wizards.get(ctx.user.id)
            if w and w.get("step") == "days":
                if not w["days"]:
                    await ctx.reply(t("wizard_no_selection", ctx.lang))
                    return
                await self._wizard_ask_reminder(ctx, w)
        elif action == "pdel":
            if await self.store.delete_place(ctx.user.id, int(arg)):
                await ctx.reply(t("place_removed", ctx.lang))
        elif action == "done":
            await self._cb_done(ctx, int(arg))
        elif action == "snooze":
            await self._cb_snooze(ctx, int(arg))
        elif action == "lang":
            await self._set_lang(ctx, normalize(arg))
        elif action == "rem":
            await self._cb_rem(ctx, arg)

    async def _cb_place(self, ctx: Ctx, place_id: int) -> None:
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            return
        w = self.wizards.get(ctx.user.id)
        if w and w.get("kind") == "task" and w.get("step") == "place":
            w["data"]["place_id"] = place.id
            w["step"] = "schedule"
            await ctx.reply(t("wizard_ask_schedule", ctx.lang), [
                [(t("sched_once", ctx.lang), "sched:once"),
                 (t("sched_every_n", ctx.lang), "sched:every_n_days")],
                [(t("sched_weekdays", ctx.lang), "sched:weekdays"),
                 (t("sched_monthly", ctx.lang), "sched:monthly_day")],
            ])
            return
        await self.store.set_location(ctx.user.id, place.id)
        was_pending = ctx.settings.pending_reminder
        await self.store.update_settings(ctx.user.id, pending_reminder=False)
        intent = self._pending_inputs.pop(ctx.user.id, None)
        if intent == "tasks":
            await self._send_task_list(ctx, place)
        else:
            await ctx.reply(t("where_set", ctx.lang, name=place.name))
        if was_pending:
            await self._digest_now(ctx.user.id, ctx.account)

    async def _cb_sched(self, ctx: Ctx, stype: str) -> None:
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") != "schedule":
            return
        w["data"]["stype"] = stype
        if stype == "once":
            w["step"] = "when"
            await ctx.reply(t("wizard_ask_when", ctx.lang))
        elif stype == "every_n_days":
            w["step"] = "n"
            await ctx.reply(t("wizard_ask_n", ctx.lang))
        elif stype == "monthly_day":
            w["step"] = "monthday"
            await ctx.reply(t("wizard_ask_month_day", ctx.lang))
        elif stype == "weekdays":
            w["step"] = "days"
            await self._wd_buttons(ctx, w)

    async def _wd_buttons(self, ctx: Ctx, w: dict) -> None:
        await ctx.reply(t("wizard_ask_days", ctx.lang),
                        [[(t(k, ctx.lang), f"wd:{i}") for i, k in enumerate(WD_KEYS)],
                         [(t("wd_done", ctx.lang), "wdsubmit")]])

    async def _cb_done(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.mark_done(occ_id, ctx.ev.platform):
            await ctx.reply(t("msg_done", ctx.lang, title=got[1].title))

    async def _cb_snooze(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.snooze(occ_id, SNOOZE_MIN):
            await ctx.reply(t("msg_snoozed", ctx.lang, minutes=SNOOZE_MIN, title=got[1].title))

    # ---------- task list / digest ----------

    async def _send_task_list(self, ctx: Ctx, place: PlaceDC) -> None:
        today = datetime.now(ctx.tz).date()
        occs = await self.store.occurrences_today(ctx.user.id, today, ctx.settings.tz,
                                                  place_id=place.id)
        if not occs:
            await ctx.reply(t("tasks_empty", ctx.lang, place=place.name))
            return
        lines = [t("tasks_header", ctx.lang, place=place.name)]
        rows: ButtonRows = []
        for i, (occ, task) in enumerate(occs, 1):
            due_local = occ.due_at.astimezone(ctx.tz).strftime("%d.%m %H:%M")
            mark = t("task_overdue_mark", ctx.lang) if occ.status == "missed" else ""
            lines.append(t("task_row", ctx.lang, n=i, title=task.title, due=due_local) + mark)
            rows.append([(t("btn_done", ctx.lang), f"done:{occ.id}"),
                         (t("btn_snooze", ctx.lang), f"snooze:{occ.id}")])
        await ctx.reply("\n".join(lines), rows)

    async def build_digest(self, user_id: int, settings: UserSettings, place: PlaceDC):
        """(text, buttons) for today's tasks at `place`, or None if nothing."""
        tz = ZoneInfo(settings.tz)
        today = datetime.now(tz).date()
        occs = await self.store.occurrences_today(user_id, today, settings.tz, place_id=place.id)
        if not occs:
            return None
        lang = "ru"
        user = await self.store.get_user(user_id)
        if user:
            lang = user.language
        lines = [t("tasks_header", lang, place=place.name)]
        rows: ButtonRows = []
        for i, (occ, task) in enumerate(occs, 1):
            due = occ.due_at.astimezone(tz).strftime("%d.%m %H:%M")
            mark = t("task_overdue_mark", lang) if occ.status == "missed" else ""
            lines.append(t("task_row", lang, n=i, title=task.title, due=due) + mark)
            rows.append([(t("btn_done", lang), f"done:{occ.id}"),
                         (t("btn_snooze", lang), f"snooze:{occ.id}")])
        return "\n".join(lines), rows

    async def _digest_now(self, user_id: int, origin: Account) -> bool:
        settings = await self.store.get_settings(user_id)
        loc = await self.store.get_location(user_id)
        if loc is None:
            return False
        digest = await self.build_digest(user_id, settings, loc[0])
        if digest is None:
            return False
        await self._fanout(user_id, settings, origin, *digest)
        return True

    async def notify_user(self, user_id: int, text: str,
                          buttons: ButtonRows | None = None) -> None:
        """Send to every linked account (or the most recently active one)."""
        settings = await self.store.get_settings(user_id)
        accounts = await self.store.get_accounts(user_id)
        if settings.notify_platforms == "primary":
            accounts = sorted(accounts, key=lambda a: a.last_seen or a.linked_at)[-1:]
        for acc in accounts:
            messenger = self.messengers.get(acc.platform)
            if messenger:
                if buttons:
                    await messenger.send_buttons(acc, text, buttons)
                else:
                    await messenger.send_text(acc, text)

    async def _fanout(self, user_id: int, settings: UserSettings,
                      origin: Account | None, text: str, buttons: ButtonRows) -> None:
        accounts = await self.store.get_accounts(user_id)
        for acc in accounts:
            if settings.notify_platforms == "primary" and origin is not None:
                if acc.platform != origin.platform:
                    continue
            messenger = self.messengers.get(acc.platform)
            if messenger:
                await messenger.send_buttons(acc, text, buttons)

    # ---------- reminder settings ----------

    async def _rem_status(self, ctx: Ctx) -> None:
        st = ctx.settings
        status = t("reminders_status", ctx.lang,
                   on=t("rem_on" if st.reminders_on else "rem_off", ctx.lang),
                   mode=st.remind_mode,
                   times=" ".join(st.remind_times) if st.remind_mode == "times"
                   else f"{st.remind_interval_min} min",
                   quiet=f"{st.quiet_start:%H:%M}–{st.quiet_end:%H:%M}",
                   ttl=st.location_ttl_hours, tz=st.tz)
        await ctx.reply(status, [
            [(t("btn_rem_toggle", ctx.lang), "rem:toggle")],
            [(t("btn_rem_times", ctx.lang), "rem:times"),
             (t("btn_rem_interval", ctx.lang), "rem:interval")],
            [(t("btn_rem_quiet", ctx.lang), "rem:quiet"),
             (t("btn_rem_ttl", ctx.lang), "rem:ttl")],
            [(t("btn_rem_tz", ctx.lang), "rem:tz")],
        ])

    async def _cb_rem(self, ctx: Ctx, arg: str) -> None:
        if arg == "toggle":
            st = await self.store.update_settings(
                ctx.user.id, reminders_on=not ctx.settings.reminders_on,
            )
            ctx.settings = st
            await self._rem_status(ctx)
            return
        if arg in ("times", "interval", "quiet", "ttl", "tz"):
            self._pending_inputs[ctx.user.id] = f"reminput:{arg}"

    async def _reminput(self, ctx: Ctx, field: str, text: str) -> None:
        values: dict = {}
        if field == "times":
            parts = text.split()
            parsed = [_parse_hhmm(p) for p in parts]
            if parts and all(p is not None for p in parsed):
                values = {"remind_mode": "times",
                          "remind_times": [p.strftime("%H:%M") for p in parsed]}
            else:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("btn_rem_times", ctx.lang)))
                return
        elif field == "interval":
            if not text.isdigit() or int(text) < 5:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("btn_rem_interval", ctx.lang)))
                return
            values = {"remind_mode": "interval", "remind_interval_min": int(text)}
        elif field == "quiet":
            parts = text.split()
            parsed = [_parse_hhmm(p) for p in parts]
            if len(parts) != 2 or any(p is None for p in parsed):
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("btn_rem_quiet", ctx.lang)))
                return
            values = {"quiet_start": parsed[0], "quiet_end": parsed[1]}
        elif field == "ttl":
            if not text.isdigit() or not 1 <= int(text) <= 168:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("btn_rem_ttl", ctx.lang)))
                return
            values = {"location_ttl_hours": int(text)}
        elif field == "tz":
            try:
                ZoneInfo(text.strip())
            except (KeyError, ValueError):
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("btn_rem_tz", ctx.lang)))
                return
            values = {"tz": text.strip()}
        st = await self.store.update_settings(ctx.user.id, **values)
        ctx.settings = st
        await ctx.reply(t("rem_updated", ctx.lang,
                         status=t("reminders_status", ctx.lang,
                                 on=t("rem_on" if st.reminders_on else "rem_off", ctx.lang),
                                 mode=st.remind_mode,
                                 times=" ".join(st.remind_times) if st.remind_mode == "times"
                                 else f"{st.remind_interval_min} min",
                                 quiet=f"{st.quiet_start:%H:%M}–{st.quiet_end:%H:%M}",
                                 ttl=st.location_ttl_hours, tz=st.tz)))
