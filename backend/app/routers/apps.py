import json
import os
import re
import shutil
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app import backup, docker_ops, operations, tailscale_ops
from app.catalog import get_manifest, load_catalog, version_tuple
from app.database import get_db
from app.envfile import validate_value
from app.models import InstalledApp, Job
from app.routers.auth import require_admin

router = APIRouter(prefix="/api", tags=["apps"], dependencies=[Depends(require_admin)])


class InstallIn(BaseModel):
    settings: dict[str, str] = {}
    components: list[str] = []
    import_id: str | None = None  # from POST /api/imports
    import_passphrase: str | None = None


class ExportIn(BaseModel):
    passphrase: str


class RestoreIn(BaseModel):
    repo: str
    snapshot_id: str


MIN_PASSPHRASE = 8


def _check_passphrase(value: str | None) -> None:
    if not value or len(value) < MIN_PASSPHRASE:
        raise HTTPException(400, f"Die Passphrase braucht mindestens {MIN_PASSPHRASE} Zeichen")


def _manifest_or_404(app_id: str):
    manifest = get_manifest(app_id)
    if manifest is None:
        raise HTTPException(404, "Unbekannte App")
    return manifest


def _start(app_id: str, action: str, target, *args) -> dict:
    try:
        return {"job_id": operations.start_job(app_id, action, target, *args)}
    except operations.OperationError as exc:
        raise HTTPException(409, str(exc))


@router.get("/apps")
def list_apps(db: DbSession = Depends(get_db)):
    """Catalog plus install state, one entry per catalog app."""
    installed = {a.id: a for a in db.scalars(select(InstalledApp))}
    # Show-once credentials nobody has read yet (dialog closed before the job
    # finished): offer them on the app card until they are fetched once.
    pending = {
        job.app_id: job.id
        for job in db.scalars(
            select(Job).where(Job.credentials.is_not(None), Job.status != "running").order_by(Job.id)
        )
    }
    status = tailscale_ops.status()
    result = []
    for manifest in load_catalog().values():
        app = installed.get(manifest.id)
        entry = {
            "id": manifest.id,
            "name": manifest.name,
            "description": manifest.description,
            "catalog_version": manifest.version,
            "port": manifest.port,
            "has_icon": bool(manifest.icon),
            "has_backup": manifest.backup is not None,
            "settings": [s.model_dump() for s in manifest.env.settings],
            "components": {k: {"label": c.label, "description": c.description, "default": c.default}
                           for k, c in manifest.components.items()},
            "installed": app is not None,
            "busy": operations.is_busy(manifest.id),
            # Left behind by "remove, keep data": a new install reuses it.
            "data_kept": app is None and (operations.app_dir(manifest.id) / ".env").exists(),
            "pending_credentials_job": pending.get(manifest.id),
        }
        if app:
            states = docker_ops.ps(operations.app_dir(manifest.id))
            entry.update({
                "version": app.version,
                "update_available": version_tuple(manifest.version) > version_tuple(app.version),
                "enabled_components": [c for c in app.components.split(",") if c],
                "url": tailscale_ops.app_url(manifest.port, status),
                "services": [s.__dict__ for s in states],
                "healthy": docker_ops.all_healthy(states),
            })
        result.append(entry)
    return result


