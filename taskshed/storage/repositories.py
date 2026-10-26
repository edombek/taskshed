"""Store — the single repository facade over the DB.

Returns core dataclasses (taskshed.core.models) so that handlers, scheduler
and core logic never touch ORM rows.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from taskshed.core.models import (
    Account as AccountDC,
)
from taskshed.core.models import (
    Place as PlaceDC,
)
from taskshed.core.models import (
    Task as TaskDC,
)
from taskshed.core.models import (
    TaskOccurrence as OccDC,
)
from taskshed.core.models import (
    User as UserDC,
)
from taskshed.core.models import (
    UserSettings as SettingsDC,
)
from taskshed.core.scheduling import plan_occurrences

from .db import make_session_factory
from .tables import (
    Account,
    Base,
    Completion,
    CurrentLocation,
    LinkAudit,
    LinkCode,
    Place,
    Task,
    TaskOccurrence,
    User,
    UserSettings,
)

LINK_CODE_TTL = timedelta(minutes=10)
_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1")


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite gives naive UTC back; make it explicit."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _to_dc(r: User) -> UserDC:
    return UserDC(id=r.id, created_at=_aware(r.created_at), language=r.language)


def _place(r: Place) -> PlaceDC:
    return PlaceDC(id=r.id, user_id=r.user_id, name=r.name)


def _task(r: Task) -> TaskDC:
    return TaskDC(
        id=r.id, user_id=r.user_id, place_id=r.place_id, title=r.title, note=r.note,
        deadline=_aware(r.deadline), schedule_type=r.schedule_type,
        schedule_data=r.schedule_data or {}, reminder_time=_time(r.reminder_time),
        is_active=r.is_active,
    )


def _occ(r: TaskOccurrence) -> OccDC:
    return OccDC(id=r.id, task_id=r.task_id, due_at=_aware(r.due_at),
                 reminder_at=_aware(r.reminder_at), status=r.status)


def _settings(r: UserSettings) -> SettingsDC:
    return SettingsDC(
        user_id=r.user_id, reminders_on=r.reminders_on, remind_mode=r.remind_mode,
        remind_times=list(r.remind_times or []),
        remind_interval_min=r.remind_interval_min,
        quiet_start=_time(r.quiet_start), quiet_end=_time(r.quiet_end),
        location_ttl_hours=r.location_ttl_hours, tz=r.tz,
        notify_platforms=r.notify_platforms, stats_format=r.stats_format,
        last_reminded_at=_aware(r.last_reminded_at),
        pending_reminder=r.pending_reminder,
    )


class Store:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._sf: async_sessionmaker = make_session_factory(engine)

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ---------- users & accounts ----------

    async def get_user_by_account(self, platform: str, platform_user_id: str) -> UserDC | None:
        async with self._sf() as s:
            row = await s.scalar(
                select(User).join(Account).where(
                    Account.platform == platform,
                    Account.platform_user_id == platform_user_id,
                )
            )
            return _to_dc(row) if row else None

    async def get_account_by_key(self, platform: str, platform_user_id: str) -> AccountDC | None:
        async with self._sf() as s:
            r = await s.scalar(
                select(Account).where(Account.platform == platform,
                                      Account.platform_user_id == platform_user_id)
            )
            return (AccountDC(r.id, r.user_id, r.platform, r.platform_user_id, _aware(r.linked_at))
                    if r else None)

    async def get_user(self, user_id: int) -> UserDC | None:
        async with self._sf() as s:
            row = await s.get(User, user_id)
            return _to_dc(row) if row else None

    async def get_accounts(self, user_id: int) -> list[AccountDC]:
        async with self._sf() as s:
            rows = (await s.scalars(
                select(Account).where(Account.user_id == user_id)
            )).all()
            return [
                AccountDC(r.id, r.user_id, r.platform, r.platform_user_id,
                          _aware(r.linked_at), _aware(r.last_seen))
                for r in rows
            ]

    async def register_account(
        self, platform: str, platform_user_id: str,
        language: str = "ru", tz: str = "UTC",
    ) -> tuple[UserDC, AccountDC]:
        async with self._sf() as s:
            u = User(language=language)
            s.add(u)
            await s.flush()
            s.add(UserSettings(user_id=u.id, tz=tz))
            a = Account(user_id=u.id, platform=platform, platform_user_id=platform_user_id)
            s.add(a)
            await s.commit()
            return _to_dc(u), AccountDC(a.id, u.id, platform, platform_user_id, _aware(a.linked_at))

    async def set_language(self, user_id: int, language: str) -> None:
        async with self._sf() as s:
            await s.execute(update(User).where(User.id == user_id).values(language=language))
            await s.commit()

    async def set_user_state(self, user_id: int, state: dict | None) -> None:
        async with self._sf() as s:
            await s.execute(update(User).where(User.id == user_id).values(state_json=state))
            await s.commit()

    async def get_user_state(self, user_id: int) -> dict | None:
        async with self._sf() as s:
            row = await s.get(User, user_id)
            return row.state_json if row else None

    async def touch_account(self, account_id: int) -> None:
        async with self._sf() as s:
            await s.execute(update(Account).where(Account.id == account_id)
                            .values(last_seen=_naive(datetime.now(UTC))))
            await s.commit()

    async def all_user_ids(self) -> list[int]:
        async with self._sf() as s:
            return list((await s.scalars(select(User.id))).all())

    # ---------- settings ----------

    async def get_settings(self, user_id: int) -> SettingsDC:
        async with self._sf() as s:
            row = await s.get(UserSettings, user_id)
            assert row is not None, f"settings missing for user {user_id}"
            return _settings(row)

    async def update_settings(self, user_id: int, **values) -> SettingsDC:
        for k in ("quiet_start", "quiet_end"):
            if isinstance(values.get(k), time):
                values[k] = values[k].strftime("%H:%M")
        if "last_reminded_at" in values and values["last_reminded_at"]:
            values["last_reminded_at"] = _naive(values["last_reminded_at"])
        async with self._sf() as s:
            await s.execute(update(UserSettings).where(UserSettings.user_id == user_id)
                             .values(**values))
            await s.commit()
        return await self.get_settings(user_id)

    # ---------- places & location ----------

    async def add_place(self, user_id: int, name: str) -> PlaceDC | None:
        name = name.strip()[:80]
        if not name:
            return None
        async with self._sf() as s:
            exists = await s.scalar(
                select(Place).where(Place.user_id == user_id, Place.name == name)
            )
            if exists:
                return None
            p = Place(user_id=user_id, name=name)
            s.add(p)
            await s.commit()
            return _place(p)

    async def list_places(self, user_id: int) -> list[PlaceDC]:
        async with self._sf() as s:
            rows = (await s.scalars(
                select(Place).where(Place.user_id == user_id).order_by(Place.name)
            )).all()
            return [_place(r) for r in rows]

    async def get_place(self, user_id: int, place_id: int) -> PlaceDC | None:
        async with self._sf() as s:
            row = await s.get(Place, place_id)
            return _place(row) if row and row.user_id == user_id else None

    async def delete_place(self, user_id: int, place_id: int) -> bool:
        async with self._sf() as s:
            res = await s.execute(
                delete(Place).where(Place.id == place_id, Place.user_id == user_id)
            )
            await s.commit()
            return bool(res.rowcount)

    async def set_location(self, user_id: int, place_id: int,
                          set_at: datetime | None = None) -> None:
        async with self._sf() as s:
            await s.merge(CurrentLocation(
                user_id=user_id, place_id=place_id, set_at=_naive(set_at or datetime.now(UTC)),
            ))
            await s.commit()

    async def get_location(self, user_id: int) -> tuple[PlaceDC, datetime] | None:
        async with self._sf() as s:
            row = await s.get(CurrentLocation, user_id)
            if not row:
                return None
            place = await s.get(Place, row.place_id)
            if not place:
                return None
            return _place(place), _aware(row.set_at)

    # ---------- tasks ----------

    async def create_task(self, user_id: int, place_id: int, title: str,
                          schedule_type: str, schedule_data: dict,
                          reminder_time: time, deadline: datetime | None = None,
                          note: str = "") -> TaskDC:
        async with self._sf() as s:
            t = Task(
                user_id=user_id, place_id=place_id, title=title[:200], note=note,
                schedule_type=schedule_type, schedule_data=schedule_data,
                reminder_time=reminder_time.strftime("%H:%M"),
                deadline=_naive(deadline) if deadline else None,
            )
            s.add(t)
            await s.commit()
            return _task(t)

    async def get_task(self, task_id: int) -> TaskDC | None:
        async with self._sf() as s:
            row = await s.get(Task, task_id)
            return _task(row) if row else None

    async def list_tasks(self, user_id: int, place_id: int | None = None,
                         active_only: bool = True) -> list[TaskDC]:
        async with self._sf() as s:
            q = select(Task).where(Task.user_id == user_id)
            if place_id is not None:
                q = q.where(Task.place_id == place_id)
            if active_only:
                q = q.where(Task.is_active.is_(True))
            rows = (await s.scalars(q.order_by(Task.id))).all()
            return [_task(r) for r in rows]

    async def set_task_active(self, task_id: int, user_id: int, active: bool) -> bool:
        async with self._sf() as s:
            res = await s.execute(update(Task).where(Task.id == task_id, Task.user_id == user_id)
                                  .values(is_active=active))
            await s.commit()
            return bool(res.rowcount)

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        async with self._sf() as s:
            res = await s.execute(delete(Task).where(Task.id == task_id, Task.user_id == user_id))
            await s.commit()
            return bool(res.rowcount)

    # ---------- occurrences ----------

    async def materialize_occurrences(self, task: TaskDC, today: date,
                                      horizon_days: int, tz: str) -> int:
        tzinfo = ZoneInfo(tz)
        planned = plan_occurrences(task, today, horizon_days, tzinfo)
        inserted = 0
        async with self._sf() as s:
            for occ in planned:
                exists = await s.scalar(
                    select(TaskOccurrence.id).where(
                        TaskOccurrence.task_id == task.id,
                        TaskOccurrence.due_at == _naive(occ.due_at),
                    )
                )
                if exists is None:
                    s.add(TaskOccurrence(task_id=task.id, due_at=_naive(occ.due_at),
                                         reminder_at=_naive(occ.reminder_at)))
                    inserted += 1
            await s.commit()
        return inserted

    async def occurrences_today(self, user_id: int, today_local: date,
                                tz: str, place_id: int | None = None) -> list[tuple[OccDC, TaskDC]]:
        """Pending/missed occurrences due on the user-local date, plus overdue pending."""
        tzinfo = ZoneInfo(tz)
        day_start = _naive(datetime.combine(today_local, time.min, tzinfo=tzinfo))
        day_end = _naive(datetime.combine(today_local, time.max, tzinfo=tzinfo))
        async with self._sf() as s:
            q = (
                select(TaskOccurrence, Task)
                .join(Task, Task.id == TaskOccurrence.task_id)
                .where(Task.user_id == user_id, Task.is_active.is_(True))
                .where(
                    (
                        (TaskOccurrence.due_at >= day_start) & (TaskOccurrence.due_at <= day_end)
                        & (TaskOccurrence.status != "done")
                    )
                    | ((TaskOccurrence.status == "pending") & (TaskOccurrence.due_at < day_start))
                )
            )
            if place_id is not None:
                q = q.where(Task.place_id == place_id)
            rows = await s.execute(q.order_by(TaskOccurrence.due_at))
            return [(_occ(o), _task(t)) for o, t in rows]

    async def mark_done(self, occurrence_id: int, via: str) -> bool:
        async with self._sf() as s:
            occ = await s.get(TaskOccurrence, occurrence_id)
            if not occ or occ.status == "done":
                return False
            occ.status = "done"
            s.add(Completion(occurrence_id=occ.id, done_at=_naive(datetime.now(UTC)),
                             completed_via=via))
            await s.commit()
            return True

    async def snooze(self, occurrence_id: int, minutes: int) -> bool:
        async with self._sf() as s:
            occ = await s.get(TaskOccurrence, occurrence_id)
            if not occ or occ.status == "done":
                return False
            now = datetime.now(UTC)
            delta = timedelta(minutes=minutes)
            occ.reminder_at = _naive(now + delta)
            due = _aware(occ.due_at)
            if due <= now:
                occ.due_at = _naive(now + delta)
            await s.commit()
            return True

    async def mark_overdue(self, now: datetime) -> int:
        async with self._sf() as s:
            res = await s.execute(
                update(TaskOccurrence)
                .where(TaskOccurrence.status == "pending", TaskOccurrence.due_at < _naive(now))
                .values(status="missed")
            )
            await s.commit()
            return res.rowcount

    # ---------- linking ----------

    async def create_link_code(self, user_id: int, platform: str) -> str:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        now = datetime.now(UTC)
        async with self._sf() as s:
            await s.execute(delete(LinkCode).where(LinkCode.user_id == user_id))
            s.add(LinkCode(code=code, user_id=user_id, created_by_platform=platform,
                           expires_at=_naive(now + LINK_CODE_TTL)))
            await s.commit()
        return code

    async def redeem_link_code(self, code: str) -> int | None:
        """Returns owning user_id if code is valid & fresh, else None."""
        now = _naive(datetime.now(UTC))
        async with self._sf() as s:
            row = await s.get(LinkCode, code.upper())
            if not row or row.used or row.expires_at < now:
                return None
            row.used = True
            await s.commit()
            return row.user_id

    async def attach_account(self, account: AccountDC, user_id: int, action: str = "link") -> None:
        async with self._sf() as s:
            await s.execute(update(Account).where(Account.id == account.id)
                            .values(user_id=user_id))
            s.add(LinkAudit(user_id=user_id, action=action,
                            detail=f"{account.platform}:{account.platform_user_id}"))
            await s.commit()

    async def detach_account(self, account_id: int, user_id: int) -> None:
        async with self._sf() as s:
            await s.execute(delete(Account).where(Account.id == account_id))
            s.add(LinkAudit(user_id=user_id, action="unlink", detail=f"account {account_id}"))
            await s.commit()

    # ---------- stats support ----------

    async def get_occurrence(self, occurrence_id: int) -> tuple[OccDC, TaskDC] | None:
        async with self._sf() as s:
            row = await s.get(TaskOccurrence, occurrence_id)
            if not row:
                return None
            task = await s.get(Task, row.task_id)
            return (_occ(row), _task(task)) if task else None

    async def merge_user(self, from_id: int, to_id: int) -> None:
        """Move places/tasks of `from_id` into `to_id`, rename name clashes."""
        async with self._sf() as s:
            places = (await s.scalars(select(Place).where(Place.user_id == from_id))).all()
            existing = {p.name for p in (
                await s.scalars(select(Place).where(Place.user_id == to_id))
            ).all()}
            for p in places:
                name = p.name
                i = 2
                while name in existing:
                    name = f"{p.name} #{i}"
                    i += 1
                p.name = name
                p.user_id = to_id
                existing.add(name)
            await s.execute(update(Task).where(Task.user_id == from_id).values(user_id=to_id))
            await s.execute(delete(UserSettings).where(UserSettings.user_id == from_id))
            await s.execute(delete(CurrentLocation).where(CurrentLocation.user_id == from_id))
            await s.execute(delete(User).where(User.id == from_id))
            s.add(LinkAudit(user_id=to_id, action="merge", detail=f"from user {from_id}"))
            await s.commit()

    async def occurrences_in_range(self, user_id: int, start: datetime,
                                   end: datetime) -> list[tuple[OccDC, TaskDC]]:
        async with self._sf() as s:
            rows = await s.execute(
                select(TaskOccurrence, Task)
                .join(Task, Task.id == TaskOccurrence.task_id)
                .where(Task.user_id == user_id,
                       TaskOccurrence.due_at >= _naive(start),
                       TaskOccurrence.due_at <= _naive(end))
                .order_by(TaskOccurrence.due_at)
            )
            return [(_occ(o), _task(t)) for o, t in rows]
