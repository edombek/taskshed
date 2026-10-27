"""deadline clock and warning lead per task; morning moment and repeat interval

Revision ID: a7f3c2d81b64
Revises: f2a6b83c14d9
Create Date: 2026-10-27 18:40:02.114380

Every task kind now carries a deadline clock and a "warn me this many minutes
before" lead, so the per-task reminder clock goes away. Task plans also stop
being host instants: `tasks.deadline`/`start_at` become the **owner's
wall-clock**, and the materialized `task_occurrences.reminder_at` becomes
`notify_at`.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f3c2d81b64'
down_revision: str | None = 'f2a6b83c14d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _parse(value) -> datetime | None:
    """SQLite hands raw SQL datetimes back as text; make them datetimes."""
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:                          # some rows carry an empty string here
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparsable datetime stored in the database: {value!r}")


def _to_wall(stored, tz_name: str) -> datetime | None:
    """A naive-UTC instant -> the same moment as wall-clock in the user's zone."""
    dt = _parse(stored)
    if dt is None:
        return None
    dt = dt if dt.tzinfo is None else dt.astimezone(UTC).replace(tzinfo=None)
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz_name or "UTC")).replace(tzinfo=None)


def upgrade() -> None:
    conn = op.get_bind()

    # ---- settings: one morning moment + a repeat interval, no time list ----
    op.add_column('user_settings', sa.Column('morning_time', sa.String(5),
                                             server_default='09:00', nullable=False))
    op.add_column('user_settings', sa.Column('nag_interval_min', sa.Integer(),
                                             server_default='60', nullable=False))
    op.add_column('user_settings', sa.Column('last_morning_at', sa.DateTime(), nullable=True))
    op.add_column('user_settings', sa.Column('last_notified_at', sa.DateTime(), nullable=True))
    # read everything first: SQLite does not like a table changing while its
    # own SELECT is still open
    for row in conn.execute(sa.text(
            "SELECT user_id, remind_mode, remind_times, remind_interval_min, "
            "last_reminded_at FROM user_settings")).fetchall():
        try:
            times = json.loads(row.remind_times or "[]")
        except (TypeError, ValueError):
            times = []
        nag = row.remind_interval_min if row.remind_mode == "interval" else 60
        conn.execute(sa.text(
            "UPDATE user_settings SET morning_time=:m, nag_interval_min=:n, "
            "last_morning_at=:l, last_notified_at=:l WHERE user_id=:u"),
            {"m": times[0] if times else "09:00", "n": nag or 60,
             "l": row.last_reminded_at, "u": row.user_id})

    # ---- tasks: deadline clock + warning lead; plans become the user's wall-clock
    op.add_column('tasks', sa.Column('deadline_time', sa.String(5),
                                     server_default='23:59', nullable=False))
    op.add_column('tasks', sa.Column('notify_before_min', sa.Integer(),
                                     server_default='60', nullable=False))
    for row in conn.execute(sa.text(
            "SELECT t.id, t.schedule_type, t.reminder_time, t.deadline, t.start_at, "
            "u.tz AS tz FROM tasks t JOIN user_settings u ON u.user_id = t.user_id"
            )).fetchall():
        hh, mm = (int(x) for x in (row.reminder_time or "09:00").split(":"))
        # old behaviour: reminded at reminder_time for a 23:59 deadline — the
        # same lead read the new way
        lead = max(0, (23 * 60 + 59) - (hh * 60 + mm))
        deadline = _to_wall(row.deadline, row.tz)
        started = _to_wall(row.start_at, row.tz)
        clock = deadline.strftime("%H:%M") if (row.schedule_type == "once"
                                              and deadline) else "23:59"
        conn.execute(sa.text(
            "UPDATE tasks SET deadline_time=:c, notify_before_min=:l, deadline=:d, "
            "start_at=:s WHERE id=:i"),
            {"c": clock, "l": lead, "d": deadline, "s": started, "i": row.id})

    # ---- occurrences: reminder_at becomes the notice moment of the deadline
    with op.batch_alter_table('task_occurrences') as batch:
        batch.add_column(sa.Column('notify_at', sa.DateTime(), nullable=True))
    conn.execute(sa.text("UPDATE task_occurrences SET notify_at = reminder_at "
                         "WHERE notify_at IS NULL"))
    with op.batch_alter_table('task_occurrences') as batch:
        batch.drop_column('reminder_at')
        batch.alter_column('notify_at', existing_type=sa.DateTime(), nullable=False)

    # ---- columns the new model does not use any more
    with op.batch_alter_table('user_settings') as batch:
        batch.drop_column('remind_mode')
        batch.drop_column('remind_times')
        batch.drop_column('remind_interval_min')
        batch.drop_column('last_reminded_at')
    with op.batch_alter_table('tasks') as batch:
        batch.drop_column('reminder_time')


def downgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table('task_occurrences') as batch:
        batch.add_column(sa.Column('reminder_at', sa.DateTime(), nullable=True))
    conn.execute(sa.text("UPDATE task_occurrences SET reminder_at = notify_at "
                         "WHERE reminder_at IS NULL"))
    with op.batch_alter_table('task_occurrences') as batch:
        batch.drop_column('notify_at')

    op.add_column('tasks', sa.Column('reminder_time', sa.String(5),
                                     server_default='09:00', nullable=False))
    for row in conn.execute(sa.text("SELECT id, deadline_time, notify_before_min "
                                    "FROM tasks")):
        hh, mm = (int(x) for x in (row.deadline_time or "23:59").split(":"))
        mins = max(0, (hh * 60 + mm) - (row.notify_before_min or 0))
        conn.execute(sa.text("UPDATE tasks SET reminder_time=:r WHERE id=:i"),
                     {"r": f"{mins // 60:02d}:{mins % 60:02d}", "i": row.id})
        # plans go back to being naive instants; without the owner's zone here
        # they are read as UTC, which is how this revision understood them
    with op.batch_alter_table('tasks') as batch:
        batch.drop_column('deadline_time')
        batch.drop_column('notify_before_min')

    op.add_column('user_settings', sa.Column('remind_mode', sa.String(10),
                                             server_default='times', nullable=False))
    op.add_column('user_settings', sa.Column('remind_times', sa.JSON(),
                                             nullable=True))
    op.add_column('user_settings', sa.Column('remind_interval_min', sa.Integer(),
                                             server_default='120', nullable=False))
    op.add_column('user_settings', sa.Column('last_reminded_at', sa.DateTime(),
                                             nullable=True))
    for row in conn.execute(sa.text("SELECT user_id, morning_time, nag_interval_min, "
                                    "last_morning_at FROM user_settings")).fetchall():
        conn.execute(sa.text(
            "UPDATE user_settings SET remind_mode=:mode, remind_times=:times, "
            "remind_interval_min=:n, last_reminded_at=:l WHERE user_id=:u"),
            {"mode": "interval", "times": json.dumps([row.morning_time or "09:00"]),
             "n": row.nag_interval_min or 120, "l": row.last_morning_at,
             "u": row.user_id})
    with op.batch_alter_table('user_settings') as batch:
        batch.drop_column('morning_time')
        batch.drop_column('nag_interval_min')
        batch.drop_column('last_morning_at')
        batch.drop_column('last_notified_at')