@router.post("/apps/{app_id}/install")
def install(app_id: str, body: InstallIn):
    manifest = _manifest_or_404(app_id)
    unknown = set(body.components) - set(manifest.components)
    if unknown:
        raise HTTPException(400, f"Unbekannte Komponenten: {', '.join(sorted(unknown))}")
    allowed = {s.name for s in manifest.env.settings}
    try:
        for name, value in body.settings.items():
            if name not in allowed:
                raise ValueError(f"Unbekannte Einstellung {name}")
            validate_value(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if body.import_id:
        if not re.fullmatch(r"[0-9a-f]{32}", body.import_id):
            raise HTTPException(400, "Ungültige Import-ID")
        _check_passphrase(body.import_passphrase)
        if manifest.backup is None:
            raise HTTPException(400, f"{manifest.name} hat keine Daten, die sich importieren liessen")
    return _start(app_id, "install", operations.install, app_id, body.settings, body.components,
                  body.import_id, body.import_passphrase)


@router.post("/apps/{app_id}/update")
def update(app_id: str):
    _manifest_or_404(app_id)
    return _start(app_id, "update", operations.update, app_id)


class UninstallIn(BaseModel):
    delete_data: bool = False
    export_passphrase: str | None = None  # export first, then remove


@router.post("/apps/{app_id}/uninstall")
def uninstall(app_id: str, body: UninstallIn):
    _manifest_or_404(app_id)
    if body.export_passphrase is not None:
        _check_passphrase(body.export_passphrase)
    return _start(app_id, "uninstall", operations.uninstall, app_id, body.delete_data, body.export_passphrase)


@router.post("/apps/{app_id}/export")
def export(app_id: str, body: ExportIn, db: DbSession = Depends(get_db)):
    manifest = _manifest_or_404(app_id)
    if db.get(InstalledApp, app_id) is None:
        raise HTTPException(404, "Nicht installiert")
    if manifest.backup is None:
        raise HTTPException(400, f"{manifest.name} hat keine Daten auf dem Server")
    _check_passphrase(body.passphrase)
    return _start(app_id, "export", operations.export, app_id, body.passphrase)


@router.get("/apps/{app_id}/snapshots")
def app_snapshots(app_id: str, db: DbSession = Depends(get_db)):
    _manifest_or_404(app_id)
    if db.get(InstalledApp, app_id) is None:
        raise HTTPException(404, "Nicht installiert")
    return backup.list_snapshots(app_id)


@router.post("/apps/{app_id}/restore")
def restore(app_id: str, body: RestoreIn, db: DbSession = Depends(get_db)):
    _manifest_or_404(app_id)
    if db.get(InstalledApp, app_id) is None:
        raise HTTPException(404, "Nicht installiert")
    known = {str(backup.safety_repo())} | {str(backup.target_repo(t)) for t in backup.list_targets()}
    if body.repo not in known:  # never restic against an arbitrary path from the request
        raise HTTPException(400, "Unbekanntes Backup-Ziel")
    if not re.fullmatch(r"[0-9a-f]{8,64}", body.snapshot_id):
        raise HTTPException(400, "Ungültige Snapshot-ID")
    return _start(app_id, "restore", operations.restore, app_id, body.repo, body.snapshot_id)


@router.get("/exports/{name}")
def download_export(name: str):
    if not re.fullmatch(r"[a-z0-9_-]+-[0-9A-Za-z.x]+-\d{4}-\d{2}-\d{2}-\d{4}\.overhub", name):
        raise HTTPException(404)
    path = backup.exports_dir() / name
    if not path.is_file():
        raise HTTPException(404, "Export nicht mehr vorhanden (Exporte werden nach 24 Stunden gelöscht)")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.post("/imports")
def upload_import(file: UploadFile):
    """Store an uploaded export for a following install with import_id."""
    if not (file.filename or "").endswith(backup.EXPORT_SUFFIX):
        raise HTTPException(400, "Bitte eine .overhub-Datei aus einem OverHub-Export wählen")
    folder = backup.imports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for old in folder.iterdir():  # abandoned uploads
        if old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    import_id = uuid.uuid4().hex
    path = folder / f"{import_id}{backup.EXPORT_SUFFIX}"
    with open(path, "wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    os.chmod(path, 0o600)
    return {"import_id": import_id, "size": path.stat().st_size}


@router.post("/apps/{app_id}/start")
def start(app_id: str):
    _manifest_or_404(app_id)
    return _start(app_id, "start", operations.start, app_id)


@router.post("/apps/{app_id}/stop")
def stop(app_id: str):
    _manifest_or_404(app_id)
    return _start(app_id, "stop", operations.stop, app_id)


@router.get("/apps/{app_id}/logs")
def logs(app_id: str, service: str | None = None, tail: int = 200, db: DbSession = Depends(get_db)):
    _manifest_or_404(app_id)
    if db.get(InstalledApp, app_id) is None:
        raise HTTPException(404, "Nicht installiert")
    return {"logs": docker_ops.logs(operations.app_dir(app_id), service, min(max(tail, 10), 2000))}


@router.get("/jobs/{job_id}")
def job(job_id: int, db: DbSession = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404)
    credentials = None
    if job.status != "running" and job.credentials:
        # Show-once: the first read after the job finished gets them, then they are gone.
        credentials = json.loads(job.credentials)
        job.credentials = None
        db.commit()
    return {
        "id": job.id,
        "app_id": job.app_id,
        "action": job.action,
        "status": job.status,
        "log": job.log,
        "credentials": credentials,
        "result": json.loads(job.result) if job.result else None,
    }
