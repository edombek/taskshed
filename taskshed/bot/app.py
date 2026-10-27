"""BotApp: dispatches RawEvents to commands / wizards / buttons.

Platform-independent: Telegram and VK adapters only translate their wire
formats into RawEvent + implement IMessenger.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.bot.timeparse import When, as_clock, as_day, day_ymd, hhmm, parse_when, read_clock
from taskshed.config import BotCfg
from taskshed.core.models import Account, Platform, User, UserSettings
from taskshed.core.models import Place as PlaceDC
from taskshed.core.stats import occurrence_moment
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
    "изменить": "edit", "edit": "edit", "редактировать": "edit",
    "напоминания": "settings", "reminders": "settings",
    "настройки": "settings", "settings": "settings",
    "статистика": "stats", "stats": "stats",
    "привязать": "link", "link": "link",
    "отвязать": "unlink", "unlink": "unlink",
    "lang": "lang", "язык": "lang",
    "отмена": "cancel", "cancel": "cancel",
}

PERIODS = {"неделя": ("week", 7), "week": ("week", 7),
           "месяц": ("month", 30), "month": ("month", 30),
           "год": ("year", 365), "year": ("year", 365),
           "всё": ("all", None), "все": ("all", None), "all": ("all", None)}
WD_KEYS = ["wd_mo", "wd_tu", "wd_we", "wd_th", "wd_fr", "wd_sa", "wd_su"]
# time presets offered as inline buttons (Telegram cannot send an empty
# message, so the 09:00 default reminder gets a ⏎ button instead)
TM_PRESETS = [[("08:00", "tm:08:00"), ("12:00", "tm:12:00"), ("15:00", "tm:15:00")],
              [("18:00", "tm:18:00"), ("21:00", "tm:21:00"), ("23:59", "tm:23:59")]]
REMINDER_TMS = [[("⏎ 09:00", "tm:09:00")],
                [("08:00", "tm:08:00"), ("12:00", "tm:12:00")],
                [("18:00", "tm:18:00"), ("21:00", "tm:21:00")]]
# snooze durations offered as buttons: 15m/1h/4h/24h

# URLs are defanged in user-supplied strings (task titles, place names):
# a bot that sends clickable links can get flagged and banned by Telegram.
_URL_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|(?<![\w./@-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
    r"(?:/\S*)?"
)


def _defang(text: str) -> str:
    """Neutralize links: example.com -> example[.]com (not clickable)."""
    return _URL_RE.sub(lambda m: m.group(0).replace(".", "[.]"), text)


def _at(d: date, tval: time, tz: ZoneInfo) -> datetime:
    """Wall-clock moment of `tval` on day `d` in `tz`."""
    return datetime(d.year, d.month, d.day, tval.hour, tval.minute, tzinfo=tz)


class Ctx:
    """Per-event context passed to all handlers."""

    def __init__(self, app: BotApp, ev: RawEvent, user: User, account: Account,
                 settings: UserSettings, messenger: IMessenger):
        self.app, self.ev, self.user, self.account = app, ev, user, account
        self.settings = settings
        self.messenger = messenger
        self.lang = user.language
        self.tz = ZoneInfo(settings.tz)
        self.replied = False       # handler answered something
        self.suppress_menu = False # answer already has interactive buttons (menu would clutter)

    async def reply(self, text: str, buttons: ButtonRows | None = None) -> None:
        # every previous keyboard in this chat becomes stale right here
        await self.app._strip_keyboards(self.messenger, self.account, self.user.id)
        self.replied = True
        if buttons:
            mid = await self.messenger.send_buttons(self.account, text, buttons)
            self.app._track_kb(self.user.id, self.account, mid, text)
        else:
            await self.messenger.send_text(self.account, text)

    async def place_buttons(self, question_key: str,
                            extra_rows: ButtonRows | None = None) -> None:
        places = await self.app.store.list_places(self.user.id)
        if not places:
            # keep the question on screen: the wizard needs to be answerable
            # by typing a brand new place name
            await self.reply(f"{t(question_key, self.lang)}\n{t('no_places', self.lang)}",
                             extra_rows or None)
            return
        rows = [[(p.name, f"place:{p.id}")] for p in places] + (extra_rows or [])
        await self.reply(t(question_key, self.lang), rows)


class BotApp:
    def __init__(self, store: Store, cfg: BotCfg, messengers: dict[str, IMessenger]):
        self.store = store
        self.cfg = cfg
        self.messengers = messengers
        # in-memory transient dialogs: wizard / one-shot text inputs
        self.wizards: dict[int, dict] = {}
        self._pending_inputs: dict[int, str] = {}
        # last reminder-settings message per (user, account): value changes
        # edit it in place instead of stacking new copies in the chat
        self._rem_msgs: dict[tuple[int, int], str] = {}
        # live keyboards per (user, account): {message_id: text}. A newer
        # answer strips these, so stale buttons cannot be tapped twice
        self._kb_msgs: dict[tuple[int, int], dict[str, str]] = {}
        # last tab of the task editor per user: where ⬅️ Назад goes back to
        self._edit_tab: dict[int, str] = {}
        # a "where are you?" that interrupted something else: the interrupted
        # dialog lives here and is replayed once the place is known
        self._await_loc: dict[int, dict] = {}

    def _track_kb(self, user_id: int, account: Account, mid: str | None,
                  text: str) -> None:
        """Remember an interactive message so its keyboard can be stripped."""
        if mid:
            self._kb_msgs.setdefault((user_id, account.id), {})[mid] = text

    async def _strip_keyboards(self, messenger: IMessenger, account: Account,
                               user_id: int, keep: str = "") -> None:
        """Disarm every keyboard that is no longer the newest screen.

        `keep` is the id a handler is about to re-render in place: it stays
        armed (the edit that follows replaces its buttons anyway).
        """
        live = self._kb_msgs.pop((user_id, account.id), {})
        for mid, text in live.items():
            if mid == keep:
                continue
            try:
                await messenger.edit_buttons(account, mid, text, [])
            except Exception:  # deleted/aged-out message: nothing to disarm
                log.debug("keyboard strip failed for message %s", mid)
        if keep and keep in live:
            self._kb_msgs[(user_id, account.id)] = {keep: live[keep]}

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
        # after a *completed* command: a short "anything else?" with the menu.
        # Not in the middle of a wizard (its own screen already carries the next
        # step's buttons), not when the answer itself has input buttons.
        wizard_open = (self.wizards.get(user.id) or {}).get("kind") == "task"
        if ctx.replied and not ctx.suppress_menu and not wizard_open:
            try:
                mid = await messenger.send_buttons(
                    account, t("menu_ask_more", user.language),
                    self._menu_rows(user.language))
                # the follow-up menu joins the live screens: the *next*
                # answer disarms it together with the command's own buttons
                self._track_kb(user.id, account, mid,
                               t("menu_ask_more", user.language))
            except Exception:
                log.exception("menu follow-up failed for %s", user.id)

    async def ensure_materialized(self, ctx: Ctx, ahead_days: int = 0,
                                  back_days: int = 0) -> None:
        today = datetime.now(ctx.tz).date()
        # charts may look further ahead than the default reminder horizon
        horizon = max(self.cfg.occurrence_horizon_days,
                      ctx.settings.stats_horizon_days, ahead_days)
        for task in await self.store.list_tasks(ctx.user.id):
            await self.store.materialize_occurrences(
                task, today, horizon, ctx.settings.tz, back_days)

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
        if ctx.user.id in self._await_loc:
            # the place question owns the answer now: a typed place name is it,
            # anything else just asks the question again
            place = await self._find_place_by_name(ctx.user.id, _defang(text.strip()))
            if place:
                await self._location_chosen(ctx, place)
            else:
                await ctx.place_buttons(
                    "where_question",
                    [[(t("btn_loc_later", ctx.lang), "loc:later")]]
                    + self._back_only(ctx))
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
            if await self.store.add_place(ctx.user.id, _defang(text.strip())):
                await ctx.reply(t("place_added", ctx.lang, name=text))
            return
        await ctx.reply(t("unknown_cmd", ctx.lang))

    # ---------- text ----------

    async def _cmd_start(self, ctx: Ctx, _: str) -> None:
        ctx.suppress_menu = True
        await ctx.reply(t("greet", ctx.lang), self._menu_rows(ctx.lang))

    async def _cmd_help(self, ctx: Ctx, _: str) -> None:
        ctx.suppress_menu = True
        await ctx.reply(t("help_text", ctx.lang), self._menu_rows(ctx.lang))

    def _menu_rows(self, lang: str) -> ButtonRows:
        """Inline menu under /start and /help: one button per command."""
        return [
            [(t("menu_where", lang), "cmd:where"), (t("menu_tasks", lang), "cmd:tasks")],
            [(t("menu_task", lang), "cmd:task"), (t("menu_edit", lang), "cmd:edit")],
            [(t("menu_places", lang), "cmd:places"),
             (t("menu_stats", lang), "cmd:stats")],
            [(t("menu_settings", lang), "cmd:settings")],
        ]

    def _back_only(self, ctx: Ctx) -> ButtonRows:
        return [[(t("btn_back", ctx.lang), "menu:back")]]

    def _park_context(self, user_id: int) -> dict:
        """Move an open dialog aside while a place question is being asked."""
        parked: dict = {}
        wizard = self.wizards.pop(user_id, None)
        if wizard:
            parked["wizard"] = wizard
        pending = self._pending_inputs.pop(user_id, None)
        if pending:
            parked["pending"] = pending
        return parked

    def park_for_location(self, user_id: int) -> None:
        """The scheduler is about to ask "where are you?": pause the dialog."""
        self._await_loc[user_id] = self._park_context(user_id)

    async def _ask_location(self, ctx: Ctx, resume: dict | None = None) -> None:
        """A modal «Где вы сейчас?»: the dialog in progress waits, then resumes."""
        self._await_loc[ctx.user.id] = (
            self._park_context(ctx.user.id) if resume is None else resume)
        ctx.suppress_menu = True  # question + ⏭ Позже + ⬅️ Назад is the reply
        rows = [[(t("btn_loc_later", ctx.lang), "loc:later")]] + self._back_only(ctx)
        await ctx.place_buttons("where_question", rows)

    async def _location_chosen(self, ctx: Ctx, place) -> None:
        """The place that answers a parked question: set it, resume, digest."""
        parked = self._await_loc.pop(ctx.user.id, None) or {}
        await self.store.set_location(ctx.user.id, place.id)
        was_pending = ctx.settings.pending_reminder
        await self.store.update_settings(ctx.user.id, pending_reminder=False)
        await ctx.reply(t("where_set", ctx.lang, name=place.name))
        await self._replay(ctx, parked)
        if was_pending:
            # the digest comes last: its buttons are the ones to tap now, while
            # the resumed dialog carries on as typed input
            await self._digest_now(ctx.user.id, ctx.account)

    async def _replay(self, ctx: Ctx, parked: dict) -> None:
        """Put the interrupted dialog back and ask its question once more."""
        uid = ctx.user.id
        wizard = parked.get("wizard")
        if wizard:
            self.wizards[uid] = wizard
            if wizard.get("kind") == "task":
                await self._ask_step(ctx, wizard)
            elif wizard.get("kind") == "addplace":
                await self._cmd_places(ctx, "")
            return
        pending = parked.get("pending")
        if pending == "tasks":
            await self._cmd_tasks(ctx, "")
        elif pending:
            self._pending_inputs[uid] = pending

    async def _location_later(self, ctx: Ctx) -> None:
        """⏭ «Позже»: back to what was being typed, still without a place."""
        parked = self._await_loc.pop(ctx.user.id, None)
        if not parked:
            return
        ctx.suppress_menu = True
        await self._replay(ctx, parked)

    async def _cmd_where(self, ctx: Ctx, _: str) -> None:
        await self._ask_location(ctx)

    async def _cmd_tasks(self, ctx: Ctx, _: str) -> None:
        await self.ensure_materialized(ctx)
        loc = await self.store.get_location(ctx.user.id)
        if loc is None:
            # the list is what we came for: it is replayed once the place is in
            await self._ask_location(ctx, resume={"pending": "tasks"})
            return
        place, _ = loc
        await self._send_task_list(ctx, place)

    async def _cmd_task(self, ctx: Ctx, _: str) -> None:
        self.wizards[ctx.user.id] = {"kind": "task", "step": "title", "data": {}, "days": set()}
        await self._ask_step(ctx, self.wizards[ctx.user.id])

    async def _cmd_places(self, ctx: Ctx, arg: str) -> None:
        places = await self.store.list_places(ctx.user.id)
        if places:
            rows = [[(f"🗑 {p.name}", f"pdel:{p.id}")] for p in places]
            rows.append([(t("btn_back", ctx.lang), "menu:back")])
            ctx.suppress_menu = True  # list with delete buttons: menu follows on request
            await ctx.reply(
                t("places_list", ctx.lang, names=", ".join(p.name for p in places))
                + "\n" + t("no_places", ctx.lang),
                rows,
            )
        else:
            ctx.suppress_menu = True
            await ctx.reply(t("no_places", ctx.lang), self._back_only(ctx))
        self.wizards[ctx.user.id] = {"kind": "addplace"}

    async def _cb_place_delete(self, ctx: Ctx, place_id: int, confirm: bool) -> None:
        """Delete a place — but first warn about the tasks that go with it."""
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            await ctx.reply(t("place_removed", ctx.lang))
            return
        tasks = await self.store.list_tasks(ctx.user.id, place_id=place_id,
                                            active_only=False)
        if tasks and not confirm:
            ctx.suppress_menu = True
            await ctx.reply(t("place_delete_ask", ctx.lang, place=place.name,
                              n=len(tasks)),
                            [[(t("place_delete_yes", ctx.lang), f"pdelok:{place_id}")],
                             [(t("btn_back", ctx.lang), "cmd:places")]])
            return
        if await self.store.delete_place(ctx.user.id, place_id):
            note = t("place_removed", ctx.lang)
            if tasks:
                note += "\n" + t("place_removed_tasks", ctx.lang, n=len(tasks))
            await ctx.reply(note)

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
        now = datetime.now(UTC)
        start = (now - timedelta(days=days)) if days else datetime(2000, 1, 1, tzinfo=UTC)
        # charts look stats_horizon_days into the future, so planned tasks are
        # visible; text/CSV reports keep describing what already happened
        chart = fmt in ("chart", "график", "hours")
        # forward: exactly the user's horizon setting; back: the period, but
        # the map only ever reaches as far as real occurrences exist, and a
        # schedule is never planned before the date written inside it
        ahead = max(1, ctx.settings.stats_horizon_days) if chart else 1
        end = now + timedelta(days=ahead)
        if chart:
            await self.ensure_materialized(ctx, ahead, days or 365)
        occs = await self.store.occurrences_in_range(ctx.user.id, start, end)
        # planned (future) occurrences are kept on purpose: they show as
        # pending/«в плане» in reports and as blue insets on the heatmap
        if not occs:
            await ctx.reply(t("stats_no_data", ctx.lang))
            return
        if fmt in ("csv", "xlsx", "chart", "график", "hours"):
            await self._stats_file(ctx, occs, name, fmt, start, end)
            # the file/chart ends the answer: the menu follow-up must follow
            ctx.replied = True
            return
        rows = [[(t("btn_fmt_chart", ctx.lang), f"fmt:chart:{name}")]]
        if name == "week":
            # short periods also get the hour-level rendering
            rows.append([(t("btn_fmt_hours", ctx.lang), f"fmt:hours:{name}")])
        rows += [
            [(t("btn_fmt_csv", ctx.lang), f"fmt:csv:{name}"),
             (t("btn_fmt_xlsx", ctx.lang), f"fmt:xlsx:{name}")],
            [(t("btn_back", ctx.lang), "menu:back")],
        ]
        ctx.suppress_menu = True
        await ctx.reply(self._stats_text(ctx, occs, name), rows)

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
        ctx.suppress_menu = True
        await ctx.reply(t("lang_menu", ctx.lang),
                        [[("Русский", "lang:ru"), ("English", "lang:en")],
                         [(t("btn_back", ctx.lang), "menu:back")]])

    async def _set_lang(self, ctx: Ctx, lang: str) -> None:
        await self.store.set_language(ctx.user.id, lang)
        ctx.user.language = lang
        ctx.lang = lang
        if (ctx.user.id, ctx.account.id) in self._rem_msgs:
            # the settings screen itself switches language — no extra message
            await self._settings_status(ctx)
            return
        await ctx.reply(t("lang_set", lang))

    async def _cmd_settings(self, ctx: Ctx, _: str) -> None:
        await self._settings_status(ctx)

    def _stats_text(self, ctx: Ctx, past, period: str) -> str:
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
        lines = [t("stats_header", ctx.lang, period=t(f"period_{period}", ctx.lang))]
        for a in agg.values():
            base = a["total"] - a["skipped"]
            pct = round(100 * a["done"] / base) if base else 0
            lines.append(t("stats_row", ctx.lang, title=a["title"], done=a["done"],
                           missed=a["missed"], skipped=a["skipped"],
                           total=a["total"], percent=pct))
        return "\n".join(lines)

    async def _stats_file(self, ctx: Ctx, past, period: str, fmt: str,
                          start: datetime, end: datetime) -> None:
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
            elif fmt == "hours":
                # day rows × hour columns: what a short period really looked like
                grid = sagg.activity_by_hour(past, ctx.settings.tz)
                tzinfo = ZoneInfo(ctx.settings.tz)
                first = start.astimezone(tzinfo).date()
                last = end.astimezone(tzinfo).date()
                if grid:  # back only as far as the data goes, no empty run of
                    oldest = min(d for d, _ in grid)  # days before the first task
                    if oldest > first:
                        first = oldest
                if (last - first).days > 24:  # more rows than this stops being
                    first = last - timedelta(days=24)  # an hour-level picture
                path = os.path.join(tmp, "hours.png")
                if not charts.hourly_heatmap(
                        grid, ctx.lang, path, (first, last),
                        datetime.now(tzinfo).date()):
                    await ctx.reply(t("stats_no_data", ctx.lang))
                    return
                await ctx.messenger.send_image(
                    ctx.account, path, t("stats_hours_caption", ctx.lang))
            else:  # chart: GitHub-style activity map (daily bars retired)
                heat = os.path.join(tmp, "activity.png")
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
                    ctx.account, heat, t("stats_heat_caption", ctx.lang))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- wizard ----------

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
                [(t("btn_now", ctx.lang), "now:")],
                [(t("btn_today", ctx.lang), "dt:0"),
                 (t("btn_tomorrow", ctx.lang), "dt:1"),
                 (t("btn_in_2_days", ctx.lang), "dt:2")]] + back)
        elif step == "whentime":
            await ctx.reply(t("wizard_ask_time", ctx.lang), TM_PRESETS + back)
        elif step == "until":
            await ctx.reply(t("wizard_ask_until", ctx.lang), [
                [(t("btn_until_eod", ctx.lang), "dtl:eod")],
                [(t("btn_until_next_day", ctx.lang), "dtl:next18"),
                 (t("btn_until_week", ctx.lang), "dtl:week18")],
                [(t("btn_until_none", ctx.lang), "dtl:none")]] + back)
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
        elif step == "reminder":
            d = self._reminder_default(ctx, w)
            presets = [[(f"⏎ {d:%H:%M}", f"tm:{d:%H:%M}")]] + REMINDER_TMS[1:]
            await ctx.reply(t("wizard_ask_reminder", ctx.lang), presets + back)

    def _reminder_default(self, ctx: Ctx, w: dict) -> time:
        """A task stretched over time reminds by default when it begins."""
        start = w["data"].get("start")
        return start.astimezone(ctx.tz).time() if start else time(9, 0)

    async def _find_place_by_name(self, user_id: int, name: str):
        """Existing place with this name, comparing without regard to case."""
        if not name:
            return None
        for p in await self.store.list_places(user_id):
            if p.name.casefold() == name.casefold():
                return p
        return None

    async def _find_or_add_place(self, ctx: Ctx, name: str):
        """Place by name, any case; a name we have not seen creates a place."""
        found = await self._find_place_by_name(ctx.user.id, name)
        return found or await self.store.add_place(ctx.user.id, name)

    async def _place_picked(self, ctx: Ctx, w: dict, place) -> None:
        """A place was chosen by button or by name: continue the wizard."""
        w["data"]["place_id"] = place.id
        if w.get("only") == "place":
            # editor: moving the task to another place is the whole change
            await self._wizard_finish(ctx, w, w["data"].get("rt", time(9, 0)))
            return
        if w.get("pending_rt") is not None:
            # we only stopped to get a place back, the rest was already answered
            await self._wizard_finish(ctx, w, w.pop("pending_rt"))
            return
        self._goto(w, "schedule")
        await self._ask_step(ctx, w)

    async def _wizard_input(self, ctx: Ctx, w: dict, text: str) -> None:
        if w["kind"] == "addplace":
            wanted = _defang(text.strip())[:80]
            if await self._find_place_by_name(ctx.user.id, wanted):
                await ctx.reply(t("place_exists", ctx.lang))
                return
            place = await self.store.add_place(ctx.user.id, wanted)
            if place is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("no_places", ctx.lang)))
                return
            self.wizards.pop(ctx.user.id, None)
            await ctx.reply(t("place_added", ctx.lang, name=place.name))
            return
        if w["kind"] != "task":
            self.wizards.pop(ctx.user.id, None)
            return
        step = w["step"]
        if step == "title":
            w["data"]["title"] = _defang(text.strip())[:200]
            if w.get("only") == "title":
                # editor: renaming is the whole change
                await self._wizard_finish(ctx, w, w["data"].get("rt", time(9, 0)))
                return
            self._goto(w, "place")
            await self._ask_step(ctx, w)
        elif step == "place":
            # typing is a first-class answer here: with no places saved the bot
            # literally says "add one by text", and a new name is how the place
            # for this task gets born
            name = _defang(text.strip())[:80]
            place = await self._find_or_add_place(ctx, name)
            if place is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_place", ctx.lang)))
                return
            await self._place_picked(ctx, w, place)
        elif step == "n":
            if not text.lstrip("-").isdigit() or int(text) <= 0:
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=t("wizard_ask_n", ctx.lang)))
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
            await self._wizard_ask_reminder(ctx, w)
        elif step in self.WHEN_STEPS:
            # one reader for every moment the wizard asks about (start,
            # ready-by, first repeat, reminder time), then a "did I get that
            # right?" step whenever the parser had to fill anything in
            await self._when_input(ctx, w, text)

    WHEN_STEPS = ("when", "whentime", "until", "anchor", "reminder")
    WHEN_HINTS = {"when": "wizard_ask_when", "whentime": "wizard_ask_time",
                  "until": "wizard_ask_until",
                  "anchor": "wizard_ask_anchor_date",
                  "reminder": "wizard_ask_reminder"}

    async def _when_input(self, ctx: Ctx, w: dict, text: str) -> None:
        """Typed answer to any date/time question: read, resolve, confirm."""
        step = w["step"]
        if not text.strip():
            await self._when_default(ctx, w)      # Enter = the offered default
            return
        got = parse_when(text, datetime.now(ctx.tz).date())
        value, confirm, err = self._resolve_when(ctx, w, got)
        if err:
            await ctx.reply(t(err, ctx.lang,
                              hint=t(self.WHEN_HINTS[step], ctx.lang)))
            return
        if value is None:
            # only a day was given for a moment that also needs a clock
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

        The value type follows the step: a datetime for the start and the
        ready-by moment, a date for the first repeat, a time for the reminder.
        """
        step = w["step"]
        if got is None:
            return None, False, "bad"
        day_w, clock_w = as_day(got), as_clock(got)
        if step == "reminder":
            return ((clock_w.clock, clock_w.guessed, None) if clock_w
                    else (None, False, "wizard_bad_input"))
        if step == "anchor":
            return ((day_w.day, day_w.guessed, None) if day_w
                    else (None, False, "wizard_bad_input"))
        day = day_w.day if day_w else None
        clock = clock_w.clock if clock_w else None
        confirm = bool(got.guessed)
        if clock and not day:
            # a bare time still needs a day: the day work starts, or the next
            # such moment when the question is about starting
            if step in ("when", "whentime"):
                picked = w["data"].get("picked_date")
                if picked:            # the day was already chosen by button
                    day = picked
                else:
                    now = datetime.now(ctx.tz)
                    day = now.date()
                    if _at(day, clock, ctx.tz) <= now:
                        day += timedelta(days=1)
            else:
                start = w["data"].get("start")
                day = (start.astimezone(ctx.tz).date() if start
                       else datetime.now(ctx.tz).date())
            confirm = True
        if day and not clock and step == "until":
            clock = time(23, 59)      # a bare day means "by the end of it"
            confirm = True
        if not clock:
            return (None, False, None if day else "wizard_bad_input")
        value = _at(day, clock, ctx.tz)
        # nonsense is settled before the "did I read that right?" card
        if step in ("when", "whentime") and (
                value.date() < datetime.now(ctx.tz).date()):
            return None, False, "wizard_deadline_past"
        start = w["data"].get("start")
        if step == "until" and start and value <= start:
            return None, False, "wizard_until_before_start"
        return value, confirm, None

    async def _when_default(self, ctx: Ctx, w: dict) -> None:
        """An empty message: take the default the question advertises."""
        step = w["step"]
        if step == "until":
            await self._apply_when(ctx, w, step,
                                   self._end_of_day(w["data"]["start"], ctx.tz))
        elif step == "anchor":
            await self._apply_when(ctx, w, step, datetime.now(ctx.tz).date())
        elif step == "reminder":
            await self._wizard_finish(ctx, w, self._reminder_default(ctx, w))
        else:
            await ctx.reply(t("wizard_bad_input", ctx.lang,
                              hint=t(self.WHEN_HINTS[step], ctx.lang)))

    async def _apply_when(self, ctx: Ctx, w: dict, step: str, value) -> None:
        """Store a resolved moment and move the wizard on."""
        d = w["data"]
        if step in ("when", "whentime"):
            # guards the "2016 instead of 2026" typo: a once-task starting in
            # the past would materialize nothing and never show up
            if value.date() < datetime.now(ctx.tz).date():
                await ctx.reply(t("wizard_deadline_past", ctx.lang,
                                  hint=t(self.WHEN_HINTS[step], ctx.lang)))
                return
            d["start"] = value
            self._goto(w, "until")
            await self._ask_step(ctx, w)
        elif step == "until":
            start = d.get("start")
            if start and value <= start:
                await ctx.reply(t("wizard_until_before_start", ctx.lang,
                                  hint=t("wizard_ask_until", ctx.lang)))
                return
            d["deadline"] = value
            await self._wizard_ask_reminder(ctx, w)
        elif step == "anchor":
            d["anchor"] = value.isoformat()
            await self._wizard_ask_reminder(ctx, w)
        elif step == "reminder":
            await self._wizard_finish(ctx, w, value)

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

    @staticmethod
    def _end_of_day(when: datetime, tz: ZoneInfo) -> datetime:
        local = when.astimezone(tz)
        return datetime(local.year, local.month, local.day, 23, 59, tzinfo=tz)

    async def _wizard_ask_reminder(self, ctx: Ctx, w: dict) -> None:
        self._goto(w, "reminder")
        await self._ask_step(ctx, w)

    def _wizard_task_fields(self, w: dict) -> tuple[str, dict, datetime | None,
                                                     datetime | None]:
        """(schedule type, its data, ready-by moment, start) from wizard data."""
        d = w["data"]
        stype = d["stype"]
        sdata: dict = {}
        deadline = start_at = None
        if stype == "once":
            deadline, start_at = d.get("deadline"), d.get("start")
        elif stype == "every_n_days":
            sdata = {"n": d["n"], "anchor": d["anchor"]}
        elif stype == "weekdays":
            sdata = {"weekdays": sorted(w["days"])}
        elif stype == "monthly_day":
            sdata = {"day": d["day"]}
        return stype, sdata, deadline, start_at

    async def _wizard_finish(self, ctx: Ctx, w: dict, rt: time) -> None:
        if w.get("edit"):
            await self._wizard_update(ctx, w, rt)
            return
        d = w["data"]
        if await self.store.get_place(ctx.user.id, d.get("place_id") or 0) is None:
            # the place went away while this wizard was open (deleted from
            # another screen): ask again instead of writing a dangling FK
            d["place_id"] = None
            w["pending_rt"] = rt
            w["step"] = "place"
            await self._ask_step(ctx, w)
            return
        stype, sdata, deadline, start_at = self._wizard_task_fields(w)
        task = await self.store.create_task(
            ctx.user.id, d["place_id"], d["title"], stype, sdata, rt, deadline,
            start_at=start_at,
        )
        await self.store.materialize_occurrences(
            task, datetime.now(ctx.tz).date(), self.cfg.occurrence_horizon_days, ctx.settings.tz,
        )
        place = await self.store.get_place(ctx.user.id, d["place_id"])
        self.wizards.pop(ctx.user.id, None)
        await ctx.reply("\n".join([t("wizard_saved", ctx.lang, title=task.title,
                                     place=place.name if place else "?")]
                                  + await self._task_lines(ctx, task)),
                        self._task_open_rows(ctx, task))

    async def _wizard_update(self, ctx: Ctx, w: dict, rt: time) -> None:
        """Apply one edited field to an existing task and rebuild its future."""
        from taskshed.core.models import ScheduleType

        task = await self.store.get_task(ctx.user.id, int(w["edit"]))
        if task is None:
            self.wizards.pop(ctx.user.id, None)
            await ctx.reply(t("task_not_found", ctx.lang))
            return
        stype, sdata, deadline, start_at = self._wizard_task_fields(w)
        place_id = int(w["data"].get("place_id") or task.place_id)
        if await self.store.get_place(ctx.user.id, place_id) is None:
            # someone deleted the place while this edit dialog was open
            w["data"]["place_id"] = None
            w["pending_rt"] = rt
            w["step"] = "place"
            await self._ask_step(ctx, w)
            return
        task.title = w["data"].get("title", task.title)
        task.place_id = place_id
        task.schedule_type = ScheduleType(stype)
        task.schedule_data = sdata
        task.reminder_time = rt
        task.deadline, task.start_at = deadline, start_at
        await self.store.save_task(task)
        # open repeats are recomputed from the new schedule; finished history stays
        await self.store.drop_open_occurrences(task.id)
        await self.store.materialize_occurrences(
            task, datetime.now(ctx.tz).date(), self.cfg.occurrence_horizon_days,
            ctx.settings.tz)
        self.wizards.pop(ctx.user.id, None)
        ctx.suppress_menu = True  # the card below carries the edit buttons
        await ctx.reply("\n".join([t("edit_saved", ctx.lang, title=task.title)]
                                  + await self._task_lines(ctx, task)),
                        self._task_open_rows(ctx, task))

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

    # ---------- task editor ----------

    EDIT_TABS = ("future", "recurring", "past", "all")
    # field → wizard step it is edited with
    EDIT_STEPS = {"title": "title", "place": "place", "schedule": "schedule",
                  "when": "when", "reminder": "reminder", "weekdays": "days"}
    # fields that end right after the value (the rest keep the wizard chain)
    EDIT_SINGLE = ("title", "place", "reminder", "weekdays")

    def _edit_wizard(self, task, tab: str) -> dict:
        """Wizard state pre-filled from an existing task (edit mode)."""
        d: dict = {"title": task.title, "place_id": task.place_id,
                   "stype": str(task.schedule_type), "rt": task.reminder_time}
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

    # ---------- callbacks ----------

    async def _on_callback(self, ctx: Ctx, cb: str) -> None:
        action, _, arg = cb.partition(":")
        if action == "cmd":  # inline menu buttons from /start and /help
            handler = getattr(self, f"_cmd_{arg}", None)
            if handler:
                await handler(ctx, "")
        elif action == "place":
            await self._cb_place(ctx, int(arg))
        elif action == "loc" and arg == "later":
            await self._location_later(ctx)
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
                if w.get("only") == "weekdays":
                    # editor: picking the days is the whole change
                    await self._wizard_finish(ctx, w, w["data"].get("rt", time(9, 0)))
                    return
                await self._wizard_ask_reminder(ctx, w)
        elif action == "pdel":
            await self._cb_place_delete(ctx, int(arg), False)
        elif action == "pdelok":
            await self._cb_place_delete(ctx, int(arg), True)
        elif action == "edt":  # task editor: open one task's card
            tab, _, tid = arg.partition(":")
            if tid.isdigit():
                await self._cb_edit_open(ctx, tab or "future", int(tid))
        elif action == "etab":
            await self._edit_picker(ctx, arg if arg in self.EDIT_TABS else "future")
        elif action == "edf":  # edit one field of a task
            tid, _, field = arg.partition(":")
            if tid.isdigit():
                await self._cb_edit_field(ctx, int(tid), field)
        elif action == "eddel":
            tab, _, tid = arg.partition(":")
            if tid.isdigit():
                await self._cb_edit_delete(ctx, tab or "future", int(tid))
        elif action == "edyes":
            tab, _, tid = arg.partition(":")
            if tid.isdigit():
                await self._cb_edit_delete_yes(ctx, tab or "future", int(tid))
        elif action == "done":
            await self._cb_done(ctx, int(arg))
        elif action == "wip":
            await self._cb_wip(ctx, int(arg))
        elif action == "snooze":
            await self._cb_snooze(ctx, int(arg))
        elif action == "skip":
            await self._cb_skip(ctx, int(arg))
        elif action == "dtl":
            await self._cb_until(ctx, arg)
        elif action == "snz":
            await self._cb_snooze_set(ctx, arg)
        elif action == "lang":
            await self._set_lang(ctx, normalize(arg))
        elif action == "rem":
            await self._cb_rem(ctx, arg)
        elif action == "per":
            await self._stats_show(ctx, arg, None)
        elif action == "fmt":
            fmt, _, p = arg.partition(":")
            await self._stats_show(ctx, p, fmt)
        elif action == "dt":
            await self._cb_date(ctx, int(arg))
        elif action == "now":
            await self._cb_now(ctx)
        elif action == "cfm":
            await self._cb_cfm(ctx, arg == "yes")
        elif action == "tm":
            await self._cb_time(ctx, arg)
        elif action == "wz" and arg == "back":
            # ⬅️ Назад inside the task wizard: redo the previous question
            w = self.wizards.get(ctx.user.id)
            if not w or w.get("kind") != "task" or not w.get("hist"):
                return
            w["step"] = w["hist"].pop()
            w.pop("pending_rt", None)  # walking back drops an interrupted finish
            await self._ask_step(ctx, w)
        elif action == "wz" and arg == "cancel":
            # ✖ Отмена on the first wizard step (typing /отмена also works)
            w = self.wizards.get(ctx.user.id)
            if w and w.get("kind") == "task":
                self.wizards.pop(ctx.user.id, None)
                if w.get("edit"):
                    # in the editor cancelling means "back to that task"
                    await self._cb_edit_open(ctx,
                                             self._edit_tab.get(ctx.user.id, "future"),
                                             int(w["edit"]))
                    return
                await ctx.reply(t("wizard_cancelled", ctx.lang))
        elif action == "menu" and arg == "back":
            # submenu screens offer ⬅️ Назад: return to the main command menu
            self._rem_msgs.pop((ctx.user.id, ctx.account.id), None)
            self._pending_inputs.pop(ctx.user.id, None)  # drop awaited inputs
            if self.wizards.get(ctx.user.id, {}).get("kind") == "addplace":
                self.wizards.pop(ctx.user.id, None)
            parked = self._await_loc.pop(ctx.user.id, None)
            if parked:
                # ⬅️ from a place question: drop the question, keep the dialog
                if "wizard" in parked:
                    self.wizards[ctx.user.id] = parked["wizard"]
                if "pending" in parked:
                    self._pending_inputs[ctx.user.id] = parked["pending"]
            ctx.suppress_menu = True
            await ctx.reply(t("menu_ask_more", ctx.lang),
                            self._menu_rows(ctx.lang))

    async def _cb_now(self, ctx: Ctx) -> None:
        """«▶ Сейчас»: the task starts at this very minute, no date questions."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("kind") != "task" or w.get("step") not in ("when", "whentime"):
            return
        w["data"]["start"] = datetime.now(ctx.tz).replace(second=0, microsecond=0)
        self._goto(w, "until")
        await self._ask_step(ctx, w)

    async def _cb_date(self, ctx: Ctx, offset: int) -> None:
        """Date picked via buttons (today/tomorrow/…) in the task wizard."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") not in ("when", "anchor"):
            return
        d = datetime.now(ctx.tz).date() + timedelta(days=offset)
        if w["step"] == "anchor":
            w["data"]["anchor"] = d.isoformat()
            await self._wizard_ask_reminder(ctx, w)
            return
        w["data"]["picked_date"] = d
        self._goto(w, "whentime")
        await self._ask_step(ctx, w)

    async def _cb_time(self, ctx: Ctx, arg: str) -> None:
        """Time picked via buttons (start-time step or reminder-time step)."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") not in ("whentime", "reminder"):
            return
        tval = read_clock(arg)
        if tval is None:
            return
        if w["step"] == "reminder":
            await self._wizard_finish(ctx, w, tval)
            return
        # a tapped button needs no "did I read that right?" step
        await self._apply_when(ctx, w, "whentime",
                               _at(w["data"]["picked_date"], tval, ctx.tz))

    async def _cb_until(self, ctx: Ctx, arg: str) -> None:
        """Ready-by presets for a task stretched over time."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") != "until":
            return
        local = w["data"]["start"].astimezone(ctx.tz)
        if arg == "none":
            # a plain reminder, not an event stretched over time
            w["data"]["deadline"] = None
            await self._wizard_ask_reminder(ctx, w)
            return
        if arg == "eod":
            until = datetime(local.year, local.month, local.day, 23, 59,
                             tzinfo=ctx.tz)
        elif arg in ("next18", "week18"):
            day = (local + timedelta(days=1 if arg == "next18" else 7)).date()
            until = datetime(day.year, day.month, day.day, 18, 0, tzinfo=ctx.tz)
        else:
            return
        w["data"]["deadline"] = until
        await self._wizard_ask_reminder(ctx, w)

    async def _cb_place(self, ctx: Ctx, place_id: int) -> None:
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            return
        if ctx.user.id in self._await_loc:
            # this tap answers the parked place question, not the dialog that
            # was interrupted by it
            await self._location_chosen(ctx, place)
            return
        w = self.wizards.get(ctx.user.id)
        if w and w.get("kind") == "task" and w.get("step") == "place":
            await self._place_picked(ctx, w, place)
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

    async def _cb_done(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.mark_done(occ_id, ctx.ev.platform):
            await ctx.reply(t("msg_done", ctx.lang, title=got[1].title))

    async def _cb_wip(self, ctx: Ctx, occ_id: int) -> None:
        # «В работе»: the user started it. The row keeps its deadline but is
        # never punished as missed while the work is in progress
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.start_work(occ_id):
            await ctx.reply(t("msg_wip", ctx.lang, title=got[1].title))

    async def _cb_skip(self, ctx: Ctx, occ_id: int) -> None:
        # "not today" without marking it done: closes this occurrence without
        # the missed penalty, the schedule just continues with the next one
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.skip(occ_id):
            await ctx.reply(t("msg_skipped", ctx.lang, title=got[1].title))

    async def _cb_snooze(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id or got[0].status == "done":
            return
        # ask *how long*, rather than silently snoozing a fixed hour
        ctx.suppress_menu = True
        await ctx.reply(t("snooze_ask", ctx.lang), [
            [(t("btn_snz_15", ctx.lang), f"snz:{occ_id}:15"),
             (t("btn_snz_60", ctx.lang), f"snz:{occ_id}:60")],
            [(t("btn_snz_240", ctx.lang), f"snz:{occ_id}:240"),
             (t("btn_snz_1440", ctx.lang), f"snz:{occ_id}:1440")],
        ])

    async def _cb_snooze_set(self, ctx: Ctx, arg: str) -> None:
        occ_id_s, _, mins = arg.partition(":")
        if not (occ_id_s.isdigit() and mins.isdigit()):
            return
        got = await self.store.get_occurrence(int(occ_id_s))
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.snooze(int(occ_id_s), int(mins)):
            await ctx.reply(t("msg_snoozed", ctx.lang, minutes=int(mins),
                              title=got[1].title))

    # ---------- task list / digest ----------

    async def _send_task_list(self, ctx: Ctx, place: PlaceDC) -> None:
        today = datetime.now(ctx.tz).date()
        occs = await self.store.occurrences_today(ctx.user.id, today, ctx.settings.tz,
                                                  place_id=place.id)
        if not occs:
            await ctx.reply(t("tasks_empty", ctx.lang, place=place.name))
            return
        text, rows = self._render_tasks(ctx.lang, ctx.tz, place.name, occs)
        await ctx.reply(text, rows)

    def _render_tasks(self, lang: str, tz: ZoneInfo, place_name: str,
                      occs: list[tuple]) -> tuple[str, ButtonRows]:
        """(text, buttons) of a task list — shared by /задачи and digests."""
        lines = [t("tasks_header", lang, place=place_name)]
        rows: ButtonRows = []
        for i, (occ, task) in enumerate(occs, 1):
            due = occ.due_at.astimezone(tz)
            if occ.start_at is not None:
                # work stretched over time: show the whole window
                start = occ.start_at.astimezone(tz)
                line = t("task_row_range", lang, n=i, title=task.title,
                         start=start.strftime("%d.%m %H:%M"),
                         due=due.strftime("%d.%m %H:%M"))
            else:
                line = t("task_row", lang, n=i, title=task.title,
                         due=due.strftime("%d.%m %H:%M"))
            if occ.status == "missed":
                line += t("task_overdue_mark", lang)
            elif occ.status == "in_progress":
                line += t("task_started_mark", lang)
            lines.append(line)
            row = [(t("btn_done", lang), f"done:{occ.id}")]
            if occ.status == "in_progress":
                row.append((t("btn_skip", lang), f"skip:{occ.id}"))
            else:
                row += [(t("btn_wip", lang), f"wip:{occ.id}"),
                        (t("btn_snooze", lang), f"snooze:{occ.id}"),
                        (t("btn_skip", lang), f"skip:{occ.id}")]
            rows.append(row)
        return "\n".join(lines), rows

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
        return self._render_tasks(lang, tz, place.name, occs)

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
                await self._strip_keyboards(messenger, acc, user_id)
                if buttons:
                    mid = await messenger.send_buttons(acc, text, buttons)
                    self._track_kb(user_id, acc, mid, text)
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
                await self._strip_keyboards(messenger, acc, user_id)
                mid = await messenger.send_buttons(acc, text, buttons)
                self._track_kb(user_id, acc, mid, text)

    # ---------- reminder settings ----------

    @staticmethod
    def _platform_label(name: str) -> str:
        return {"telegram": "Telegram", "vk": "VK"}.get(name, name.title())

    async def _settings_status(self, ctx: Ctx) -> None:
        """One screen with everything this user can configure."""
        st = ctx.settings
        accounts = await self.store.get_accounts(ctx.user.id)
        linked = {str(a.platform) for a in accounts}
        platforms = ", ".join(
            f"{self._platform_label(p)} {'✓' if p in linked else '✗'}"
            for p in (str(e) for e in Platform))
        status = t("settings_status", ctx.lang,
                   on=t("rem_on" if st.reminders_on else "rem_off", ctx.lang),
                   mode=self._rem_mode_label(st, ctx.lang),
                   quiet=f"{st.quiet_start:%H:%M}–{st.quiet_end:%H:%M}",
                   ttl=st.location_ttl_hours, horizon=st.stats_horizon_days,
                   tz=st.tz, platforms=platforms,
                   language="Русский" if ctx.lang == "ru" else "English")
        rows = [
            [(t("btn_rem_toggle", ctx.lang), "rem:toggle")],
            [(t("btn_rem_when", ctx.lang), "rem:when")],
            [(t("btn_rem_quiet", ctx.lang), "rem:quiet"),
             (t("btn_rem_ttl", ctx.lang), "rem:ttl")],
            [(t("btn_rem_tz", ctx.lang), "rem:tz"),
             (t("btn_rem_horizon", ctx.lang), "rem:horizon")],
            [(t("btn_rem_lang", ctx.lang), "rem:lang"),
             (t("btn_rem_link", ctx.lang), "rem:link")],
        ]
        for acc in accounts:
            if acc.id != ctx.account.id:
                rows.append([(t("btn_unlink", ctx.lang,
                                 platform=self._platform_label(str(acc.platform))),
                              f"rem:unlink:{acc.id}")])
        rows.append([(t("btn_back", ctx.lang), "menu:back")])
        ctx.suppress_menu = True  # this screen *is* a menu: ⬅️ Назад closes it
        key = (ctx.user.id, ctx.account.id)
        mid = self._rem_msgs.get(key)
        # other keyboards of this chat go stale; this one is re-rendered below
        await self._strip_keyboards(ctx.messenger, ctx.account, ctx.user.id,
                                    keep=mid or "")
        if mid and await ctx.messenger.edit_buttons(ctx.account, mid, status, rows):
            self._track_kb(ctx.user.id, ctx.account, mid, status)
            ctx.replied = True
            return
        new_id = await ctx.messenger.send_buttons(ctx.account, status, rows)
        ctx.replied = True
        if new_id:
            self._rem_msgs[key] = new_id
            self._track_kb(ctx.user.id, ctx.account, new_id, status)

    def _rem_mode_label(self, st, lang: str) -> str:
        if st.remind_mode == "times":
            return t("rem_mode_times", lang, times=" ".join(st.remind_times))
        return t("rem_mode_interval", lang, minutes=st.remind_interval_min)

    async def _cb_rem(self, ctx: Ctx, arg: str) -> None:
        if arg == "toggle":
            st = await self.store.update_settings(
                ctx.user.id, reminders_on=not ctx.settings.reminders_on,
            )
            ctx.settings = st
            await self._settings_status(ctx)
            return
        if arg == "when":
            # one entry point for both modes: pick the mode, then the value
            ctx.suppress_menu = True
            await ctx.reply(
                t("rem_when_ask", ctx.lang,
                  mode=self._rem_mode_label(ctx.settings, ctx.lang)), [
                    [(t("rem_when_times_btn", ctx.lang), "rem:times")],
                    [(t("rem_when_interval_btn", ctx.lang), "rem:interval")],
                    [(t("btn_back", ctx.lang), "rem:back")],
                ])
            return
        if arg == "back":
            # leave the sub-question without leaving the settings screen
            self._pending_inputs.pop(ctx.user.id, None)  # drop awaited value
            await self._settings_status(ctx)
            return
        if arg == "lang":
            ctx.suppress_menu = True
            await ctx.reply(t("lang_menu", ctx.lang),
                            [[("Русский", "lang:ru"), ("English", "lang:en")],
                             [(t("btn_back", ctx.lang), "rem:back")]])
            return
        if arg == "link":
            code = await self.store.create_link_code(ctx.user.id, ctx.ev.platform)
            ctx.suppress_menu = True
            await ctx.reply(t("link_code_msg", ctx.lang, code=code),
                            [[(t("btn_back", ctx.lang), "rem:back")]])
            return
        if arg.startswith("unlink:"):
            await self._cb_unlink_acc(ctx, arg.split(":", 1)[1])
            return
        if arg in ("times", "interval", "quiet", "ttl", "tz", "horizon"):
            self._pending_inputs[ctx.user.id] = f"reminput:{arg}"
            ctx.suppress_menu = True  # the next message is the value itself
            await ctx.reply(t(f"rem_ask_{arg}", ctx.lang),
                            [[(t("btn_back", ctx.lang), "rem:back")]])

    async def _cb_unlink_acc(self, ctx: Ctx, acc_id: str) -> None:
        """Unlinking from the settings screen: the row named the account."""
        if not acc_id.isdigit():
            return
        accounts = await self.store.get_accounts(ctx.user.id)
        target = next((a for a in accounts if a.id == int(acc_id)), None)
        if target is None or len(accounts) < 2:
            return  # never leave a user with no account
        await self.store.detach_account(target.id, ctx.user.id)
        await ctx.reply(t("unlink_done", ctx.lang))
        await self._settings_status(ctx)

    async def _reminput(self, ctx: Ctx, field: str, text: str) -> None:
        values: dict = {}
        if field == "times":
            parts = text.split()
            parsed = [read_clock(p) for p in parts]
            if parts and all(p is not None for p in parsed):
                values = {"remind_mode": "times",
                          "remind_times": [p.strftime("%H:%M") for p in parsed]}
            else:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_times", ctx.lang)))
                return
        elif field == "interval":
            if not text.isdigit() or int(text) < 5:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_interval", ctx.lang)))
                return
            values = {"remind_mode": "interval", "remind_interval_min": int(text)}
        elif field == "quiet":
            parts = text.split()
            parsed = [read_clock(p) for p in parts]
            if len(parts) != 2 or any(p is None for p in parsed):
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_quiet", ctx.lang)))
                return
            values = {"quiet_start": parsed[0], "quiet_end": parsed[1]}
        elif field == "ttl":
            if not text.isdigit() or not 1 <= int(text) <= 168:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_ttl", ctx.lang)))
                return
            values = {"location_ttl_hours": int(text)}
        elif field == "tz":
            try:
                ZoneInfo(text.strip())
            except (KeyError, ValueError):
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_tz", ctx.lang)))
                return
            values = {"tz": text.strip()}
        elif field == "horizon":
            if not text.isdigit() or not 1 <= int(text) <= 90:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("rem_ask_horizon", ctx.lang)))
                return
            values = {"stats_horizon_days": int(text)}
        st = await self.store.update_settings(ctx.user.id, **values)
        ctx.settings = st
        # value changed → the settings screen updates in place, no new copy
        await self._settings_status(ctx)
