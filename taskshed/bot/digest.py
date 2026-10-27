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
            if not await self._refresh_task_msg(ctx):
                await ctx.reply(t("msg_done", ctx.lang, title=got[1].title))

    async def _cb_wip(self, ctx: Ctx, occ_id: int) -> None:
        # «В работе»: the user started it. The row keeps its deadline but is
        # never punished as missed while the work is in progress
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.start_work(occ_id):
            if not await self._refresh_task_msg(ctx):
                await ctx.reply(t("msg_wip", ctx.lang, title=got[1].title))

    async def _cb_skip(self, ctx: Ctx, occ_id: int) -> None:
        # "not today" without marking it done: closes this occurrence without
        # the missed penalty, the schedule just continues with the next one
        got = await self.store.get_occurrence(occ_id)
        if not got or got[1].user_id != ctx.user.id:
            return
        if await self.store.skip(occ_id):
            if not await self._refresh_task_msg(ctx):
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
        text, rows = self._render_tasks(ctx.lang, ctx.tz, place.name, occs, close=True)
        mid = await ctx.reply(text, rows)
        self._track_tl(ctx.user.id, ctx.account, mid, place.name,
                       [o.id for o, _ in occs])

    def _render_tasks(self, lang: str, tz: ZoneInfo, place_name: str,
                      occs: list[tuple], close: bool = False) -> tuple[str, ButtonRows]:
        """(text, buttons) of a task list — shared by /задачи and digests.

        Closed rows (done/skipped) keep their line with a mark but lose the
        buttons: they stay visible so an edited digest shows what happened.
        """
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
            if occ.status == "done":
                line += t("task_done_mark", lang)
            elif occ.status == "skipped":
                line += t("task_skipped_mark", lang)
            elif occ.status == "missed":
                line += t("task_overdue_mark", lang)
            elif occ.status == "in_progress":
                line += t("task_started_mark", lang)
            lines.append(line)
            if occ.status in ("done", "skipped"):
                continue
            row = [(t("btn_done", lang), f"done:{occ.id}")]
            if occ.status == "in_progress":
                row.append((t("btn_skip", lang), f"skip:{occ.id}"))
            else:
                row += [(t("btn_wip", lang), f"wip:{occ.id}"),
                        (t("btn_snooze", lang), f"snooze:{occ.id}"),
                        (t("btn_skip", lang), f"skip:{occ.id}")]
            rows.append(row)
        if close and rows:
            rows.append([(t("btn_list_close", lang), "tl:close")])
        return "\n".join(lines), rows

    async def _refresh_task_msg(self, ctx: Ctx) -> bool:
        """Redraw the task-list/digest message the tap came from.

        A handled row loses its buttons, the rest stay tappable, and no new
        message or menu is added: several tasks from one reminder can be
        worked through inside a single message. False when the message is
        unknown (restart, forwarded copy) — the caller falls back to a reply.
        """
        reg = self._tl_msgs.get((ctx.user.id, ctx.account.id), {})
        mid = ctx.ev.message_id
        entry = reg.get(mid) if mid else None
        if entry is None:
            return False
        place_name, occ_ids = entry
        occs = [got for oid in occ_ids if (got := await self.store.get_occurrence(oid))]
        text, rows = self._render_tasks(ctx.lang, ctx.tz, place_name, occs, close=True)
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
                # nothing open left: close the message ourselves
                live.pop(mid, None)
                reg.pop(mid, None)
        ctx.replied = True
        ctx.suppress_menu = True
        return True

    async def _cb_tl_close(self, ctx: Ctx) -> None:
        """✔️ Готово: the list is dismissed — buttons off, no new message."""
        reg = self._tl_msgs.get((ctx.user.id, ctx.account.id), {})
        mid = ctx.ev.message_id
        if not mid or mid not in reg:
            return
        live = self._kb_msgs.get((ctx.user.id, ctx.account.id), {})
        try:
            await ctx.messenger.edit_buttons(ctx.account, mid, live.get(mid, ""), [])
        except Exception:
            pass
        live.pop(mid, None)
        reg.pop(mid, None)
        ctx.replied = True
        ctx.suppress_menu = True

    async def build_digest(self, user_id: int, settings: UserSettings, place: PlaceDC):
        """(text, buttons, meta) for today's tasks at `place`, or None.

        `meta` = (place name, occurrence ids): what the digest message shows,
        so a tap on it can re-render that very message.
        """
        tz = ZoneInfo(settings.tz)
        today = datetime.now(tz).date()
        occs = await self.store.occurrences_today(user_id, today, settings.tz, place_id=place.id)
        if not occs:
            return None
        lang = "ru"
        user = await self.store.get_user(user_id)
        if user:
            lang = user.language
        text, rows = self._render_tasks(lang, tz, place.name, occs, close=True)
        return text, rows, (place.name, [o.id for o, _ in occs])

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
                          buttons: ButtonRows | None = None,
                          meta: tuple[str, list[int]] | None = None) -> None:
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
                    if meta:
                        self._track_tl(user_id, acc, mid, *meta)
                else:
                    await messenger.send_text(acc, text)

    async def _fanout(self, user_id: int, settings: UserSettings,
                      origin: Account | None, text: str, buttons: ButtonRows,
                      meta: tuple[str, list[int]] | None = None) -> None:
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
                if meta:
                    self._track_tl(user_id, acc, mid, *meta)
