"""The modal «Где вы сейчас?» and the location buttons behind it."""

from taskshed.bot.context import Ctx
from taskshed.i18n import t


class LocationMixin:

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
            await self.notify_now(ctx.user.id, ctx.account)

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
        ctx.suppress_menu = True
        if not parked:
            # nothing to resume: either a bare /гдея, or a stale copy of a
            # fanned-out question already answered in another chat — either
            # way hand back the main menu, never silence
            await ctx.reply(t("menu_ask_more", ctx.lang),
                            self._menu_rows(ctx.lang))
            return
        await self._replay(ctx, parked)

    async def _cmd_where(self, ctx: Ctx, _: str) -> None:
        await self._ask_location(ctx)


    async def _cb_place(self, ctx: Ctx, place_id: int) -> None:
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            # the place was deleted while this keyboard was alive
            await ctx.reply(t("place_removed", ctx.lang))
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
            await self._cmd_tasks(ctx, "")
        else:
            await ctx.reply(t("where_set", ctx.lang, name=place.name))
        if was_pending:
            await self.notify_now(ctx.user.id, ctx.account)
