"""Statistics screens driven through chat: periods, formats, charts."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from chatenv import PID, place_id_of, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


class TestStatsCommands:
    async def test_text_csv_chart(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid_home = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid_home, "Отчёт",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       datetime.now(UTC).timetz().replace(second=0,
                                                                          microsecond=0))
        await store.materialize_occurrences(task, today, 0)
        occ, _ = (await store.occurrences_today(user.id, today))[0]
        await store.mark_done(occ.id, "telegram")
        f = await send(env, "/статистика неделя")
        assert "Отчёт" in f.texts()[0] and "100%" in f.texts()[0]
        f = await send(env, "/статистика всё csv")
        docs = [m.document for m in f.sent if m.document]
        assert len(docs) == 1 and docs[0].endswith(".csv")
        f = await send(env, "/статистика месяц график")
        images = [m.image for m in f.sent if m.image]
        assert len(images) == 1 and images[0].endswith(".png")

    async def test_every_report_comes_once_per_place(self, env):
        # one place = one text block, one chart, one file: «Дом» and «Работа»
        # are never mixed into a single picture or table
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        await store.add_place(user.id, "Работа")
        work = await place_id_of(store, user.id, "Работа")
        today = datetime.now(UTC).date()
        for pid, title in ((home, "Уборка"), (work, "Отчёт")):
            task = await store.create_task(
                user.id, pid, title, ScheduleType.EVERY_N_DAYS,
                {"n": 1, "anchor": today.isoformat()}, time(9, 0))
            await store.materialize_occurrences(task, today, 0)
        f = await send(env, "/статистика неделя")
        text = f.texts()[0]
        assert "📍 Дом" in text and "📍 Работа" in text
        assert "Уборка" in text and "Отчёт" in text
        f = await send(env, "/статистика неделя график")
        assert len([m for m in f.sent if m.image]) == 2
        f = await send(env, "/статистика неделя csv")
        assert len([m for m in f.sent if m.document]) == 2


class TestStatsIncludePlanned:
    async def test_future_task_appears_in_stats(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        future = datetime.now(UTC) + timedelta(hours=20)  # inside the stats window
        task = await store.create_task(user.id, p.id, "запланировано", ScheduleType.ONCE,
                                       {}, time(9, 0), deadline=future)
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 8)
        f = await send(env, "/статистика всё")
        assert "запланировано" in f.texts()[-1]


class TestInteractiveStats:
    async def _seed(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        task = await store.create_task(user.id, p.id, "дело", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1,
                                        "anchor": datetime.now(UTC).date().isoformat()},
                                       time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 3)

    async def test_period_asked_via_buttons(self, env):
        await self._seed(env)
        f = await send(env, "/статистика")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert flat == ["per:week", "per:month", "per:year", "per:all", "menu:back"]
        assert len(f.sent) == 1  # picker replaces the text, no double reply

    async def test_period_button_shows_text_with_format_buttons(self, env):
        await self._seed(env)
        f = await send(env, cb="per:week")
        assert "дело" in f.texts()[-1]
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:chart:week" in flat and "fmt:csv:week" in flat
        assert len(f.sent) == 1

    async def test_chart_sends_only_the_activity_heatmap(self, env):
        await self._seed(env)
        f = await send(env, cb="fmt:chart:week")
        images = [m for m in f.sent if m.image]
        assert len(images) == 1  # the retired daily-bar chart must not return
        assert images[0].image.endswith(".png")
        # the chart is the end of the answer: the menu follow-up must come
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat

    async def test_csv_button_sends_document(self, env):
        await self._seed(env)
        f = await send(env, cb="fmt:csv:month")
        docs = [m for m in f.sent if m.document]
        assert len(docs) == 1 and docs[0].document.endswith(".csv")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestChartHorizonAndHours:
    """Charts look ahead (setting, default 1 week) and short periods go hourly."""

    async def _done_week(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(
            user.id, pid, "Отчёт", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": today.isoformat()}, time(9, 0))
        await store.materialize_occurrences(task, today, 1)
        for occ, _ in await store.occurrences_today(user.id, today):
            await store.mark_done(occ.id, "telegram")
        return app, store, fake, user

    async def test_week_period_offers_the_hourly_chart(self, env):
        app, store, fake, _ = await self._done_week(env)
        await send(env, "/статистика")
        f = await send(env, cb="per:week")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:hours:week" in flat and "fmt:chart:week" in flat
        app._pending_inputs.clear()
        await app.handle(RawEvent("telegram", PID, callback="fmt:hours:week"))
        assert any(m.image for m in fake.sent)

    async def test_longer_periods_do_not_offer_hourly(self, env):
        app, store, fake, _ = await self._done_week(env)
        await send(env, "/статистика")
        f = await send(env, cb="per:month")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:chart:month" in flat and "fmt:hours:month" not in flat

    async def test_hourly_map_covers_missed_and_skipped(self, env):
        # the hour grid is not only about finished tasks: a week where nothing
        # was completed still has to be drawn, with missed/skipped marked
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        task = await store.create_task(user.id, pid, "Уборка",
                                       ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": today.isoformat()},
                                       time(9, 0))
        await store.materialize_occurrences(task, today, 1)
        occs = await store.occurrences_today(user.id, today)
        await store.skip(occs[0][0].id)
        app._pending_inputs.clear()
        await app.handle(RawEvent("telegram", PID, callback="fmt:hours:week"))
        assert any(m.image for m in fake.sent)

    async def test_planned_task_shows_up_within_the_horizon(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        when = datetime.now(UTC) + timedelta(days=5)
        await store.create_task(user.id, pid, "Полка", ScheduleType.ONCE, {},
                                time(9, 0), deadline=when, start_at=when)
        # 5 days ahead: outside the old +1 day window, inside the week horizon
        await app.handle(RawEvent("telegram", PID, text="/статистика неделя график"))
        assert any(m.image for m in fake.sent)

    async def test_chart_forward_edge_is_the_horizon_setting(self, env):
        # ahead, the picture reaches exactly stats_horizon_days: the setting
        # decides whether a planned task is on it, not the length of a period
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        when = datetime.now(UTC) + timedelta(days=20)
        await store.create_task(user.id, pid, "Заказ", ScheduleType.ONCE, {},
                                time(9, 0), deadline=when, start_at=when)
        await app.handle(RawEvent("telegram", PID, text="/статистика месяц график"))
        assert not any(m.image for m in fake.sent)  # 20 days out, horizon is 7
        assert fake.find("срабатываний не было")
        await store.update_settings(user.id, stats_horizon_days=30)
        await app.handle(RawEvent("telegram", PID, text="/статистика месяц график"))
        assert any(m.image for m in fake.sent)

    async def test_week_hour_map_is_always_seven_sliding_days(self, env):
        # seven rows whatever the horizon or the data: three days back, today,
        # three days ahead — the sliding week, not the calendar one
        app, _, _ = env
        tz = ZoneInfo("UTC")
        today = datetime.now(UTC).date()
        for back, ahead in ((30, 8), (7, 7), (0, 1)):
            first, last = app._hour_window(
                "week", {(today, 9): {"done": 1}},
                datetime.now(UTC) - timedelta(days=back),
                datetime.now(UTC) + timedelta(days=ahead), tz)
            assert (first, last) == (today - timedelta(days=3),
                                     today + timedelta(days=3))
        # longer periods still trim to their data and stay under 24 rows
        first, last = app._hour_window(
            "month", {(today, 9): {"done": 1}},
            datetime.now(UTC) - timedelta(days=30),
            datetime.now(UTC) + timedelta(days=8), tz)
        assert first == today and (last - first).days <= 24

    async def test_hour_map_pages_to_the_next_week(self, env):
        # next week's hours are viewable, and its plan is materialized even
        # when it lies past the horizon setting
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await store.update_settings(user.id, stats_horizon_days=1)
        today = datetime.now(UTC).date()
        when = datetime.combine(today + timedelta(days=9), time(15, 0), tzinfo=UTC)
        await store.create_task(user.id, pid, "Созвон", ScheduleType.ONCE, {},
                                time(9, 0), deadline=when, start_at=when)
        f = await send(env, "/статистика неделя")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "fmt:hours:week" in flat and "hrs:7" in flat
        f = await send(env, cb="hrs:7")
        assert any(m.image for m in f.sent)
        nav = [cb for row in f.last_buttons() for _, cb in row]
        # the paging buttons carry the shift of the week they draw: back to
        # this one, or a whole week further — not a fixed ±7 from today
        assert "hrs:0" in nav and "hrs:14" in nav
        labels = {cb: lbl for row in f.last_buttons() for lbl, cb in row}
        assert labels.get("menu:back") == "✅ Готово"  # the walk ends with Готово

    async def test_hour_map_pages_many_weeks_unbounded(self, env):
        # five taps of «next» reach a task five weeks out; the drawn window
        # and the buttons move with every tap, nothing stops at ±1 week
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await store.update_settings(user.id, stats_horizon_days=1)
        today = datetime.now(UTC).date()
        when = datetime.combine(today + timedelta(days=31), time(15, 0),
                                tzinfo=UTC)
        await store.create_task(user.id, pid, "Командировка", ScheduleType.ONCE,
                                {}, time(9, 0), deadline=when, start_at=when)
        await send(env, "/статистика неделя")
        for shift in (7, 14, 21, 28):
            f = await send(env, cb=f"hrs:{shift}")
        first = today + timedelta(days=25)  # shift 28: today-3+28
        last = first + timedelta(days=6)
        img = next(m for m in f.sent if m.image)
        assert f"{first:%d.%m} — {last:%d.%m}" in img.text  # the right week drew
        nav = [cb for row in f.last_buttons() for _, cb in row]
        assert f"hrs:{28 + 7}" in nav and "hrs:21" in nav
        # the labels say which week each button leads to
        labels = [lbl for row in f.last_buttons() for lbl, cb in row
                  if cb.startswith("hrs:")]
        nxt = next(lbl for lbl in labels if "➡️" in lbl)
        assert f"{first + timedelta(days=7):%d.%m}" in nxt

    async def test_planned_week_is_planned_whole(self, env):
        # a week the horizon touches is filled to its Sunday: no planned square
        # may be missing at the right edge while its cell is drawn
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        await store.create_task(user.id, pid, "Прогулка", ScheduleType.EVERY_N_DAYS,
                                {"n": 1, "anchor": today.isoformat()}, time(9, 0))
        await send(env, "/статистика неделя график")
        horizon = today + timedelta(days=7)          # default horizon setting
        sunday = horizon + timedelta(days=6 - horizon.weekday())
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=1),
            datetime.combine(sunday + timedelta(days=1), time(0, 0), UTC))
        assert sunday in {occ.due_at.date() for occ, _ in occs}

    async def test_repeat_at_the_horizon_lights_every_planned_day(self, env):
        # the whole horizon window is materialized before it is drawn, so a
        # repeat shows on every one of its days inside it
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        home = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        await store.create_task(user.id, home, "Покупки", ScheduleType.EVERY_N_DAYS,
                                {"n": 3, "anchor": today.isoformat()}, time(9, 0))
        await store.update_settings(user.id, stats_horizon_days=30)
        await send(env, "/статистика месяц график")
        now = datetime.now(UTC)
        occs = await store.occurrences_in_range(user.id, now,
                                                now + timedelta(days=30))
        assert len(occs) >= 10          # every 3rd day across the window

    async def test_repeat_is_drawn_back_to_its_own_anchor(self, env):
        # a rule that carries a date in it exists from that date: the days it
        # was already planned on belong on the chart
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        today = datetime.now(UTC).date()
        await store.create_task(user.id, pid, "Поверка", ScheduleType.EVERY_N_DAYS,
                                {"n": 7, "anchor": (today - timedelta(days=21)).isoformat()},
                                time(9, 0))
        await app.handle(RawEvent("telegram", PID, text="/статистика месяц график"))
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=30), datetime.now(UTC))
        assert len({occ.due_at.date() for occ, _ in occs
                    if occ.due_at < datetime.now(UTC)}) >= 3

    async def test_weekday_rule_gets_no_history_before_it_existed(self, env):
        # a rule with no date of its own cannot be backfilled: a year chart
        # must not invent planned days from before the task was created
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await store.create_task(user.id, pid, "Совещание", ScheduleType.WEEKDAYS,
                                {"weekdays": [0, 2, 4]}, time(9, 0))
        await app.handle(RawEvent("telegram", PID, text="/статистика год график"))
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=365), datetime.now(UTC))
        assert not [occ for occ, _ in occs if occ.due_at < datetime.now(UTC)]
