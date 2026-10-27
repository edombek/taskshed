"""Task editor: tabbed pickers, per-field edits, deletion, places wipe."""

from datetime import UTC, datetime, time, timedelta

from chatenv import PID, prev_buttons, send

from taskshed.core.models import ScheduleType


class TestTaskEditor:
    """✏️ Editor: change any field of a task, reach past and recurring ones."""

    async def _seed(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        await store.add_place(user.id, "Работа")
        places = await store.list_places(user.id)
        home, work = places[0].id, places[1].id
        today = datetime.now(UTC).date()
        future = await store.create_task(
            user.id, home, "Полка", ScheduleType.ONCE, {}, time(9, 0),
            deadline=datetime.now(UTC) + timedelta(days=2),
            start_at=datetime.now(UTC) + timedelta(days=1))
        every = await store.create_task(
            user.id, home, "Полить", ScheduleType.EVERY_N_DAYS,
            {"n": 1, "anchor": today.isoformat()}, time(9, 0))
        await store.materialize_occurrences(every, today, 3, "UTC")
        weekly = await store.create_task(
            user.id, home, "Стирка", ScheduleType.WEEKDAYS,
            {"weekdays": [2]}, time(9, 0))
        back = await store.create_task(
            user.id, home, "Прошлое", ScheduleType.EVERY_N_DAYS,
            {"n": 5, "anchor": (today - timedelta(days=30)).isoformat()}, time(9, 0))
        await store.materialize_occurrences(back, today - timedelta(days=30), 25, "UTC")
        await store.mark_overdue(datetime.now(UTC))
        return store, user, future.id, every.id, weekly.id, back.id, work

    def _cbs(self, messenger) -> list[str]:
        return [cb for row in messenger.last_buttons() for _, cb in row]

    async def test_picker_offers_tabs_and_upcoming_tasks(self, env):
        store, user, future, every, _, back, _ = await self._seed(env)
        f = await send(env, "/изменить")
        flat = self._cbs(f)
        assert f"edt:future:{future}" in flat and f"edt:future:{every}" in flat
        assert "etab:past" in flat and "etab:recurring" in flat and "etab:all" in flat
        assert "Будущие" in f.texts()[0]
        # past and recurring live behind their own tabs
        f = await send(env, cb="etab:past")
        flat = self._cbs(f)
        assert f"edt:past:{back}" in flat and f"edt:past:{every}" not in flat
        assert f"edt:past:{future}" not in flat
        f = await send(env, cb="etab:recurring")
        assert f"edt:recurring:{every}" in self._cbs(f)

    async def test_rename_keeps_the_rest_of_the_task(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, "/изменить")
        f = await send(env, cb=f"edf:{future}:title")
        assert "Название" in f.texts()[0] or "название" in f.texts()[0]
        f = await send(env, "повесить полку")
        assert "повесить полку" in f.texts()[0] and "Изменено" in f.texts()[0]
        task = await store.get_task(user.id, future)
        assert task.title == "повесить полку"
        assert task.start_at and task.reminder_time == time(9, 0)

    async def test_move_to_another_place(self, env):
        store, user, future, *_, work = await self._seed(env)
        await send(env, cb=f"edf:{future}:place")
        await send(env, cb=f"place:{work}")
        assert (await store.get_task(user.id, future)).place_id == work

    async def test_change_reminder_moment(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, cb=f"edf:{future}:reminder")
        f = await send(env, cb="tm:07:15")
        assert "07:15" in f.texts()[0]
        assert (await store.get_task(user.id, future)).reminder_time == time(7, 15)

    async def test_change_weekdays_only(self, env):
        store, user, _, _, weekly, *_ = await self._seed(env)
        await send(env, cb=f"edf:{weekly}:weekdays")
        await send(env, cb="wd:4")
        await send(env, cb="wdsubmit")
        task = await store.get_task(user.id, weekly)
        assert sorted(task.schedule_data["weekdays"]) == [2, 4]
        assert task.reminder_time == time(9, 0)

    async def test_moving_the_window_rebuilds_open_repeats(self, env):
        store, user, future, *_ = await self._seed(env)
        await send(env, cb=f"edf:{future}:when")
        await send(env, cb="dt:6")        # start six days ahead
        await send(env, cb="tm:11:00")
        await send(env, cb="dtl:eod")     # ready by the end of that day
        f = await send(env, cb="tm:11:00")
        assert "Изменено" in f.texts()[0]
        task = await store.get_task(user.id, future)
        assert task.start_at.date() == (datetime.now(UTC) + timedelta(days=6)).date()
        # the old repeat is gone, the new one exists
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC), datetime.now(UTC) + timedelta(days=9))
        mine = [o for o, t in occs if t.id == future]
        assert len(mine) == 1 and mine[0].start_at.date() == task.start_at.date()

    async def test_delete_asks_then_removes_history_too(self, env):
        store, user, _, _, _, back, _ = await self._seed(env)
        await send(env, cb="etab:past")
        f = await send(env, cb=f"eddel:past:{back}")
        assert "edyes" in " ".join(self._cbs(f)) and "Прошлое" in f.texts()[0]
        f = await send(env, cb=f"edyes:past:{back}")
        assert "удалена" in f.texts()[0]
        assert await store.get_task(user.id, back) is None
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=120), datetime.now(UTC))
        assert not [o for o, t in occs if t.id == back]  # cascade, no orphans


    async def test_menu_returns_after_the_editor_and_a_skip(self, env):
        # browsing the editor must not leave a dialog open: skip/done replies
        # still end with the main menu, otherwise nothing is clickable anymore
        app, store, _ = env
        store, user, _, every, *_ = await self._seed(env)
        home = next(p.id for p in await store.list_places(user.id) if p.name == "Дом")
        await store.set_location(user.id, home)
        await send(env, "/изменить")
        await send(env, cb="etab:all")
        await send(env, cb=f"edt:all:{every}")
        assert not app.wizards, "a card is not a dialog: no wizard may stay open"
        f = await send(env, "/задачи")
        skip = next(cb for row in prev_buttons(f) for _, cb in row
                    if cb.startswith("skip:"))
        f = await send(env, cb=skip)
        assert "Пропущено" in f.texts()[0]
        assert "Хотите сделать" in f.texts()[-1]
        assert "cmd:tasks" in [cb for row in f.sent[-1].buttons for _, cb in row]

    async def test_place_delete_asks_before_wiping_its_tasks(self, env):
        store, user, future, every, weekly, back, _ = await self._seed(env)
        home = next(p.id for p in await store.list_places(user.id) if p.name == "Дом")
        f = await send(env, cb=f"pdel:{home}")
        assert "pdelok" in " ".join(self._cbs(f))
        assert await store.get_place(user.id, home) is not None
        f = await send(env, cb=f"pdelok:{home}")
        assert "удалено задач: 4" in f.texts()[0]
        assert await store.get_place(user.id, home) is None
        assert await store.list_tasks(user.id, active_only=False) == []
        occs = await store.occurrences_in_range(
            user.id, datetime.now(UTC) - timedelta(days=90), datetime.now(UTC))
        assert occs == []  # history went with it, and the user said yes to that

    async def test_places_come_back_after_a_full_wipe(self, env):
        # the owner deleted both places: the first-run UX (a bare word is a
        # place name) must pick up again, and near-duplicates are refused
        app, store, _ = env
        store, user, *_ = await self._seed(env)
        for p in await store.list_places(user.id):
            f = await send(env, cb=f"pdel:{p.id}")
            ok = [cb for row in (f.last_buttons() or []) for _, cb in row
                  if cb.startswith("pdelok:")]
            if ok:
                await send(env, cb=ok[0])
        assert await store.list_places(user.id) == []
        assert await store.list_tasks(user.id, active_only=False) == []
        f = await send(env, "Дом")
        assert "добавлено" in f.texts()[0]
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]
        await send(env, "/места")
        f = await send(env, "дом")
        assert "уже есть" in f.texts()[0]
        assert [p.name for p in await store.list_places(user.id)] == ["Дом"]
