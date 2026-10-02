"""Create the OverHub admin on first start (same rule as the apps: only while
no user exists, so a password changed in the UI is never overwritten)."""
from sqlalchemy import func, select

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def init_db() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if settings.admin_password and db.scalar(select(func.count()).select_from(User)) == 0:
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
            db.commit()
            print(f"bootstrap: created admin '{settings.admin_username}'")
    finally:
        db.close()
