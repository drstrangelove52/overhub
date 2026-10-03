import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app import backup, operations, replace
from app.catalog import get_manifest
from app.config import settings
from app.database import get_db
from app.models import BackupStatus, BackupTarget, InstalledApp
from app.routers.auth import require_admin

router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_admin)])

USB_PATH = "/mnt/overhub/backup"


class TargetIn(BaseModel):
    name: str
    location: str = ""  # folder targets
    kind: str = "dir"  # dir | sftp
    host: str = ""  # sftp only
    user: str = ""
    password: str = ""
    path: str = ""


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("")
def overview(db: DbSession = Depends(get_db)):
    targets = [
        {"id": t.id, "name": t.name, "location": t.location, "available": backup.target_available(t),
         **({"kind": "sftp", **backup.sftp_fields(t.location)} if backup.is_sftp(t.location) else {"kind": "dir"})}
        for t in db.query(BackupTarget).order_by(BackupTarget.id)
    ]
    apps = []
    ids = [a.id for a in db.query(InstalledApp).order_by(InstalledApp.id)
           if (m := get_manifest(a.id)) and m.backup] + [backup.OVERHUB_ID]
    for app_id in ids:
        row = db.get(BackupStatus, app_id)
        apps.append({
            "id": app_id,
            "name": "OverHub" if app_id == backup.OVERHUB_ID else get_manifest(app_id).name,
            "last_success_at": _iso(row.last_success_at) if row else None,
            "last_error": row.last_error if row else None,
            "last_error_at": _iso(row.last_error_at) if row else None,
        })
    return {
        "targets": targets,
        "apps": apps,
        "overdue": backup.overdue_apps(),
        "key_acknowledged": backup.get_setting("backup_key_acknowledged") == "1",
        "running": operations.is_busy("_backup"),
        "usb_path": USB_PATH,
    }


def _resolve(body: TargetIn, db: DbSession, current: BackupTarget | None = None) -> tuple[str, str | None]:
    """Checked location and password for a new or edited target; nothing is
    stored when the input is wrong. Editing an SFTP target with an empty
    password field keeps the stored password."""
    try:
        if body.kind == "sftp":
            location = backup.sftp_location(body.user, body.host, body.path)
        else:
            location = backup.check_location(body.location)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    for existing in db.query(BackupTarget):
        if existing is not current and os.path.normpath(existing.location) == os.path.normpath(location):
            raise HTTPException(409, f"Dieser Ordner ist schon als Ziel „{existing.name}“ eingerichtet")
    if body.kind != "sftp":
        return location, None
    password = body.password or (current.password if current is not None and backup.is_sftp(current.location) else "")
    if not password:
        raise HTTPException(400, "Passwort fehlt")
    problem = backup.sftp_check(location, password)
    if problem:
        raise HTTPException(400, problem)
    return location, password


@router.post("/targets")
def add_target(body: TargetIn, db: DbSession = Depends(get_db)):
    location, password = _resolve(body, db)
    target = BackupTarget(name=body.name.strip() or location, location=location, password=password)
    db.add(target)
    db.commit()
    backup.recovery_key()  # created now, so the UI can show it right away
    available = backup.target_available(target, fresh=True)
    # Saved first: restic finds an SFTP target's password in the database.
    if available and backup.foreign_repo(backup.target_repo(target)):
        db.delete(target)
        db.commit()
        raise HTTPException(409, backup.foreign_repo_message())
    return {"id": target.id, "available": available}


@router.put("/targets/{target_id}")
def update_target(target_id: int, body: TargetIn, db: DbSession = Depends(get_db)):
    """Name, and for an NAS server, user, password and folder. A new folder
    starts a new backup history there; the old backups stay where they are."""
    target = db.get(BackupTarget, target_id)
    if target is None:
        raise HTTPException(404)
    location, password = _resolve(body, db, target)
    before = (target.name, target.location, target.password)
    target.name, target.location, target.password = body.name.strip() or location, location, password
    db.commit()
    backup._available_cache.clear()
    available = backup.target_available(target, fresh=True)
    if location != before[1] and available and backup.foreign_repo(backup.target_repo(target)):
        target.name, target.location, target.password = before
        db.commit()
        raise HTTPException(409, backup.foreign_repo_message())
    return {"id": target.id, "available": available}


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, db: DbSession = Depends(get_db)):
    target = db.get(BackupTarget, target_id)
    if target is None:
        raise HTTPException(404)
    db.delete(target)  # the repository on the disk stays untouched
    db.commit()
    return {"ok": True}


@router.get("/key")
def key():
    return {"key": backup.recovery_key()}


class KeyAckIn(BaseModel):
    confirm: str  # the last characters of the key, typed back in


@router.post("/key/ack")
def acknowledge_key(body: KeyAckIn):
    # Typing the end of the key back in proves it was actually stored somewhere —
    # a plain "I saved it" button got clicked without saving (seen in testing).
    confirm = body.confirm.strip()
    if len(confirm) < 6 or not backup.recovery_key().endswith(confirm):
        raise HTTPException(400, "Die eingegebenen Zeichen stimmen nicht mit dem Ende des Schlüssels überein")
    backup.set_setting("backup_key_acknowledged", "1")
    return {"ok": True}


@router.post("/run")
def run_now():
    if not [t for t in backup.list_targets() if backup.target_available(t, fresh=True)]:
        raise HTTPException(409, "Kein Backup-Ziel verfügbar")
    try:
        return {"job_id": operations.start_job("_backup", "backup", lambda log: backup.backup_all(log, "manual"))}
    except operations.OperationError as exc:
        raise HTTPException(409, str(exc))


# ---------- replace a device ----------

class ReplaceScanIn(BaseModel):
    key: str
    location: str = ""  # folder below /mnt/overhub
    kind: str = "dir"  # dir | sftp
    host: str = ""  # sftp only
    user: str = ""
    password: str = ""
    path: str = ""


class ReplaceIn(ReplaceScanIn):
    snapshot_id: str


def _replace_source(body: ReplaceScanIn) -> tuple[str, str | None]:
    if body.kind != "sftp":
        return body.location, None
    try:
        return backup.sftp_location(body.user, body.host, body.path), body.password
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/replace/scan")
def replace_scan(body: ReplaceScanIn):
    location, password = _replace_source(body)
    try:
        return replace.scan(location, body.key.strip(), password)
    except replace.ReplaceError as exc:
        raise HTTPException(400, str(exc))


@router.post("/replace")
def replace_run(body: ReplaceIn, db: DbSession = Depends(get_db)):
    if db.query(InstalledApp).count():
        raise HTTPException(409, "Auf diesem Gerät sind schon Apps installiert")
    location, password = _replace_source(body)
    try:
        replace.scan(location, body.key.strip(), password)  # same checks before starting the job
    except replace.ReplaceError as exc:
        raise HTTPException(400, str(exc))
    try:
        job_id = operations.start_job("_replace", "replace", replace.run, location, body.key.strip(), body.snapshot_id,
                                      password)
    except operations.OperationError as exc:
        raise HTTPException(409, str(exc))
    return {"job_id": job_id}
