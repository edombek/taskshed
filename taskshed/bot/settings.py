"""The per-user settings screen and the values it awaits by text."""

from datetime import datetime
from zoneinfo import ZoneInfo

from taskshed.bot.context import Ctx
from taskshed.bot.timeparse import read_clock
from taskshed.core.models import Platform
from taskshed.i18n import normalize, t


class SettingsMixin:

    async def _cmd_lang(self, ctx: Ctx, arg: str) -> None:
        lang = normalize(arg) if arg in ("ru", "en", "русский", "russian") else None
        if lang:
            await self._set_lang(ctx, lang)
            return
        ctx.suppress_menu = True
        await ctx.reply(t("lang_menu", ctx.lang),
                        [[("Русский", "lang:ru"), ("English", "lang:en")]]
                        + self._finish_row(ctx))

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
                   morning=f"{st.morning_time:%H:%M}", nag=st.nag_interval_min,
                   quiet=f"{st.quiet_start:%H:%M}–{st.quiet_end:%H:%M}",
                   ttl=st.location_ttl_hours, horizon=st.stats_horizon_days,
                   tz=st.tz, platforms=platforms,
                   notify=self._where_label(ctx, st.notify_platforms),
                   language="Русский" if ctx.lang == "ru" else "English")
        rows = [
            [(t("btn_rem_toggle", ctx.lang), "rem:toggle")],
            [(t("btn_rem_morning", ctx.lang), "rem:morning"),
             (t("btn_rem_nag", ctx.lang), "rem:nag")],
            [(t("btn_rem_quiet", ctx.lang), "rem:quiet"),
             (t("btn_rem_ttl", ctx.lang), "rem:ttl")],
            [(t("btn_rem_tz", ctx.lang), "rem:tz"),
             (t("btn_rem_horizon", ctx.lang), "rem:horizon")],
            [(t("btn_rem_lang", ctx.lang), "rem:lang"),
             (t("btn_rem_where", ctx.lang), "rem:where")],
            [(t("btn_rem_link", ctx.lang), "rem:link")],
        ]
        for acc in accounts:
            if acc.id != ctx.account.id:
                rows.append([(t("btn_unlink", ctx.lang,
                                 platform=self._platform_label(str(acc.platform))),
                              f"rem:unlink:{acc.id}")])
        rows += self._finish_row(ctx)
        ctx.suppress_menu = True  # this screen *is* a menu: ✅ Готово closes it
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

    async def _cb_rem(self, ctx: Ctx, arg: str) -> None:
        if arg == "toggle":
            st = await self.store.update_settings(
                ctx.user.id, reminders_on=not ctx.settings.reminders_on,
            )
            ctx.settings = st
            await self._settings_status(ctx)
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
        if arg == "where":
            await self._rem_where(ctx)
            return
        if arg.startswith("where:"):
            mode = arg.split(":", 1)[1]
            if mode in ("all", "primary", "telegram", "vk"):
                ctx.settings = await self.store.update_settings(
                    ctx.user.id, notify_platforms=mode)
            await self._rem_where(ctx)
            return
        if arg.startswith("unlink:"):
            await self._cb_unlink_acc(ctx, arg.split(":", 1)[1])
            return
        if arg in ("morning", "nag", "quiet", "ttl", "tz", "horizon"):
            self._pending_inputs[ctx.user.id] = f"reminput:{arg}"
            ctx.suppress_menu = True  # the next message is the value itself
            await ctx.reply(t(f"rem_ask_{arg}", ctx.lang),
                            [[(t("btn_back", ctx.lang), "rem:back")]])

    def _where_label(self, ctx: Ctx, mode: str) -> str:
        if mode == "all":
            return t("where_all", ctx.lang)
        if mode == "primary":
            return t("where_primary", ctx.lang)
        return t("where_pin", ctx.lang, platform=self._platform_label(mode))

    async def _rem_where(self, ctx: Ctx) -> None:
        """Where task reminders go: everywhere, last-talked platform, or one
        platform pinned. Pinning an unlinked platform would be a silent dead
        end, so only the linked ones are offered."""
        accounts = await self.store.get_accounts(ctx.user.id)
        linked = {str(a.platform) for a in accounts}
        modes = ["all", "primary"] + [p for p in ("telegram", "vk") if p in linked]
        rows = []
        for m in modes:
            label = self._where_label(ctx, m)
            if ctx.settings.notify_platforms == m:
                label = "✓ " + label
            rows.append([(label, f"rem:where:{m}")])
        rows.append([(t("btn_back", ctx.lang), "rem:back")])
        ctx.suppress_menu = True
        await ctx.reply(t("where_title", ctx.lang), rows)

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

    async def _reminput_bad(self, ctx: Ctx, field: str) -> None:
        """A rejected value keeps the dialog open: fix it right here."""
        self._pending_inputs[ctx.user.id] = f"reminput:{field}"
        ctx.suppress_menu = True
        await ctx.reply(t("wizard_bad_input", ctx.lang,
                          hint=t(f"rem_ask_{field}", ctx.lang)),
                        [[(t("btn_back", ctx.lang), "rem:back")]])

    async def _reminput(self, ctx: Ctx, field: str, text: str) -> None:
        values: dict = {}
        if field == "morning":
            clock = read_clock(text)
            if clock is None:
                await self._reminput_bad(ctx, "morning")
                return
            values = {"morning_time": clock}
        elif field == "nag":
            if not text.isdigit() or int(text) < 5:
                await self._reminput_bad(ctx, "nag")
                return
            values = {"nag_interval_min": int(text)}
        elif field == "quiet":
            parts = text.split()
            parsed = [read_clock(p) for p in parts]
            if len(parts) != 2 or any(p is None for p in parsed):
                await self._reminput_bad(ctx, "quiet")
                return
            values = {"quiet_start": parsed[0], "quiet_end": parsed[1]}
        elif field == "ttl":
            if not text.isdigit() or not 1 <= int(text) <= 168:
                await self._reminput_bad(ctx, "ttl")
                return
            values = {"location_ttl_hours": int(text)}
        elif field == "tz":
            try:
                ZoneInfo(text.strip())
            except (KeyError, ValueError):
                await self._reminput_bad(ctx, "tz")
                return
            values = {"tz": text.strip()}
        elif field == "horizon":
            if not text.isdigit() or not 1 <= int(text) <= 90:
                await self._reminput_bad(ctx, "horizon")
                return
            values = {"stats_horizon_days": int(text)}
        st = await self.store.update_settings(ctx.user.id, **values)
        ctx.settings = st
        if field == "tz":
            # task plans are stored as the user's wall-clock, so the open
            # future has to be re-derived: "к 18:00" keeps meaning 18:00 here
            new_tz = ZoneInfo(st.tz)
            await self.store.replan_open_occurrences(
                ctx.user.id, datetime.now(new_tz).date(),
                max(self.cfg.occurrence_horizon_days, st.stats_horizon_days))
        # value changed → the settings screen updates in place, no new copy
        await self._settings_status(ctx)
