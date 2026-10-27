"""Reminder scheduler: per-minute tick with strict silence rules.

Rules:
1. reminders_on=false / quiet hours / trigger moment not reached -> silent
2. no repeat needs this user today (any place)                    -> silent
3. current location stale or unset                                -> ask where (once)
4. nothing to say for the current place and none elsewhere        -> silent

Two triggers per user, both read as the user's own wall-clock:
* the **morning digest** at `settings.morning_time`, once per calendar day —
  it covers every open repeat whose notice moment is today or already passed
  while the deadline is still ahead ("дедлайн позже, уведомление уже было");
* the **every-T-minutes repeat** (`settings.nag_interval_min`) for repeats
  sitting between their notice moment and their deadline, still open.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from taskshed.bot.app import BotApp
from taskshed.core.models import UserSettings
from taskshed.core.scheduling import in_notice_window
from taskshed.i18n import t
from taskshed.storage.repositories import Store

log = logging.getLogger(__name__)

TICK_SECONDS = 60


def _in_quiet(local: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end  # window spans midnight


def _morning_at(settings: UserSettings, today, tz: ZoneInfo) -> datetime:
    """This day's morning digest moment, as a real instant."""
    return datetime.combine(today, settings.morning_time, tzinfo=tz).astimezone(UTC)


def _morning_done(settings: UserSettings, today, tz: ZoneInfo) -> bool:
    """Has this calendar day's morning digest already gone out?"""
    last = settings.last_morning_at
    return last is not None and last.astimezone(tz).date() >= today


async def run_tick(app: BotApp, store: Store, now: datetime) -> None:
    await store.mark_overdue(now)
    for user_id in await store.all_user_ids():
        try:
            await _tick_user(app, store, user_id, now)
        except Exception:
            log.exception("tick failed for user %s", user_id)


async def _tick_user(app: BotApp, store: Store, user_id: int, now: datetime) -> None:
    settings = await store.get_settings(user_id)
    if not settings.reminders_on:
        return
    tz = ZoneInfo(settings.tz)
    today = now.astimezone(tz).date()

    # materialize upcoming occurrences for all active tasks
    horizon = app.cfg.occurrence_horizon_days
    for task in await store.list_tasks(user_id):
        await store.materialize_occurrences(task, today, horizon)

    # rule 2: today's set — deadline today or later, notice moment today or
    # earlier. Empty means fully silent, whatever the clock says.
    todays = await store.rows_to_announce(user_id, today)
    if not todays:
        return

    morning_due = now >= _morning_at(settings, today, tz) and not _morning_done(
        settings, today, tz)
    window = [(o, task) for o, task in todays if in_notice_window(o, now)]
    nag_due = bool(window) and (
        settings.last_notified_at is None
        or (now - settings.last_notified_at).total_seconds()
        >= settings.nag_interval_min * 60)
    if not (morning_due or nag_due):
        return
    if _in_quiet(now.astimezone(tz).time(), settings.quiet_start, settings.quiet_end):
        return

    loc = await store.get_location(user_id)
    fresh = (loc is not None and (now - loc[1]).total_seconds()
             <= settings.location_ttl_hours * 3600)
    if fresh:
        rows = todays if morning_due else window
        # rule 4: nothing for this place and nothing elsewhere — stay quiet
        if not await app.send_notification(user_id, settings, loc[0], rows,
                                           morning=morning_due):
            return
        values = {"last_notified_at": now, "pending_reminder": False}
        if morning_due:
            values["last_morning_at"] = now
        await store.update_settings(user_id, **values)
        return

    # rule 3: stale location — ask once, the notification follows on the answer
    if settings.pending_reminder:
        return
    places = await store.list_places(user_id)
    if not places:
        return
    lang = "ru"
    user = await store.get_user(user_id)
    if user:
        lang = user.language
    rows = [[(p.name, f"place:{p.id}")] for p in places]
    rows.append([(t("btn_loc_later", lang), "loc:later")])
    rows.append([(t("btn_back", lang), "menu:back")])
    # whatever the user was typing waits here until the place is known, and is
    # put back afterwards instead of being eaten by this question
    app.park_for_location(user_id)
    await app.notify_user(user_id, t("digest_where", lang), rows)
    await store.update_settings(user_id, pending_reminder=True)


async def run_forever(app: BotApp, store: Store) -> None:
    log.info("reminder scheduler started (every %ss)", TICK_SECONDS)
    while True:
        await run_tick(app, store, datetime.now(UTC))
        await asyncio.sleep(TICK_SECONDS)
