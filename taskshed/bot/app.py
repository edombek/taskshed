"""BotApp: dispatches RawEvents to commands / wizards / buttons.

The dialogs themselves live in the feature mixins below (one file per
feature). Telegram and VK adapters only translate their wire formats
into RawEvent + implement IMessenger. The shared transient state lives
in BotBase: one dict per feature, keyed by user id.
"""

import logging
from datetime import datetime

from taskshed.bot.base import BotBase
from taskshed.bot.cards import TaskCardsMixin
from taskshed.bot.consts import COMMANDS, defang
from taskshed.bot.context import Ctx
from taskshed.bot.digest import DigestMixin
from taskshed.bot.editor import EditorMixin
from taskshed.bot.location import LocationMixin
from taskshed.bot.places import PlacesMixin
from taskshed.bot.reports import ReportsMixin
from taskshed.bot.settings import SettingsMixin
from taskshed.bot.wizard import DEFAULT_LEAD_MIN, TaskWizardMixin
from taskshed.i18n import normalize, t
from taskshed.messaging.base import ButtonRows, RawEvent

log = logging.getLogger(__name__)


class BotApp(
    BotBase,
    LocationMixin,
    PlacesMixin,
    DigestMixin,
    TaskWizardMixin,
    TaskCardsMixin,
    EditorMixin,
    ReportsMixin,
    SettingsMixin,
):


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
            await self.store.materialize_occurrences(task, today, horizon, back_days)

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
            name = defang(text.strip())
            place = await self._find_place_by_name(ctx.user.id, name)
            if place:
                await self._location_chosen(ctx, place)
                return
            if not await self.store.list_places(ctx.user.id):
                # with no places at all the question literally invites:
                # "add one by text" — honor it instead of looping
                fresh = await self.store.add_place(ctx.user.id, name)
                if fresh:
                    await self._location_chosen(ctx, fresh)
                    return
            ctx.suppress_menu = True  # only the re-ask owns its own buttons
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
            if await self.store.add_place(ctx.user.id, defang(text.strip())):
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
            await ctx.reply(t("unlink_none", ctx.lang))
            return
        await self.store.detach_account(ctx.account.id, ctx.user.id)
        await ctx.reply(t("unlink_done", ctx.lang))


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
                    await self._wizard_finish(ctx, w,
                                              w["data"].get("lead", DEFAULT_LEAD_MIN))
                    return
                await self._after_days(ctx, w)
        elif action == "pdel":
            await self._cb_place_delete(ctx, int(arg), False)
        elif action == "pdno":
            await self._cb_place_delete(ctx, int(arg), None)
        elif action == "pdelok":
            await self._cb_place_delete(ctx, int(arg), True)
        elif action == "pl" and arg == "close":
            await self._cb_places_close(ctx)
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
        elif action == "ln":
            await self._cb_lead(ctx, arg)
        elif action == "snz":
            await self._cb_snooze_set(ctx, arg)
        elif action == "snzb":
            await self._cb_snooze_back(ctx, int(arg))
        elif action == "fin":
            await self._cb_notification_finish(ctx)
        elif action == "lang":
            await self._set_lang(ctx, normalize(arg))
        elif action == "rem":
            await self._cb_rem(ctx, arg)
        elif action == "per":
            await self._stats_show(ctx, arg, None)
        elif action == "fmt":
            fmt, _, p = arg.partition(":")
            await self._stats_show(ctx, p, fmt)
        elif action == "hrs":
            await self._hours_week(ctx, int(arg))
        elif action == "dt":
            await self._cb_date(ctx, int(arg))
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
