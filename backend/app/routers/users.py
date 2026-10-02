"""Central users (contract rules 8/9): who may use which app with which role,
and the single sign-on endpoint the apps ask."""
import re

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app import tailscale_ops
from app.catalog import get_manifest
from app.config import settings
from app.database import get_db
from app.models import OVERHUB_APP, InstalledApp, Session, User, UserRole
from app.routers.auth import current_user, require_admin, role_of, session_user
from app.security import hash_password

router = APIRouter(prefix="/api", tags=["users"])

APP_ROLES = {"user", "admin"}
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")


class UserIn(BaseModel):
    username: str
    password: str
    roles: dict[str, str | None] = {}  # app id ("_overhub" for OverHub) -> "user"/"admin"/None


class UserUpdate(BaseModel):
    password: str | None = None
    roles: dict[str, str | None] | None = None


def _roles(db: DbSession, user: User) -> dict[str, str]:
    return {r.app_id: r.role for r in db.scalars(select(UserRole).where(UserRole.user_id == user.id))}


def _overhub_admins(db: DbSession) -> int:
    return len(db.scalars(select(UserRole).where(UserRole.app_id == OVERHUB_APP, UserRole.role == "admin")).all())


def _apply_roles(db: DbSession, user: User, roles: dict[str, str | None]) -> None:
    valid_apps = {OVERHUB_APP} | {a.id for a in db.scalars(select(InstalledApp))}
    for app_id, role in roles.items():
        if app_id not in valid_apps:
            raise HTTPException(400, f"Unbekannte App {app_id}")
        allowed = {"admin"} if app_id == OVERHUB_APP else APP_ROLES
        if role is not None and role not in allowed:
            raise HTTPException(400, f"Ungültige Rolle {role} für {app_id}")
        row = db.get(UserRole, (user.id, app_id))
        if role is None:
            if row:
                db.delete(row)
        elif row:
            row.role = role
        else:
            db.add(UserRole(user_id=user.id, app_id=app_id, role=role))


def _out(db: DbSession, user: User) -> dict:
    return {"id": user.id, "username": user.username, "roles": _roles(db, user)}


@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(db: DbSession = Depends(get_db)):
    return [_out(db, u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", dependencies=[Depends(require_admin)])
def create_user(body: UserIn, db: DbSession = Depends(get_db)):
    username = body.username.strip().lower()
    if not USERNAME.match(username):
        raise HTTPException(400, "Benutzername: 2–32 Zeichen, Kleinbuchstaben, Ziffern, . _ -")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, f"Benutzer {username} gibt es schon")
    if len(body.password) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen")
    user = User(username=username, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    _apply_roles(db, user, body.roles)
    db.commit()
    return _out(db, user)


@router.put("/users/{user_id}", dependencies=[Depends(require_admin)])
def update_user(user_id: int, body: UserUpdate, db: DbSession = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404)
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen")
        user.password_hash = hash_password(body.password)
        db.query(Session).filter(Session.user_id == user.id).delete()  # a reset password logs out everywhere
    if body.roles is not None:
        was_admin = role_of(db, user, OVERHUB_APP) == "admin"
        _apply_roles(db, user, body.roles)
        db.flush()
        if was_admin and _overhub_admins(db) == 0:
            db.rollback()
            raise HTTPException(400, "Es muss mindestens einen OverHub-Admin geben")
    db.commit()
    return _out(db, user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, me: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404)
    if user.id == me.id:
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/my-apps")
def my_apps(user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Installed apps this user may open (the page non-admins see)."""
    roles = _roles(db, user)
    status = tailscale_ops.status()
    result = []
    for app in db.scalars(select(InstalledApp).order_by(InstalledApp.id)):
        manifest = get_manifest(app.id)
        if manifest and app.id in roles:
            result.append({
                "id": app.id, "name": manifest.name, "description": manifest.description,
                "has_icon": bool(manifest.icon), "role": roles[app.id],
                "url": tailscale_ops.app_url(manifest.port, status),
            })
    return result


@router.get("/apps/{app_id}/icon")
def icon(app_id: str):
    """App icons are not secret: shown on the admin page and on "Meine Apps"."""
    manifest = get_manifest(app_id)
    if manifest is None or not manifest.icon:
        raise HTTPException(404)
    return FileResponse(manifest.dir / manifest.icon)


@router.get("/sso/whoami")
def whoami(
    app: str = Query(...),
    db: DbSession = Depends(get_db),
    token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    """Asked by an app's backend with the browser's OverHub cookie (contract rule 8).
    200 {username, role} · 401 not logged in · 403 no access to this app."""
    user = session_user(db, token)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    role = role_of(db, user, app)
    if role not in APP_ROLES:
        raise HTTPException(403, "Kein Zugriff auf diese App")
    return {"username": user.username, "role": role}
