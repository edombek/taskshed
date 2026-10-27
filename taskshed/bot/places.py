"""Places: one live list message, in-place delete confirmations."""

from taskshed.bot.context import Ctx
from taskshed.i18n import t
from taskshed.messaging.base import ButtonRows


class PlacesMixin:

    async def _cmd_places(self, ctx: Ctx, arg: str) -> None:
        """The places screen: typed names add, 🗑 buttons delete, ✔️ closes."""
        text, rows = await self._places_view(ctx)
        mid = await self._edit_places_msg(ctx, text, rows)
        if mid is None:
            ctx.suppress_menu = True  # the screen owns its own buttons
            mid = await ctx.reply(text, rows)
            self._pl_msgs[(ctx.user.id, ctx.account.id)] = mid
        self.wizards[ctx.user.id] = {"kind": "addplace"}

    async def _places_view(self, ctx: Ctx) -> tuple[str, ButtonRows]:
        places = await self.store.list_places(ctx.user.id)
        if not places:
            return t("no_places", ctx.lang), [
                [(t("btn_list_close", ctx.lang), "pl:close")]]
        rows = [[(f"🗑 {p.name}", f"pdel:{p.id}")] for p in places]
        rows.append([(t("btn_list_close", ctx.lang), "pl:close")])
        text = (t("places_list", ctx.lang,
                  names=", ".join(p.name for p in places))
                + "\n" + t("no_places", ctx.lang))
        return text, rows

    async def _edit_places_msg(self, ctx: Ctx, text: str, rows: ButtonRows) -> str | None:
        """Redraw the live places message; None when there is none to redraw."""
        mid = self._pl_msgs.get((ctx.user.id, ctx.account.id))
        if not mid:
            return None
        try:
            ok = await ctx.messenger.edit_buttons(ctx.account, mid, text, rows)
        except Exception:
            ok = False
        if not ok:
            self._pl_msgs.pop((ctx.user.id, ctx.account.id), None)
            return None
        live = self._kb_msgs.get((ctx.user.id, ctx.account.id))
        if live is not None and mid in live:
            live[mid] = text
        ctx.replied = True
        ctx.suppress_menu = True
        return mid

    async def _cb_place_delete(self, ctx: Ctx, place_id: int,
                               confirm: bool | None) -> None:
        """🗑 tap asks in place (no second message); ✖️ puts the list back."""
        if confirm is None:  # ✖️ Не удалять
            if not await self._redraw_list(ctx):
                await self._cmd_places(ctx, "")
            return
        place = await self.store.get_place(ctx.user.id, place_id)
        if place is None:
            await ctx.reply(t("place_removed", ctx.lang))
            return
        tasks = await self.store.list_tasks(ctx.user.id, place_id=place_id,
                                            active_only=False)
        if not confirm:
            ask = (t("place_delete_ask", ctx.lang, place=place.name, n=len(tasks))
                   if tasks else
                   t("place_delete_ask_plain", ctx.lang, place=place.name))
            rows = [[(t("place_delete_yes", ctx.lang), f"pdelok:{place_id}"),
                     (t("btn_pl_no", ctx.lang), f"pdno:{place_id}")]]
            if not await self._edit_places_msg(ctx, ask, rows):
                ctx.suppress_menu = True
                await ctx.reply(ask, rows)
            return
        if await self.store.delete_place(ctx.user.id, place_id):
            note = t("place_removed", ctx.lang)
            if tasks:
                note += "\n" + t("place_removed_tasks", ctx.lang, n=len(tasks))
            if not await self._note_on_places(ctx, note):
                await ctx.reply(note)

    async def _redraw_list(self, ctx: Ctx) -> bool:
        text, rows = await self._places_view(ctx)
        return await self._edit_places_msg(ctx, text, rows) is not None

    async def _note_on_places(self, ctx: Ctx, note: str) -> bool:
        """Put a note on the live places screen, keeping it open."""
        if not self._pl_msgs.get((ctx.user.id, ctx.account.id)):
            return False
        text, rows = await self._places_view(ctx)
        return await self._edit_places_msg(ctx, note + "\n\n" + text, rows) is not None

    async def _cb_places_close(self, ctx: Ctx) -> None:
        """✔️ Готово: leave the screen — the keyboard goes away, menu back."""
        mid = self._pl_msgs.pop((ctx.user.id, ctx.account.id), None)
        self.wizards.pop(ctx.user.id, None)
        if not mid:
            # an old copy of the screen (restart, stripped bookkeeping):
            # the button must still lead somewhere, never silence
            ctx.suppress_menu = True
            await ctx.reply(t("menu_ask_more", ctx.lang),
                            self._menu_rows(ctx.lang))
            return
        live = self._kb_msgs.get((ctx.user.id, ctx.account.id), {})
        try:
            await ctx.messenger.edit_buttons(ctx.account, mid, live.get(mid, ""), [])
        except Exception:
            pass
        live.pop(mid, None)
        ctx.replied = True
        ctx.suppress_menu = True

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
