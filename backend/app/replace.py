"""Replace a device: rebuild a fresh OverHub from the backup of an old one.

The old OverHub's own snapshot (app "_overhub") holds its database and every
app's .env. From it the new device takes over
- users and passwords (the old login applies again),
- backup targets and the recovery key (the key the user typed in),
- every installed app: installed in the current catalog version with its old
  secrets, then filled with the newest data snapshot from the same repository
  (data of an older app version may go into a newer one, contract rule 5).
"""
import os
import shutil
import sqlite3
from pathlib import Path

from app import backup, operations
from app.catalog import get_manifest
from app.config import settings
from app.database import SessionLocal
from app.envfile import read_env, write_env
from app.models import BackupTarget, InstalledApp, Session, User


class ReplaceError(Exception):
    pass


def repo_for(location: str) -> Path:
    return Path(location.rstrip("/")) / "overhub"


def scan(location: str, key: str) -> list[dict]:
    """OverHub snapshots in <location>/overhub, readable with key. Newest first."""
    target = BackupTarget(name="scan", location=location.rstrip("/"))
    if not backup.target_available(target):
        raise ReplaceError("Ordner nicht erreichbar. Ist die Backup-Disk eingesteckt?")
    repo = repo_for(location)
    if not (repo / "config").exists():
        raise ReplaceError(f"In {location} liegt kein OverHub-Backup")
    if not backup.restic(repo, "cat", "config", timeout=120, password=key).ok:
        raise ReplaceError("Der Wiederherstellungs-Schlüssel passt nicht zu diesem Backup")
    result = []
    for snap in backup.snapshots(repo, backup.OVERHUB_ID, password=key):
        sid = snap.get("short_id") or snap["id"][:8]
        meta = backup.read_meta(repo, sid, backup.OVERHUB_ID, password=key)
        result.append({
            "id": sid,
            "time": snap.get("time"),
            "overhub_version": meta.get("version"),
            "apps": meta.get("apps"),  # None for backups made before OverHub 0.3.1
        })
    return sorted(result, key=lambda s: s["time"] or "", reverse=True)


def _latest_app_snapshot(repo: Path, app_id: str, key: str) -> str | None:
    snaps = sorted(backup.snapshots(repo, app_id, password=key), key=lambda s: s.get("time") or "")
    return (snaps[-1].get("short_id") or snaps[-1]["id"][:8]) if snaps else None


def run(log, location: str, key: str, snapshot_id: str) -> None:
    db = SessionLocal()
    try:
        if db.query(InstalledApp).count():
            raise operations.OperationError("Auf diesem Gerät sind schon Apps installiert — Ersetzen geht nur auf einem frischen OverHub")
    finally:
        db.close()
    repo = repo_for(location)
    work = settings.data_dir / "restore" / backup.OVERHUB_ID
    try:
        try:
            backup.fetch(repo, snapshot_id, backup.OVERHUB_ID, work, password=key)
        except backup.BackupError as exc:
            raise operations.OperationError(str(exc))
        old_db = work / "overhub.db"
        if not old_db.exists():
            raise operations.OperationError("Die Sicherung enthält keine OverHub-Datenbank")
        log("Sicherung des alten OverHub geholt")

        con = sqlite3.connect(old_db)
        try:
            users = con.execute("select username, password_hash from user").fetchall()
            targets = con.execute("select name, location from backup_target").fetchall()
            apps = con.execute("select id, components from installed_app order by id").fetchall()
        finally:
            con.close()

        # Recovery key: the one that opened this backup is the key of all its repos.
        path = backup.key_file()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        backup.set_setting("backup_key_acknowledged", "1")
        log("Wiederherstellungs-Schlüssel übernommen")

        db = SessionLocal()
        try:
            known = {os.path.normpath(t.location) for t in db.query(BackupTarget)}
            wanted = list(targets)
            if os.path.normpath(location.rstrip("/")) not in {os.path.normpath(l) for _, l in wanted}:
                wanted.append(("Backup-Disk", location.rstrip("/")))
            for name, loc in wanted:
                if os.path.normpath(loc) not in known:
                    db.add(BackupTarget(name=name, location=loc))
                    known.add(os.path.normpath(loc))
            db.commit()
        finally:
            db.close()
        log(f"Backup-Ziele übernommen: {', '.join(n for n, _ in wanted)}")

        restored, failed = [], []
        for app_id, components in apps:
            manifest = get_manifest(app_id)
            if manifest is None:
                log(f"{app_id} gibt es im Katalog nicht mehr — übersprungen")
                failed.append(app_id)
                continue
            log(f"== {manifest.name}")
            old_env = work / "apps" / app_id / ".env"
            app_dir = operations.app_dir(app_id)
            app_dir.mkdir(parents=True, exist_ok=True)
            if old_env.exists():
                write_env(app_dir / ".env", read_env(old_env))  # old secrets: the data needs them
            try:
                operations.install(log, app_id, {}, [c for c in (components or "").split(",") if c])
                if manifest.backup:
                    sid = _latest_app_snapshot(repo, app_id, key)
                    if sid:
                        log(f"Spiele die neuesten Daten ein (Snapshot {sid}) …")
                        backup.restore_app(app_id, repo, sid, log, check=True)
                        operations.start(log, app_id)
                    else:
                        log(f"Keine Datensicherung von {manifest.name} gefunden — App startet leer")
                restored.append(manifest.name)
            except (operations.OperationError, backup.BackupError) as exc:
                log(f"FEHLER bei {manifest.name}: {exc}")
                failed.append(app_id)

        # Users last: from here on the old login applies (current sessions end).
        db = SessionLocal()
        try:
            db.query(Session).delete()
            db.query(User).delete()
            for username, password_hash in users:
                db.add(User(username=username, password_hash=password_hash))
            db.commit()
        finally:
            db.close()
        log(f"Benutzer des alten OverHub übernommen ({', '.join(u for u, _ in users)}) — bitte mit dem alten Passwort anmelden")
        if failed:
            raise operations.OperationError(f"Wiederhergestellt: {', '.join(restored) or 'nichts'}; nicht möglich: {', '.join(failed)}")
        log(f"Gerät ersetzt: {', '.join(restored) or 'keine Apps'}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
