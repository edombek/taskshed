"""Today's task list and its action buttons (snooze, skip, в работе)."""

from datetime import UTC, datetime, time, timedelta

from chatenv import PID, place_id_of, prev_buttons, send

from taskshed.core.models import ScheduleType
from taskshed.messaging.base import RawEvent


class TestSnoozePicker:
    """⏭ Отложить must ask *how long* instead of picking a fixed hour."""

    async def _occ(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(0, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        return store, occs[0][0].id

    async def test_picker_offers_durations(self, env):
        _, occ_id = await self._occ(env)
        f = await send(env, cb=f"snooze:{occ_id}")
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert f"snz:{occ_id}:15" in flat
        assert f"snz:{occ_id}:1440" in flat
        # the picker itself is the interactive reply: no menu spam below it
        assert len(f.sent) == 1

    async def test_choice_snoozes_by_requested_minutes(self, env):
        store, occ_id = await self._occ(env)
        before = (await store.get_occurrence(occ_id))[0].reminder_at
        f = await send(env, cb=f"snz:{occ_id}:15")
        assert any("15 мин" in t for t in f.texts())
        after = (await store.get_occurrence(occ_id))[0].reminder_at
        assert after > before + timedelta(minutes=14)
        # after a *completed* action the menu follow-up returns
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat


class TestSkipOccurrence:
    async def _occ(self, env):
        _, store, _ = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        task = await store.create_task(user.id, p.id, "T", ScheduleType.EVERY_N_DAYS,
                                       {"n": 1, "anchor": anchor}, time(9, 0))
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 1, "UTC")
        occs = await store.occurrences_today(user.id, datetime.now(UTC).date(), "UTC")
        return store, occs[0][0].id

    async def test_skip_closes_occurrence_without_missed(self, env):
        store, occ_id = await self._occ(env)
        f = await send(env, "/задачи")
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert f"skip:{occ_id}" in flat
        f = await send(env, cb=f"skip:{occ_id}")
        assert f.find("Пропущено")
        occ, _ = await store.get_occurrence(occ_id)
        assert occ.status == "skipped"
        # gone from today's list, and the background overdue pass ignores it
        assert not await store.occurrences_today(
            (await store.get_user_by_account("telegram", PID)).id,
            datetime.now(UTC).date(), "UTC")
        await store.mark_overdue(datetime.now(UTC) + timedelta(days=1))
        assert (await store.get_occurrence(occ_id))[0].status == "skipped"

    async def test_skipped_shows_in_stats_not_as_missed(self, env):
        store, occ_id = await self._occ(env)
        await send(env, cb=f"skip:{occ_id}")
        f = await send(env, "/статистика всё")
        assert "пропущено 1" in f.texts()[-1]
        assert "просрочено 0" in f.texts()[-1]


class TestWorkInProgress:
    """One-off tasks have a work window and a «▶ В работе» button."""

    async def _stretched_task(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        task = await store.create_task(
            user.id, pid, "Полка", ScheduleType.ONCE, {}, time(9, 0),
            deadline=datetime.now(UTC) - timedelta(hours=1),
            start_at=datetime.now(UTC) - timedelta(hours=3),
        )
        await store.materialize_occurrences(task, datetime.now(UTC).date(), 3, "UTC")
        await store.set_location(user.id, pid)
        return app, store, task

    async def test_list_offers_wip_and_shows_the_window(self, env):
        app, store, task = await self._stretched_task(env)
        f = await send(env, "/задачи")
        assert any("с " in m and " до " in m for m in f.texts())
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert any(cb.startswith("wip:") for cb in flat)
        assert any(cb.startswith("done:") for cb in flat)

    async def test_wip_keeps_the_row_open_and_drops_itself(self, env):
        app, store, _ = await self._stretched_task(env)
        f = await send(env, "/задачи")
        occ_id = next(int(cb.split(":")[1]) for row in prev_buttons(f)
                      for _, cb in row if cb.startswith("wip:"))
        f = await send(env, cb=f"wip:{occ_id}")
        assert "В работе: «Полка»" in f.texts()[0]
        # the overdue pass does not turn started work into missed
        assert await store.mark_overdue(datetime.now(UTC)) == 0
        f = await send(env, "/задачи")
        assert any("▶в работе" in m for m in f.texts())
        flat = [cb for row in prev_buttons(f) for _, cb in row]
        assert not any(cb.startswith("wip:") for cb in flat)
        assert not any(cb.startswith("snooze:") for cb in flat)
        assert f"done:{occ_id}" in flat and f"skip:{occ_id}" in flat

    async def test_digest_offers_wip_too(self, env):
        app, store, _ = await self._stretched_task(env)
        await store.set_location((await store.get_user_by_account("telegram", PID)).id,
                                 await place_id_of(store, (await store.get_user_by_account(
                                     "telegram", PID)).id, "Дом"))
        settings = await store.get_settings((await store.get_user_by_account(
            "telegram", PID)).id)
        place = (await store.get_location((await store.get_user_by_account(
            "telegram", PID)).id))[0]
        digest = await app.build_digest(
            (await store.get_user_by_account("telegram", PID)).id, settings, place)
        assert digest and any(cb.startswith("wip:")
                              for _, _, rows in digest[1] for row in rows
                              for _, cb in row)


class TestOneMessagePerTask:
    """One task = one message. A button tap re-renders that very message:
    closed task loses its keyboard, no new messages, no menu follow-up."""

    async def _list_with_two(self, env):
        _, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        p = (await store.list_places(user.id))[0]
        await store.set_location(user.id, p.id)
        anchor = datetime.now(UTC).date().isoformat()
        for title in ("A", "B"):
            task = await store.create_task(user.id, p.id, title,
                                           ScheduleType.EVERY_N_DAYS,
                                           {"n": 1, "anchor": anchor}, time(9, 0))
            await store.materialize_occurrences(task, datetime.now(UTC).date(),
                                                1, "UTC")
        await send(env, "/задачи")
        assert fake.sent[0].buttons == []  # header: a plain text message
        return fake.sent[1], fake.sent[2]  # the two task messages

    async def _tap(self, env, cb, msg_id):
        """Tap without the send() helper: it would wipe the edited message."""
        app, _, fake = env
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", PID, callback=cb, message_id=msg_id))
        return fake.sent[before:]

    async def test_done_disarms_only_its_own_message(self, env):
        a, b = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("done:"))
        assert not await self._tap(env, f"done:{d1}", a.id)  # nothing new sent
        assert "✅" in a.text and a.buttons == []
        # the neighbour task keeps its keyboard untouched
        assert any(cb.startswith("done:") for row in b.buttons for _, cb in row)

    async def test_wip_swaps_the_keyboard_in_place(self, env):
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("wip:"))
        assert not await self._tap(env, f"wip:{d1}", a.id)
        assert "▶в работе" in a.text
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"done:{d1}" in flat and f"skip:{d1}" in flat
        assert not any(cb.startswith("wip:") for cb in flat)

    async def test_snooze_picker_lives_in_the_same_message(self, env):
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("snooze:"))
        assert not await self._tap(env, f"snooze:{d1}", a.id)
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"snz:{d1}:15" in flat and f"snzb:{d1}" in flat
        assert not await self._tap(env, f"snz:{d1}:15", a.id)
        assert "💤" in a.text  # the note landed on the task's own message
        flat = [cb for row in a.buttons for _, cb in row]
        assert f"done:{d1}" in flat  # still open, buttons are back

    async def test_unknown_message_falls_back_to_the_old_reply(self, env):
        # unknown message id (restart, forwarded copy): reply + menu as before
        a, _ = await self._list_with_two(env)
        d1 = next(int(cb.split(":")[1]) for row in a.buttons for _, cb in row
                  if cb.startswith("done:"))
        f = await send(env, cb=f"done:{d1}", msg="telegram-999")
        assert any("Готово" in t for t in f.texts())
        flat = [cb for row in f.last_buttons() for _, cb in row]
        assert "cmd:tasks" in flat
        assert a.buttons  # the untouched original keeps its keyboard


