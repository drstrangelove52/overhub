import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app import docker_ops, operations, tailscale_ops
from app.catalog import get_manifest, load_catalog, version_tuple
from app.database import get_db
from app.envfile import validate_value
from app.models import InstalledApp, Job
from app.routers.auth import current_user

router = APIRouter(prefix="/api", tags=["apps"], dependencies=[Depends(current_user)])


class InstallIn(BaseModel):
    settings: dict[str, str] = {}
    components: list[str] = []


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


@router.get("/apps/{app_id}/icon")
def icon(app_id: str):
    manifest = _manifest_or_404(app_id)
    if not manifest.icon:
        raise HTTPException(404)
    return FileResponse(manifest.dir / manifest.icon)


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
    return _start(app_id, "install", operations.install, app_id, body.settings, body.components)


@router.post("/apps/{app_id}/update")
def update(app_id: str):
    _manifest_or_404(app_id)
    return _start(app_id, "update", operations.update, app_id)


class UninstallIn(BaseModel):
    delete_data: bool = False


@router.post("/apps/{app_id}/uninstall")
def uninstall(app_id: str, body: UninstallIn):
    _manifest_or_404(app_id)
    return _start(app_id, "uninstall", operations.uninstall, app_id, body.delete_data)


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
    }
