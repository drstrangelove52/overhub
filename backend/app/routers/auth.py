from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.database import get_db
from app.models import OVERHUB_APP, Session, User, UserRole
from app.security import generate_session_token, hash_password, session_expiry, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)

# The browser keeps the cookie as long as browsers allow; whether the session is
# still valid is decided here (90 days, extended whenever it is used — also by
# an app asking /api/sso/whoami, so using only OverCook keeps you logged in).
COOKIE_MAX_AGE = 400 * 86400


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def session_user(db: DbSession, token: str | None) -> User | None:
    """The user of a valid session; extends the session (sliding expiry)."""
    if not token:
        return None
    session = db.get(Session, token)
    if session is None:
        return None
    now = datetime.now(timezone.utc)
    expires = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        db.delete(session)
        db.commit()
        return None
    if expires - now < timedelta(seconds=settings.session_max_age_seconds) - timedelta(days=1):
        session.expires_at = session_expiry(settings.session_max_age_seconds)  # at most one write per day
        db.commit()
    return db.get(User, session.user_id)


def role_of(db: DbSession, user: User, app_id: str) -> str | None:
    row = db.get(UserRole, (user.id, app_id))
    return row.role if row else None


def current_user(
    db: DbSession = Depends(get_db),
    token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> User:
    user = session_user(db, token)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    return user


def require_admin(user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> User:
    if role_of(db, user, OVERHUB_APP) != "admin":
        raise HTTPException(403, "Nur für OverHub-Admins")
    return user


@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginIn, response: Response, db: DbSession = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    token = generate_session_token()
    db.add(Session(token=token, user_id=user.id, expires_at=session_expiry(settings.session_max_age_seconds)))
    db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return {"username": user.username}


@router.post("/logout")
def logout(
    response: Response,
    db: DbSession = Depends(get_db),
    token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    if token and (session := db.get(Session, token)):
        db.delete(session)
        db.commit()
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    return {"username": user.username, "is_admin": role_of(db, user, OVERHUB_APP) == "admin"}


@router.put("/me/password")
def change_password(body: PasswordIn, user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Aktuelles Passwort ist falsch")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Das neue Passwort braucht mindestens 8 Zeichen")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
