"""BotApp: dispatches RawEvents to commands / wizards / buttons.

Platform-independent: Telegram and VK adapters only translate their wire
formats into RawEvent + implement IMessenger.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.config import BotCfg
from taskshed.core.models import Account, Platform, User, UserSettings
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

_TIME_RE = re.compile(r"^(\d{1,2})\s*[:.,\-\s]\s*(\d{1,2})$")
_DATE_FULL_RE = re.compile(r"^(\d{1,4})[ ./-]+(\d{1,2})[ ./-]+(\d{2,4})$")
_DATE_SHORT_RE = re.compile(r"^(\d{1,2})[ ./-]+(\d{1,2})$")

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


def _parse_hhmm(s: str) -> time | None:
    m = _TIME_RE.match(s.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return time(h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def _parse_date(s: str, today: date) -> date | None:
    """Flexibly parse a typed date: 2026-11-15, 15.11.2026, 15/11/26,
    '15 11 2026', 15.11, and the words today/tomorrow (ru+en). Day comes
    first unless the year does; a year-less date rolls forward."""
    s = s.strip().lower()
    if s in ("сегодня", "today"):
        return today
    if s in ("завтра", "tomorrow"):
        return today + timedelta(days=1)
    m = _DATE_FULL_RE.match(s)
    short = False
    if m:
        a, b, c = (int(g) for g in m.groups())
        if a > 31:  # YYYY-MM-DD
            y, mo, d = a, b, c
        else:  # DD.MM.YYYY / DD.MM.YY (day-first, the RU habit)
            d, mo, y = a, b, (2000 + c if c < 100 else c)
    else:
        m = _DATE_SHORT_RE.match(s)
        if not m:
            return None
        d, mo, y, short = int(m.group(1)), int(m.group(2)), today.year, True
    try:
        got = date(y, mo, d)
    except ValueError:
        return None
    if short and got < today:
        try:
            got = date(y + 1, mo, d)
        except ValueError:
            return None
    return got


def _split_date_time(s: str) -> tuple[str, str | None]:
    """Split typed 'DATE TIME'; the time may itself use dots ('14.30')."""
    parts = s.strip().split()
    if len(parts) >= 2 and _parse_hhmm(parts[-1]) is not None:
        return " ".join(parts[:-1]), parts[-1]
    return s.strip(), None


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
            await self.reply(t("no_places", self.lang), extra_rows or None)
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
        # Not while a wizard step is in progress, not when the answer itself
        # carries input buttons (stats pickers, start/help).
        if (ctx.replied and not ctx.suppress_menu
                and not self.wizards.get(user.id)):
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

    async def ensure_materialized(self, ctx: Ctx) -> None:
        today = datetime.now(ctx.tz).date()
        # charts may look further ahead than the default reminder horizon
        horizon = max(self.cfg.occurrence_horizon_days, ctx.settings.stats_horizon_days)
        for task in await self.store.list_tasks(ctx.user.id):
            await self.store.materialize_occurrences(task, today, horizon, ctx.settings.tz)

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
            [(t("menu_task", lang), "cmd:task"), (t("menu_places", lang), "cmd:places")],
            [(t("menu_settings", lang), "cmd:settings"),
             (t("menu_stats", lang), "cmd:stats")],
        ]

    def _back_only(self, ctx: Ctx) -> ButtonRows:
        return [[(t("btn_back", ctx.lang), "menu:back")]]

    async def _cmd_where(self, ctx: Ctx, _: str) -> None:
        self._pending_inputs[ctx.user.id] = "where"
        ctx.suppress_menu = True  # question + ⬅️ Назад is the whole reply
        await ctx.place_buttons("where_question", self._back_only(ctx))

    async def _cmd_tasks(self, ctx: Ctx, _: str) -> None:
        await self.ensure_materialized(ctx)
        loc = await self.store.get_location(ctx.user.id)
        if loc is None:
            self._pending_inputs[ctx.user.id] = "tasks"
            ctx.suppress_menu = True
            await ctx.place_buttons("where_question", self._back_only(ctx))
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
        future = max(1, ctx.settings.stats_horizon_days) if chart else 1
        end = now + timedelta(days=future)
        if chart:
            await self.ensure_materialized(ctx)
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
                if (last - first).days > 40:  # keep the picture readable
                    first = last - timedelta(days=40)
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
                [(t("btn_today", ctx.lang), "dt:0"),
                 (t("btn_tomorrow", ctx.lang), "dt:1"),
                 (t("btn_in_2_days", ctx.lang), "dt:2")]] + back)
        elif step == "whentime":
            await ctx.reply(t("wizard_ask_time", ctx.lang), TM_PRESETS + back)
        elif step == "until":
            await ctx.reply(t("wizard_ask_until", ctx.lang), [
                [(t("btn_until_eod", ctx.lang), "dtl:eod")],
                [(t("btn_until_next_day", ctx.lang), "dtl:next18"),
                 (t("btn_until_week", ctx.lang), "dtl:week18")]] + back)
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

    async def _wizard_input(self, ctx: Ctx, w: dict, text: str) -> None:
        if w["kind"] == "addplace":
            if await self.store.add_place(ctx.user.id, _defang(text.strip())):
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
            w["data"]["title"] = _defang(text.strip())[:200]
            self._goto(w, "place")
            await self._ask_step(ctx, w)
        elif step == "n":
            if not text.lstrip("-").isdigit() or int(text) <= 0:
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=t("wizard_ask_n", ctx.lang)))
                return
            w["data"]["n"] = int(text)
            self._goto(w, "anchor")
            await self._ask_step(ctx, w)
        elif step == "anchor":
            today = datetime.now(ctx.tz).date()
            anchor = _parse_date(text, today) if text.strip() else today
            if anchor is None:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_anchor_date", ctx.lang)))
                return
            w["data"]["anchor"] = anchor.isoformat()
            await self._wizard_ask_reminder(ctx, w)
        elif step == "monthday":
            if not text.isdigit() or not 1 <= int(text) <= 31:
                await ctx.reply(t("wizard_bad_input", ctx.lang,
                                  hint=t("wizard_ask_month_day", ctx.lang)))
                return
            w["data"]["day"] = int(text)
            await self._wizard_ask_reminder(ctx, w)
        elif step == "when":
            hint = t("wizard_ask_when", ctx.lang)
            dpart, tsrc = _split_date_time(text)
            d = _parse_date(dpart, datetime.now(ctx.tz).date())
            tval = _parse_hhmm(tsrc) if tsrc else None
            if d is None or (tsrc is not None and tval is None):
                await ctx.reply(t("wizard_bad_input", ctx.lang, hint=hint))
                return
            if tval is None:
                # date only: remember it, ask the time the usual way
                w["data"]["picked_date"] = d
                self._goto(w, "whentime")
                await self._ask_step(ctx, w)
                return
            start = datetime(d.year, d.month, d.day, tval.hour, tval.minute,
                             tzinfo=ctx.tz)
            # guards the "2016 instead of 2026" typo: a once-task starting
            # before today would materialize nothing and never show up
            if start.date() < datetime.now(ctx.tz).date():
                await ctx.reply(t("wizard_deadline_past", ctx.lang, hint=hint))
                return
            w["data"]["start"] = start
            self._goto(w, "until")
            await self._ask_step(ctx, w)
        elif step == "whentime":
            await self._set_start(ctx, w, w["data"]["picked_date"],
                                  _parse_hhmm(text))
        elif step == "until":
            await self._set_until(ctx, w, text)
        elif step == "reminder":
            rt = self._reminder_default(ctx, w)
            if text.strip():
                parsed = _parse_hhmm(text)
                if parsed is None:
                    await ctx.reply(t("wizard_bad_input", ctx.lang,
                                      hint=t("wizard_ask_reminder", ctx.lang)))
                    return
                rt = parsed
            await self._wizard_finish(ctx, w, rt)

    async def _set_start(self, ctx: Ctx, w: dict, d: date,
                         tval: time | None) -> None:
        """Shared tail of start entry (button or typed HH:MM)."""
        if tval is None:
            await ctx.reply(t("wizard_bad_input", ctx.lang,
                              hint=t("wizard_ask_time", ctx.lang)))
            return
        start = datetime(d.year, d.month, d.day, tval.hour, tval.minute,
                         tzinfo=ctx.tz)
        if start.date() < datetime.now(ctx.tz).date():
            await ctx.reply(t("wizard_deadline_past", ctx.lang,
                              hint=t("wizard_ask_time", ctx.lang)))
            return
        w["data"]["start"] = start
        self._goto(w, "until")
        await self._ask_step(ctx, w)

    async def _set_until(self, ctx: Ctx, w: dict, text: str) -> None:
        """Ready-by moment; Enter means "end of the day it starts on"."""
        hint = t("wizard_ask_until", ctx.lang)
        start = w["data"]["start"]
        if not text.strip():
            w["data"]["deadline"] = self._end_of_day(start, ctx.tz)
            await self._wizard_ask_reminder(ctx, w)
            return
        dpart, tsrc = _split_date_time(text)
        d = _parse_date(dpart, datetime.now(ctx.tz).date())
        tval = _parse_hhmm(tsrc) if tsrc else None
        if d is None or (tsrc is not None and tval is None):
            await ctx.reply(t("wizard_bad_input", ctx.lang, hint=hint))
            return
        until = datetime(d.year, d.month, d.day,
                         tval.hour if tval else 23, tval.minute if tval else 59,
                         tzinfo=ctx.tz)
        if until <= start:
            await ctx.reply(t("wizard_until_before_start", ctx.lang, hint=hint))
            return
        w["data"]["deadline"] = until
        await self._wizard_ask_reminder(ctx, w)

    @staticmethod
    def _end_of_day(when: datetime, tz: ZoneInfo) -> datetime:
        local = when.astimezone(tz)
        return datetime(local.year, local.month, local.day, 23, 59, tzinfo=tz)

    async def _wizard_ask_reminder(self, ctx: Ctx, w: dict) -> None:
        self._goto(w, "reminder")
        await self._ask_step(ctx, w)

    async def _wizard_finish(self, ctx: Ctx, w: dict, rt: time) -> None:
        d = w["data"]
        stype = d["stype"]
        sdata: dict = {}
        deadline = None
        start_at = None
        if stype == "once":
            deadline = d["deadline"]
            start_at = d["start"]
        elif stype == "every_n_days":
            sdata = {"n": d["n"], "anchor": d["anchor"]}
        elif stype == "weekdays":
            sdata = {"weekdays": sorted(w["days"])}
        elif stype == "monthly_day":
            sdata = {"day": d["day"]}
        task = await self.store.create_task(
            ctx.user.id, d["place_id"], d["title"], stype, sdata, rt, deadline,
            start_at=start_at,
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
        if action == "cmd":  # inline menu buttons from /start and /help
            handler = getattr(self, f"_cmd_{arg}", None)
            if handler:
                await handler(ctx, "")
        elif action == "place":
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
        elif action == "tm":
            await self._cb_time(ctx, arg)
        elif action == "wz" and arg == "back":
            # ⬅️ Назад inside the task wizard: redo the previous question
            w = self.wizards.get(ctx.user.id)
            if not w or w.get("kind") != "task" or not w.get("hist"):
                return
            w["step"] = w["hist"].pop()
            await self._ask_step(ctx, w)
        elif action == "wz" and arg == "cancel":
            # ✖ Отмена on the first wizard step (typing /отмена also works)
            w = self.wizards.get(ctx.user.id)
            if w and w.get("kind") == "task":
                self.wizards.pop(ctx.user.id, None)
                await ctx.reply(t("wizard_cancelled", ctx.lang))
        elif action == "menu" and arg == "back":
            # submenu screens offer ⬅️ Назад: return to the main command menu
            self._rem_msgs.pop((ctx.user.id, ctx.account.id), None)
            self._pending_inputs.pop(ctx.user.id, None)  # drop awaited inputs
            if self.wizards.get(ctx.user.id, {}).get("kind") == "addplace":
                self.wizards.pop(ctx.user.id, None)
            ctx.suppress_menu = True
            await ctx.reply(t("menu_ask_more", ctx.lang),
                            self._menu_rows(ctx.lang))

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
        if w["step"] == "reminder":
            rt = _parse_hhmm(arg)
            if rt is not None:
                await self._wizard_finish(ctx, w, rt)
            return
        await self._set_start(ctx, w, w["data"]["picked_date"], _parse_hhmm(arg))

    async def _cb_until(self, ctx: Ctx, arg: str) -> None:
        """Ready-by presets for a task stretched over time."""
        w = self.wizards.get(ctx.user.id)
        if not w or w.get("step") != "until":
            return
        local = w["data"]["start"].astimezone(ctx.tz)
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

    async def _set_deadline(self, ctx: Ctx, w: dict, d: date,
                            tval: time | None) -> None:
        """Shared tail of deadline entry (button or typed HH:MM)."""
        if tval is None:
            await ctx.reply(t("wizard_bad_input", ctx.lang,
                              hint=t("wizard_ask_time", ctx.lang)))
            return
        deadline = datetime(d.year, d.month, d.day, tval.hour, tval.minute,
                            tzinfo=ctx.tz)
        if deadline.date() < datetime.now(ctx.tz).date():
            await ctx.reply(t("wizard_deadline_past", ctx.lang,
                              hint=t("wizard_ask_time", ctx.lang)))
            return
        w["data"]["deadline"] = deadline
        await self._wizard_ask_reminder(ctx, w)

    async def _cb_place(self, ctx: Ctx, place_id: int) -> None:
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            return
        w = self.wizards.get(ctx.user.id)
        if w and w.get("kind") == "task" and w.get("step") == "place":
            w["data"]["place_id"] = place.id
            self._goto(w, "schedule")
            await self._ask_step(ctx, w)
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
            parsed = [_parse_hhmm(p) for p in parts]
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
            parsed = [_parse_hhmm(p) for p in parts]
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
