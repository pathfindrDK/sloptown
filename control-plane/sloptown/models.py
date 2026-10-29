"""Database schema. Mirrors the table list in docs/internal-api.md."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    sub: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="elev")  # laerer|elev|admin|system-admin
    org_id: Mapped[str] = mapped_column(String, default="")
    terms_version: Mapped[str | None] = mapped_column(String, nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = _now()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    sub: Mapped[str] = mapped_column(ForeignKey("users.sub"))
    partitioned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Launch-token sessions are scoped to one file with the role Miira asserted.
    file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    file_role: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LaunchJti(Base):
    __tablename__ = "launch_jti"
    jti: Mapped[str] = mapped_column(String, primary_key=True)
    exp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # Miira file id
    title: Mapped[str] = mapped_column(String, default="Ny Vibe")
    # "webapp": the template app with preview and publish. "scratch" ("fra bunden"):
    # an empty workspace for code that is never served on the web (Arduino, scripts).
    kind: Mapped[str] = mapped_column(String, default="webapp", server_default="webapp")
    slug: Mapped[str] = mapped_column(String, unique=True)
    owner_sub: Mapped[str] = mapped_column(ForeignKey("users.sub"))
    container_name: Mapped[str | None] = mapped_column(String, nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String, nullable=True)
    network_name: Mapped[str | None] = mapped_column(String, nullable=True)
    image_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="starting")  # starting|ready|stopped|error
    token_seed: Mapped[str | None] = mapped_column(String, nullable=True)  # carried over from the pool slot
    dev_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    runtime_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    published_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Public: the published app opens without a Miira login (edge.py answers anonymous
    # visitors with no identity). Owner only; the teacher answers for what the app shows.
    public: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    # Design system and colour palette (sloptown.design), mirrored from the Vibe's
    # agentd marker. None: a "fra bunden" project, or a webapp from before design
    # systems existed (Miira look, no palette until someone picks one).
    style: Mapped[str | None] = mapped_column(String, nullable=True)
    palette: Mapped[str | None] = mapped_column(String, nullable=True)
    active_turn_sub: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set by Stop while messages wait, so stopping never starts the next turn by itself.
    queue_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_active_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PoolSlot(Base):
    __tablename__ = "pool_slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_name: Mapped[str] = mapped_column(String, unique=True)
    volume_name: Mapped[str] = mapped_column(String)
    network_name: Mapped[str] = mapped_column(String)
    image_tag: Mapped[str] = mapped_column(String)
    # Per-slot secret seed; the dev token is derived from it (see tokens.py), never stored.
    token_seed: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="creating")  # creating|ready|claimed|discarding
    created_at: Mapped[datetime] = _now()


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    opencode_session_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="Ny tråd")
    owner_sub: Mapped[str] = mapped_column(String)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    author_sub: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|stopped|error
    # Queue order; "Send nu" moves a turn ahead of the others by giving it an earlier time.
    queued_at: Mapped[datetime] = _now()
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _now()


class MembersCache(Base):
    __tablename__ = "members_cache"
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = _now()


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _now()
    project_id: Mapped[str] = mapped_column(String)
    user_sub: Mapped[str | None] = mapped_column(String, nullable=True)  # build bucket
    member_id: Mapped[str | None] = mapped_column(String, nullable=True)  # app bucket
    bucket: Mapped[str] = mapped_column(String)  # build|app
    model: Mapped[str] = mapped_column(String)
    requested_model: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    nerfed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_usage_bucket_user_ts", "bucket", "user_sub", "ts"),
        Index("ix_usage_bucket_project_ts", "bucket", "project_id", "ts"),
    )


class BudgetSettingsRow(Base):
    __tablename__ = "budget_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _now()
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)


class BudgetOverride(Base):
    __tablename__ = "budget_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String)  # user|project
    subject_id: Mapped[str] = mapped_column(String)
    bucket: Mapped[str] = mapped_column(String)  # build|app
    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_override_lookup", "subject", "subject_id", "bucket", "week_start"),)


class BudgetNotification(Base):
    """One row per (subject, bucket, budget window, level) that has been announced to watchers."""

    __tablename__ = "budget_notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String)  # user|project
    subject_id: Mapped[str] = mapped_column(String)
    bucket: Mapped[str] = mapped_column(String)
    window_floor: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # week start, or the last reset
    level: Mapped[str] = mapped_column(String)  # warning:0.8 | nerfed | blocked
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ux_budget_notification", "subject", "subject_id", "bucket", "window_floor", "level", unique=True),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _now()
    actor_sub: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_kind: Mapped[str] = mapped_column(String)  # user|admin|system
    action: Mapped[str] = mapped_column(String)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)


class EdgeCode(Base):
    """One-time code that turns a studio session into a cookie on an app or preview host."""

    __tablename__ = "edge_codes"
    code_hash: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String)
    sub: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # app|preview
    view_as: Mapped[str | None] = mapped_column(String, nullable=True)  # preview only: owner|editor|viewer
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HostSession(Base):
    """Session cookie on an app or preview host (vibe_app / vibe_preview)."""

    __tablename__ = "host_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    sub: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # app|preview
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
