"""SQLAlchemy 2.x ORM tables.

Naming mirrors docs/diagrams/er-diagram.mmd. All datetime columns store
timezone-aware UTC (SQLite: naive UTC; PostgreSQL: timestamptz).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC in DB


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    language: Mapped[str] = mapped_column(String(5), default="ru")
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # wizard state


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(10))
    platform_user_id: Mapped[str] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    reminders_on: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_mode: Mapped[str] = mapped_column(String(10), default="times")
    remind_times: Mapped[list] = mapped_column(JSON, default=lambda: ["09:00"])
    remind_interval_min: Mapped[int] = mapped_column(Integer, default=120)
    quiet_start: Mapped[str] = mapped_column(String(5), default="23:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="07:00")
    location_ttl_hours: Mapped[int] = mapped_column(Integer, default=6)
    tz: Mapped[str] = mapped_column(String(64), default="UTC")
    notify_platforms: Mapped[str] = mapped_column(String(10), default="all")
    stats_format: Mapped[str] = mapped_column(String(10), default="both")
    stats_horizon_days: Mapped[int] = mapped_column(Integer, default=7)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # last digest state: pending reminder awaiting location answer
    pending_reminder: Mapped[bool] = mapped_column(Boolean, default=False)


class CurrentLocation(Base):
    __tablename__ = "current_location"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    set_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    schedule_type: Mapped[str] = mapped_column(String(20))
    schedule_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reminder_time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM user tz
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskOccurrence(Base):
    __tablename__ = "task_occurrences"
    __table_args__ = (UniqueConstraint("task_id", "due_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    reminder_at: Mapped[datetime] = mapped_column(DateTime)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class Completion(Base):
    __tablename__ = "completions"
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("task_occurrences.id", ondelete="CASCADE"), primary_key=True
    )
    done_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_via: Mapped[str] = mapped_column(String(10), default="")


class LinkCode(Base):
    __tablename__ = "link_codes"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_by_platform: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class LinkAudit(Base):
    __tablename__ = "link_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))  # link|unlink|merge
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
