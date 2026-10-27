"""Task lists, the reminder digest, and the action buttons on both."""

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