class TestDigestPerTask:
    async def test_digest_is_a_header_plus_one_message_per_task(self, env):
        app, store, fake = env
        await send(env, "Дом")
        user = await store.get_user_by_account("telegram", PID)
        pid = await place_id_of(store, user.id, "Дом")
        await store.set_location(user.id, pid)
        anchor = datetime.now(UTC).date().isoformat()
        for title in ("A", "B"):
            task = await store.create_task(user.id, pid, title,
                                           ScheduleType.EVERY_N_DAYS,
                                           {"n": 1, "anchor": anchor}, time(9, 0))
            await store.materialize_occurrences(task, datetime.now(UTC).date(),
                                                1, "UTC")
        acc = (await store.get_accounts(user.id))[0]
        base = len(fake.sent)
        assert await app._digest_now(user.id, acc)
        assert "Задачи" in fake.sent[base].text
        assert fake.sent[base].buttons == []
        assert len(fake.sent) == base + 3  # header + two task messages
        msg = fake.sent[-1]
        occ_id = next(int(cb.split(":")[1]) for row in msg.buttons
                      for _, cb in row if cb.startswith("done:"))
        before = len(fake.sent)
        await app.handle(RawEvent("telegram", PID, callback=f"done:{occ_id}",
                                  message_id=msg.id))
        assert len(fake.sent) == before  # the task message absorbed the tap
        assert "✅" in msg.text and msg.buttons == []
