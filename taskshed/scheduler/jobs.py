"""Reminder scheduler: per-minute tick with strict silence rules.

Rules (fixed by the design contract):
1. reminders_on=false / quiet hours / trigger moment not reached -> silent
2. no occurrences due today (any place)                     -> silent
3. current location stale or unset                          -> ask where (once)
4. no occurrences for the current place                     -> silent

The trigger moment is either the global settings moment (times/interval mode)
or a task's own reminder time (`task_occurrences.reminder_at`) that has been
reached and not covered yet by `last_reminded_at`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from taskshed.bot.app import BotApp
from taskshed.core.models import UserSettings
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


def _trigger_due(settings: UserSettings, now: datetime, tz: ZoneInfo) -> bool:
    """Is the current minute a reminder moment that hasn't fired yet?"""
    last = settings.last_reminded_at
    local_now = now.astimezone(tz)
    if settings.remind_mode == "interval":
        if last is None:
            return True
        return (now - last).total_seconds() >= settings.remind_interval_min * 60
    # times mode: fire if any scheduled moment today (user tz) has passed
    # and hasn't been covered by last_reminded_at
    for hhmm in settings.remind_times:
        try:
            h, m = (int(x) for x in hhmm.split(":"))
            fire = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
        except ValueError:
            continue
        if fire <= local_now and (last is None or last < fire.astimezone(UTC)):
            return True
    return False


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
        await store.materialize_occurrences(task, today, horizon, settings.tz)

    today_occs = await store.occurrences_today(user_id, today, settings.tz)
    if not today_occs:
        return  # rule 2: nothing today — fully silent

    # a task's own reminder time (or snooze) that we haven't covered yet
    per_task_due = any(
        o.status == "pending" and o.reminder_at <= now
        and (settings.last_reminded_at is None
             or settings.last_reminded_at < o.reminder_at)
        for o, _ in today_occs
    )
    if not (_trigger_due(settings, now, tz) or per_task_due):
        return
    if _in_quiet(now.astimezone(tz).time(), settings.quiet_start, settings.quiet_end):
        return

    loc = await store.get_location(user_id)
    fresh = loc is not None and (now - loc[1]).total_seconds() <= settings.location_ttl_hours * 3600
    if fresh:
        place = loc[0]
        digest = await app.build_digest(user_id, settings, place)
        if digest is None:
            return  # rule 4: nothing for this place — silent
        await app.notify_user(user_id, *digest)
        await store.update_settings(user_id, last_reminded_at=now, pending_reminder=False)
        return

    # rule 3: stale location — ask once, digest will follow on the answer
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
    rows.append([(t("btn_back", lang), "menu:back")])
    await app.notify_user(user_id, t("digest_where", lang), rows)
    await store.update_settings(user_id, pending_reminder=True, last_reminded_at=now)


async def run_forever(app: BotApp, store: Store) -> None:
    log.info("reminder scheduler started (every %ss)", TICK_SECONDS)
    while True:
        await run_tick(app, store, datetime.now(UTC))
        await asyncio.sleep(TICK_SECONDS)
