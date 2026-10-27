"""Places: the list screen, deletion with a warning, lookup by name."""

from taskshed.bot.context import Ctx
from taskshed.i18n import t


class PlacesMixin:

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
