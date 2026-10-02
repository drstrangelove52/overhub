"""Create the OverHub admin on first start (same rule as the apps: only while
no user exists, so a password changed in the UI is never overwritten)."""
from sqlalchemy import func, inspect, select, text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import OVERHUB_APP, InstalledApp, User, UserRole
from app.security import hash_password


# Columns added after a table first shipped. create_all() only creates missing
# tables, so existing installs get them here. (table, column, SQL type)
_ADDED_COLUMNS = [("job", "result", "TEXT")]


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            if column not in {c["name"] for c in inspector.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def grant_admins(db, app_id: str) -> None:
    """Every OverHub admin becomes admin of app_id unless a role was set already."""
    admins = db.scalars(select(UserRole.user_id).where(UserRole.app_id == OVERHUB_APP, UserRole.role == "admin")).all()
    for user_id in admins:
        if db.get(UserRole, (user_id, app_id)) is None:
            db.add(UserRole(user_id=user_id, app_id=app_id, role="admin"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    db = SessionLocal()
    try:
        if settings.admin_password and db.scalar(select(func.count()).select_from(User)) == 0:
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
            db.commit()
            print(f"bootstrap: created admin '{settings.admin_username}'")
        # Before roles existed (OverHub < 0.4.0) every user was an admin: keep it that way.
        if db.scalar(select(func.count()).select_from(UserRole)) == 0:
            for user in db.scalars(select(User)):
                db.add(UserRole(user_id=user.id, app_id=OVERHUB_APP, role="admin"))
            db.flush()
            for app in db.scalars(select(InstalledApp)):
                grant_admins(db, app.id)
        db.commit()
    finally:
        db.close()
