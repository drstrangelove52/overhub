import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app import backup, operations
from app.catalog import get_manifest
from app.config import settings
from app.database import get_db
from app.models import BackupStatus, BackupTarget, InstalledApp
from app.routers.auth import current_user

router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(current_user)])

USB_PATH = "/mnt/overhub/backup"


class TargetIn(BaseModel):
    name: str
    location: str


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("")
def overview(db: DbSession = Depends(get_db)):
    targets = [
        {"id": t.id, "name": t.name, "location": t.location, "available": backup.target_available(t)}
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


@router.post("/targets")
def add_target(body: TargetIn, db: DbSession = Depends(get_db)):
    location = body.location.strip().rstrip("/\\") or "/"
    path = Path(location)
    if not path.is_absolute() or ".." in path.parts:
        raise HTTPException(400, "Bitte einen absoluten Pfad angeben, z.B. /mnt/overhub/backup")
    data_dir = settings.data_dir.resolve()
    if path.resolve() == data_dir or data_dir in path.resolve().parents:
        raise HTTPException(400, "Das Ziel darf nicht im OverHub-Datenordner liegen (dieselbe Disk schützt nicht)")
    for existing in db.query(BackupTarget):
        if os.path.normpath(existing.location) == os.path.normpath(location):
            raise HTTPException(409, f"Dieser Ordner ist schon als Ziel „{existing.name}“ eingerichtet")
    target = BackupTarget(name=body.name.strip() or location, location=location)
    db.add(target)
    db.commit()
    backup.recovery_key()  # created now, so the UI can show it right away
    return {"id": target.id, "available": backup.target_available(target)}


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


@router.post("/key/ack")
def acknowledge_key():
    backup.set_setting("backup_key_acknowledged", "1")
    return {"ok": True}


@router.post("/run")
def run_now():
    if not [t for t in backup.list_targets() if backup.target_available(t)]:
        raise HTTPException(409, "Kein Backup-Ziel verfügbar")
    try:
        return {"job_id": operations.start_job("_backup", "backup", lambda log: backup.backup_all(log, "manual"))}
    except operations.OperationError as exc:
        raise HTTPException(409, str(exc))
