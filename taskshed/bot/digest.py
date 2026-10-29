"""One message per task, the notices that carry them, the buttons inside.

A task message owns its occurrence: tapping a button re-renders that very
message (buttons off when the task is closed) — no confirmation messages, no
menu follow-up after every tap. A notice is built for the user's current
place: its tasks get a message each with buttons, tasks of other places come
as one brief line-up, and a «Готово» button under the last message ends the
round and hands the main menu back.
"""

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from taskshed.bot.context import Ctx
from taskshed.core.models import Account, Platform, UserSettings
from taskshed.core.models import Place as PlaceDC
from taskshed.core.scheduling import in_notice_window
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows, IMessenger

log = logging.getLogger(__name__)

NOTICE_OTHERS_LIMIT = 12


class DigestMixin:

    # ---------- /задачи ----------

    async def _cmd_tasks(self, ctx: Ctx, _: str) -> None:
        await self.ensure_materialized(ctx)
        loc = await self.store.get_location(ctx.user.id)
        if loc is None:
            # the list is what we came for: it is replayed once the place is in
            await self._ask_location(ctx, resume={"pending": "tasks"})
            return
        place, _ = loc
        today = datetime.now(ctx.tz).date()
        rows = await self.store.occurrences_today(ctx.user.id, today)
        mine = [(o, task) for o, task in rows if task.place_id == place.id]
        others = [(o, task) for o, task in rows if task.place_id != place.id]
        if not mine and not others:
            # nothing here today: still offer the walk out — the menu follows
            # this reply, and 🌍 checks the other places right from here
            await ctx.reply(t("tasks_empty", ctx.lang, place=place.name),
                            [[(t("btn_all_tasks", ctx.lang), "tas:all")]])
            return
        ctx.suppress_menu = True  # the notice ends with its own «Готово»
        await self._send_notice(ctx.messenger, ctx.account, ctx.user.id, ctx.lang,
                                ctx.tz, t("tasks_header", ctx.lang, place=place.name),
                                mine, others)

    # ---------- task buttons ----------

    def _end_notice_round(self, ctx: Ctx) -> None:
        """A fallback reply is about to disarm every keyboard of this chat —
        the footer's «Готово» included. So the notice round ends here: forget
        its bookkeeping and let the follow-up menu be the exit.
        """
        key = (ctx.user.id, ctx.account.id)
        self._fin_msgs.pop(key, None)
        self._tl_msgs.pop(key, None)

    async def _cb_done(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.mark_done(occ_id, ctx.ev.platform):
            if not await self._apply_task_msg(ctx, occ_id):
                self._end_notice_round(ctx)
                await ctx.reply(t("msg_done", ctx.lang, title=got[1].title))

    async def _cb_wip(self, ctx: Ctx, occ_id: int) -> None:
        # «В работе»: the user started it. The row keeps its deadline but is
        # never punished as missed while the work is in progress
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.start_work(occ_id):
            if not await self._apply_task_msg(ctx, occ_id):
                self._end_notice_round(ctx)
                await ctx.reply(t("msg_wip", ctx.lang, title=got[1].title))

    async def _cb_skip(self, ctx: Ctx, occ_id: int) -> None:
        # "not today" without marking it done: closes this occurrence without
        # the missed penalty, the schedule just continues with the next one
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.skip(occ_id):
            if not await self._apply_task_msg(ctx, occ_id):
                self._end_notice_round(ctx)
                await ctx.reply(t("msg_skipped", ctx.lang, title=got[1].title))

    async def _cb_snooze(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id or got[0].status == "done":
            return
        # ask *how long* — inside the task's own message whenever we own it
        ask = t("snooze_ask", ctx.lang) + "\n"
        if not await self._edit_task_msg(
                ctx, occ_id, ask + self._task_line(ctx.lang, ctx.tz, got[0], got[1]),
                self._snooze_rows(ctx.lang, occ_id)):
            ctx.suppress_menu = True
            await ctx.reply(t("snooze_ask", ctx.lang),
                            self._snooze_rows(ctx.lang, occ_id))

    async def _cb_snooze_back(self, ctx: Ctx, occ_id: int) -> None:
        if not await self._apply_task_msg(ctx, occ_id):
            ctx.suppress_menu = True
            await ctx.reply(t("snooze_ask", ctx.lang),
                            self._snooze_rows(ctx.lang, occ_id))

    async def _cb_snooze_set(self, ctx: Ctx, arg: str) -> None:
        occ_id_s, _, mins = arg.partition(":")
        if not (occ_id_s.isdigit() and mins.isdigit()):
            return
        got = await self.store.get_occurrence(int(occ_id_s))
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.snooze(int(occ_id_s), int(mins)):
            line = self._task_line(ctx.lang, ctx.tz, got[0], got[1]) + "\n" + t(
                "task_snoozed_note", ctx.lang, minutes=int(mins))
            if not await self._edit_task_msg(
                    ctx, int(occ_id_s), line, self._task_rows(ctx.lang, got[0])):
                self._end_notice_round(ctx)
                await ctx.reply(t("msg_snoozed", ctx.lang, minutes=int(mins),
                                  title=got[1].title))

    async def _cb_notification_finish(self, ctx: Ctx) -> None:
        """«Готово» under a notice: stop editing statuses, main menu back.

        ctx.reply disarms every keyboard of this chat on the way, so the task
        messages of this notice lose their buttons right here.
        """
        self._fin_msgs.pop((ctx.user.id, ctx.account.id), None)
        ctx.suppress_menu = True  # the menu below *is* the follow-up
        await ctx.reply(t("menu_ask_more", ctx.lang), self._menu_rows(ctx.lang))

    async def _cb_all_tasks(self, ctx: Ctx) -> None:
        """🌍 «Все задачи»: open rows of every place, grouped, no buttons.

        The notice shows the current place in full and the others in brief;
        this is the answer to "then show me everything" — every open repeat
        from today to the horizon, each place its own block.
        """
        await self.ensure_materialized(ctx)
        tz = ctx.tz
        start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
        horizon = max(self.cfg.occurrence_horizon_days,
                      ctx.settings.stats_horizon_days)
        occs = await self.store.occurrences_in_range(
            ctx.user.id, start, datetime.now(UTC) + timedelta(days=horizon))
        open_rows = [(o, tk) for o, tk in occs
                     if o.status in ("pending", "in_progress", "missed")]
        ctx.suppress_menu = True  # the screen owns its «Готово» and «Все задачи»
        footer = self._notice_footer(ctx.lang)
        if not open_rows:
            await ctx.reply(t("tasks_all_empty", ctx.lang), footer)
            return
        names = {p.id: p.name for p in await self.store.list_places(ctx.user.id)}
        groups: dict[int, list] = {}
        for occ, tk in open_rows:
            groups.setdefault(tk.place_id, []).append((occ, tk))
        lines = [t("tasks_all_header", ctx.lang)]
        for pid in sorted(groups, key=lambda k: names.get(k, "?").lower()):
            rows = sorted(groups[pid], key=lambda r: r[0].due_at)
            lines.append(t("tasks_all_place", ctx.lang, place=names.get(pid, "?")))
            for occ, tk in rows[:NOTICE_OTHERS_LIMIT]:
                lines.append(self._task_line(ctx.lang, tz, occ, tk))
            if len(rows) > NOTICE_OTHERS_LIMIT:
                lines.append(t("notif_more", ctx.lang,
                               n=len(rows) - NOTICE_OTHERS_LIMIT))
        await ctx.reply("\n".join(lines), footer)

    # ---------- one task, one message ----------

    def _task_line(self, lang: str, tz: ZoneInfo, occ, task) -> str:
        due = occ.due_at.astimezone(tz)
        if occ.start_at is not None:
            # work stretched over time: show the whole window
            start = occ.start_at.astimezone(tz)
            line = t("task_msg_range", lang, title=task.title,
                     start=start.strftime("%d.%m %H:%M"),
                     due=due.strftime("%d.%m %H:%M"))
        else:
            line = t("task_msg", lang, title=task.title,
                     due=due.strftime("%d.%m %H:%M"))
        if occ.status == "done":
            line += t("task_done_mark", lang)
        elif occ.status == "skipped":
            line += t("task_skipped_mark", lang)
        elif occ.status == "missed":
            line += t("task_overdue_mark", lang)
        elif occ.status == "in_progress":
            line += t("task_started_mark", lang)
        return line

    def _task_rows(self, lang: str, occ) -> ButtonRows:
        """Buttons of one task message; empty once the task is closed."""
        if occ.status in ("done", "skipped"):
            return []
        if occ.status == "in_progress":
            return [[(t("btn_done", lang), f"done:{occ.id}"),
                     (t("btn_skip", lang), f"skip:{occ.id}")]]
        return [[(t("btn_done", lang), f"done:{occ.id}"),
                 (t("btn_skip", lang), f"skip:{occ.id}")],
                [(t("btn_snooze", lang), f"snooze:{occ.id}"),
                 (t("btn_wip", lang), f"wip:{occ.id}")]]

    def _snooze_rows(self, lang: str, occ_id: int) -> ButtonRows:
        return [[(t("btn_snz_15", lang), f"snz:{occ_id}:15"),
                 (t("btn_snz_60", lang), f"snz:{occ_id}:60")],
                [(t("btn_snz_240", lang), f"snz:{occ_id}:240"),
                 (t("btn_snz_1440", lang), f"snz:{occ_id}:1440")],
                [(t("btn_back", lang), f"snzb:{occ_id}")]]

    async def _edit_task_msg(self, ctx: Ctx, occ_id: int, text: str,
                             rows: ButtonRows) -> bool:
        """Re-render the live task message of this occurrence in place.

        False when the chat has no live message for it (restart, forwarded
        copy, already stripped) — the caller falls back to a reply + menu.
        """
        reg = self._tl_msgs.get((ctx.user.id, ctx.account.id), {})
        mid = ctx.ev.message_id
        if not mid or reg.get(mid) != occ_id:
            return False
        try:
            ok = await ctx.messenger.edit_buttons(ctx.account, mid, text, rows)
        except Exception:
            ok = False
        if not ok:
            return False
        live = self._kb_msgs.get((ctx.user.id, ctx.account.id))
        if live is not None and mid in live:
            if rows:
                live[mid] = text
            else:
                live.pop(mid, None)  # closed: the message keeps no keyboard
                reg.pop(mid, None)
        ctx.replied = True
        ctx.suppress_menu = True
        return True

    async def _apply_task_msg(self, ctx: Ctx, occ_id: int) -> bool:
        got = await self.store.get_occurrence(occ_id)
        if not got:
            return False
        return await self._edit_task_msg(
            ctx, occ_id, self._task_line(ctx.lang, ctx.tz, got[0], got[1]),
            self._task_rows(ctx.lang, got[0]))

    # ---------- notices: this place in full, other places in brief ----------

    async def _accounts_for(self, settings: UserSettings,
                            origin: Account | None = None) -> list[Account]:
        """Accounts this user's notices are allowed to reach.

        "primary" is the platform the user last talked to the bot on
        (`last_seen`); a platform name pins notices to it — falling back
        to where the user actually is when that platform is unlinked.
        """
        accounts = await self.store.get_accounts(settings.user_id)
        mode = settings.notify_platforms
        if mode == "all":
            return accounts
        if mode in (str(p) for p in Platform):
            pinned = [a for a in accounts if str(a.platform) == mode]
            if pinned:
                return pinned
        if origin is not None:
            return [a for a in accounts if a.platform == origin.platform]
        return sorted(accounts, key=lambda a: a.last_seen or a.linked_at)[-1:]

    def _others_block(self, lang: str, tz: ZoneInfo, others, names: dict) -> str:
        """Other places' tasks: short lines, no buttons — they are not here."""
        lines = [t("notif_others", lang)]
        for occ, task in others[:NOTICE_OTHERS_LIMIT]:
            lines.append(t("notif_other_row", lang,
                           place=names.get(task.place_id, "?"), title=task.title,
                           due=occ.due_at.astimezone(tz).strftime("%d.%m %H:%M")))
        if len(others) > NOTICE_OTHERS_LIMIT:
            lines.append(t("notif_more", lang, n=len(others) - NOTICE_OTHERS_LIMIT))
        return "\n".join(lines)

    async def _send_notice(self, messenger: IMessenger, acc: Account, user_id: int,
                           lang: str, tz: ZoneInfo, header: str, mine, others) -> None:
        """Header + one message per task of this place + brief others + «Готово»."""
        await self._strip_keyboards(messenger, acc, user_id)
        await messenger.send_text(acc, header)
        for occ, task in mine:
            text = self._task_line(lang, tz, occ, task)
            rows = self._task_rows(lang, occ)
            if not rows:
                await messenger.send_text(acc, text)
                continue
            mid = await messenger.send_buttons(acc, text, rows)
            self._track_kb(user_id, acc, mid, text)
            self._track_tl(user_id, acc, mid, occ.id)
        if others:
            names = {p.id: p.name for p in await self.store.list_places(user_id)}
            await messenger.send_text(acc, self._others_block(lang, tz, others, names))
        # the round ends when the user says so: «Готово» hands the menu back,
        # «Все задачи» widens the view to every place — sent even when this
        # place had nothing to tap, otherwise the screen would have no exit
        text = t("notif_footer", lang)
        mid = await messenger.send_buttons(acc, text, self._notice_footer(lang))
        self._track_kb(user_id, acc, mid, text)
        if mid:
            self._fin_msgs[(user_id, acc.id)] = mid

    def _notice_footer(self, lang: str) -> ButtonRows:
        return [[(t("btn_finish", lang), "fin:"),
                 (t("btn_all_tasks", lang), "tas:all")]]

    async def send_notification(self, user_id: int, settings: UserSettings,
                                place: PlaceDC, rows, *, morning: bool = False,
                                origin: Account | None = None) -> bool:
        """One notice: tasks of `place` as separate messages with buttons, the
        rest of the places as short lines. False when there is nothing to say."""
        mine = [(o, task) for o, task in rows if task.place_id == place.id]
        others = [(o, task) for o, task in rows if task.place_id != place.id]
        if not mine and not others:
            return False
        lang = "ru"
        user = await self.store.get_user(user_id)
        if user:
            lang = user.language
        tz = ZoneInfo(settings.tz)
        header = t("notif_header_morning" if morning else "notif_header_repeat",
                   lang, place=place.name)
        # what a live check needs in the log: which set went where
        log.info("notice for user %s at «%s»: %d here, %d elsewhere (morning=%s)",
                 user_id, place.name, len(mine), len(others), morning)
        for acc in await self._accounts_for(settings, origin):
            messenger = self.messengers.get(acc.platform)
            if not messenger:
                continue
            await self._send_notice(messenger, acc, user_id, lang, tz, header,
                                    mine, others)
        return True

    async def notify_now(self, user_id: int, origin: Account | None = None,
                         morning: bool | None = None) -> bool:
        """Send the notice that was waiting for a place, right now.

        The place is known; the set is re-read through the user's current
        timezone, so a stale row planned in another zone is not what we send.
        """
        settings = await self.store.get_settings(user_id)
        loc = await self.store.get_location(user_id)
        if loc is None:
            return False
        now = datetime.now(UTC)
        tz = ZoneInfo(settings.tz)
        today = now.astimezone(tz).date()
        if morning is None:
            morning = not (settings.last_morning_at and
                           settings.last_morning_at.astimezone(tz).date() >= today)
        todays = await self.store.rows_to_announce(user_id, today)
        rows = todays if morning else [(o, task) for o, task in todays
                                       if in_notice_window(o, now)]
        if not rows:
            return False
        if not await self.send_notification(user_id, settings, loc[0], rows,
                                            morning=morning, origin=origin):
            return False
        values = {"last_notified_at": now}
        if morning:
            values["last_morning_at"] = now
        await self.store.update_settings(user_id, **values)
        return True

    async def notify_user(self, user_id: int, text: str,
                          buttons: ButtonRows | None = None) -> None:
        """Send to every linked account (or the most recently active one)."""
        settings = await self.store.get_settings(user_id)
        for acc in await self._accounts_for(settings):
            messenger = self.messengers.get(acc.platform)
            if messenger:
                await self._strip_keyboards(messenger, acc, user_id)
                if buttons:
                    mid = await messenger.send_buttons(acc, text, buttons)
                    self._track_kb(user_id, acc, mid, text)
                else:
                    await messenger.send_text(acc, text)
