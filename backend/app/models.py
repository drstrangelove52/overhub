from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))


OVERHUB_APP = "_overhub"  # role "admin" here = may manage OverHub; apps: "user" or "admin"


class UserRole(Base):
    """Role of a user per app (contract rule 9). No row = no access."""

    __tablename__ = "user_role"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class Session(Base):
    __tablename__ = "session"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstalledApp(Base):
    __tablename__ = "installed_app"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # catalog id
    version: Mapped[str] = mapped_column(String(32))
    components: Mapped[str] = mapped_column(String(255), default="")  # comma-separated
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupTarget(Base):
    """A restic repository location. 3a: a host directory (USB disk mount point
    or any path); SFTP and S3 follow later."""

    __tablename__ = "backup_target"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(255))  # directory; the repo lives in <location>/overhub
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupStatus(Base):
    """Last result per app (and "_overhub" for OverHub itself) for the UI warnings."""

    __tablename__ = "backup_status"

    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Job(Base):
    """A long-running action (install/update/start/stop) with its own log."""

    __tablename__ = "job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|failed
    log: Mapped[str] = mapped_column(Text, default="")
    # Show-once credentials (JSON). Handed out by the first GET after success, then cleared.
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-secret output that stays readable (e.g. {"download": "<export file>"}).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
