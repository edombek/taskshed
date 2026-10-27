"""One message per task, the reminder digest, and the buttons inside both.

A task message owns its occurrence: tapping a button re-renders that very
message (buttons off when the task is closed) — no confirmation messages,
no menu follow-up after every tap.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from taskshed.bot.context import Ctx
from taskshed.core.models import Account, UserSettings
from taskshed.core.models import Place as PlaceDC
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows


class DigestMixin:

    async def _cmd_tasks(self, ctx: Ctx, _: str) -> None:
        await self.ensure_materialized(ctx)
        loc = await self.store.get_location(ctx.user.id)
        if loc is None:
            # the list is what we came for: it is replayed once the place is in
            await self._ask_location(ctx, resume={"pending": "tasks"})
            return
        place, _ = loc
        await self._send_task_list(ctx, place)

    # ---------- task buttons ----------

    async def _cb_done(self, ctx: Ctx, occ_id: int) -> None:
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.mark_done(occ_id, ctx.ev.platform):
            if not await self._apply_task_msg(ctx, occ_id):
                await ctx.reply(t("msg_done", ctx.lang, title=got[1].title))

    async def _cb_wip(self, ctx: Ctx, occ_id: int) -> None:
        # «В работе»: the user started it. The row keeps its deadline but is
        # never punished as missed while the work is in progress
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.start_work(occ_id):
            if not await self._apply_task_msg(ctx, occ_id):
                await ctx.reply(t("msg_wip", ctx.lang, title=got[1].title))

    async def _cb_skip(self, ctx: Ctx, occ_id: int) -> None:
        # "not today" without marking it done: closes this occurrence without
        # the missed penalty, the schedule just continues with the next one
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.skip(occ_id):
            if not await self._apply_task_msg(ctx, occ_id):
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
                await ctx.reply(t("msg_snoozed", ctx.lang, minutes=int(mins),
                                  title=got[1].title))

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
                 (t("btn_wip", lang), f"wip:{occ.id}")],
                [(t("btn_snooze", lang), f"snooze:{occ.id}"),
                 (t("btn_skip", lang), f"skip:{occ.id}")]]

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

    # ---------- sending ----------

    async def _send_task_list(self, ctx: Ctx, place: PlaceDC) -> None:
        today = datetime.now(ctx.tz).date()
        occs = await self.store.occurrences_today(ctx.user.id, today, ctx.settings.tz,
                                                  place_id=place.id)
        if not occs:
            await ctx.reply(t("tasks_empty", ctx.lang, place=place.name))
            return
        await ctx.reply(t("tasks_header", ctx.lang, place=place.name))
        for occ, task in occs:
            await self._send_task_msg(ctx, ctx.lang, ctx.tz, occ, task)

    async def _send_task_msg(self, ctx: Ctx, lang: str, tz: ZoneInfo,
                             occ, task) -> None:
        text = self._task_line(lang, tz, occ, task)
        rows = self._task_rows(lang, occ)
        if not rows:
            await ctx.messenger.send_text(ctx.account, text)
            return
        mid = await ctx.messenger.send_buttons(ctx.account, text, rows)
        self._track_kb(ctx.user.id, ctx.account, mid, text)
        self._track_tl(ctx.user.id, ctx.account, mid, occ.id)

    async def build_digest(self, user_id: int, settings: UserSettings, place: PlaceDC):
        """(header, [(occ_id, text, rows)]) of today's tasks, or None for silence.

        The header names the place; every task is a message of its own.
        """
        tz = ZoneInfo(settings.tz)
        today = datetime.now(tz).date()
        occs = await self.store.occurrences_today(user_id, today, settings.tz,
                                                  place_id=place.id)
        if not occs:
            return None
        lang = "ru"
        user = await self.store.get_user(user_id)
        if user:
            lang = user.language
        items = [(occ.id, self._task_line(lang, tz, occ, task),
                  self._task_rows(lang, occ)) for occ, task in occs]
        return t("tasks_header", lang, place=place.name), items

    async def send_digest(self, user_id: int, settings: UserSettings,
                          place: PlaceDC, origin: Account | None = None) -> bool:
        """Header + one message per task into every account the settings allow."""
        digest = await self.build_digest(user_id, settings, place)
        if digest is None:
            return False
        header, items = digest
        accounts = await self.store.get_accounts(user_id)
        if settings.notify_platforms == "primary":
            if origin is not None:
                accounts = [a for a in accounts if a.platform == origin.platform]
            else:
                accounts = sorted(accounts,
                                  key=lambda a: a.last_seen or a.linked_at)[-1:]
        for acc in accounts:
            messenger = self.messengers.get(acc.platform)
            if not messenger:
                continue
            await self._strip_keyboards(messenger, acc, user_id)
            await messenger.send_text(acc, header)
            for occ_id, text, rows in items:
                if not rows:
                    await messenger.send_text(acc, text)
                    continue
                mid = await messenger.send_buttons(acc, text, rows)
                self._track_kb(user_id, acc, mid, text)
                self._track_tl(user_id, acc, mid, occ_id)
        return True

    async def _digest_now(self, user_id: int, origin: Account) -> bool:
        settings = await self.store.get_settings(user_id)
        loc = await self.store.get_location(user_id)
        if loc is None:
            return False
        return await self.send_digest(user_id, settings, loc[0], origin=origin)

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
