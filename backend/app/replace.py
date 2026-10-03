"""Replace a device: rebuild a fresh OverHub from the backup of an old one.

The old backup is read from a folder below /mnt/overhub (USB disk) or from an
NAS per SFTP (login typed in; no target exists yet on the fresh device).

The old OverHub's own snapshot (app "_overhub") holds its database and every
app's .env. From it the new device takes over
- users and passwords (the old login applies again),
- backup targets (an NAS with its password) and the recovery key (the key
  the user typed in),
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
from app.bootstrap import grant_admins
from app.models import OVERHUB_APP, BackupTarget, InstalledApp, Session, User, UserRole


class ReplaceError(Exception):
    pass


def repo_for(location: str) -> Path | str:
    if backup.is_sftp(location):
        return location.rstrip("/") + "/overhub"
    return Path(location.rstrip("/")) / "overhub"


def scan(location: str, key: str, password: str | None = None) -> list[dict]:
    """OverHub snapshots in <location>/overhub, readable with key. Newest first.
    location: a folder below /mnt/overhub, or an SFTP location (with password)."""
    with backup.sftp_login(location, password):
        return _scan(location, key, password)


def _scan(location: str, key: str, password: str | None) -> list[dict]:
    repo = repo_for(location)
    if backup.is_sftp(location):
        problem = backup.sftp_check(location, password or "")
        if problem:
            raise ReplaceError(problem)
        result = backup.restic(repo, "cat", "config", timeout=120, password=key)
        if not result.ok:
            if "wrong password" in result.stderr + result.output:
                raise ReplaceError("Der Wiederherstellungs-Schlüssel passt nicht zu diesem Backup")
            raise ReplaceError(f"In {backup.sftp_fields(location)['path']} auf dem NAS liegt kein OverHub-Backup")
    else:
        try:
            location = backup.check_location(location)
        except ValueError as exc:
            raise ReplaceError(str(exc))
        if not backup.target_available(BackupTarget(name="scan", location=location), writable=False, fresh=True):
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


def _latest_app_snapshot(repo: Path | str, app_id: str, key: str) -> str | None:
    snaps = sorted(backup.snapshots(repo, app_id, password=key), key=lambda s: s.get("time") or "")
    return (snaps[-1].get("short_id") or snaps[-1]["id"][:8]) if snaps else None


def run(log, location: str, key: str, snapshot_id: str, password: str | None = None) -> None:
    with backup.sftp_login(location, password):
        _run(log, location, key, snapshot_id, password)


def _run(log, location: str, key: str, snapshot_id: str, password: str | None) -> None:
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
            tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            roles = con.execute(
                "select u.username, r.app_id, r.role from user_role r join user u on u.id = r.user_id"
            ).fetchall() if "user_role" in tables else None  # backups of OverHub < 0.4.0 have no roles
            target_columns = {r[1] for r in con.execute("pragma table_info(backup_target)")}
            targets = con.execute(  # NAS passwords since OverHub 0.6.0
                "select name, location, password from backup_target" if "password" in target_columns
                else "select name, location, null from backup_target"
            ).fetchall()
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

        # The source becomes a target too; its password is the one just typed in
        # (proven to work, the old database may hold an outdated one).
        source = location.rstrip("/")
        wanted = [(n, l, password if os.path.normpath(l) == os.path.normpath(source) and password else pw)
                  for n, l, pw in targets]
        if os.path.normpath(source) not in {os.path.normpath(l) for _, l, _ in wanted}:
            wanted.append(("NAS" if backup.is_sftp(source) else "Backup-Disk", source, password))
        db = SessionLocal()
        try:
            known = {os.path.normpath(t.location) for t in db.query(BackupTarget)}
            for name, loc, pw in wanted:
                if os.path.normpath(loc) not in known:
                    db.add(BackupTarget(name=name, location=loc, password=pw if backup.is_sftp(loc) else None))
                    known.add(os.path.normpath(loc))
            db.commit()
        finally:
            db.close()
        log(f"Backup-Ziele übernommen: {', '.join(n for n, _, _ in wanted)}")

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
            db.query(UserRole).delete()
            db.query(User).delete()
            new = {}
            for username, password_hash in users:
                new[username] = User(username=username, password_hash=password_hash)
                db.add(new[username])
            db.flush()
            if roles is None:  # before roles everyone was admin
                for user in new.values():
                    db.add(UserRole(user_id=user.id, app_id=OVERHUB_APP, role="admin"))
                db.flush()
                for app in db.query(InstalledApp):
                    grant_admins(db, app.id)
            else:
                for username, app_id, role in roles:
                    if username in new:
                        db.add(UserRole(user_id=new[username].id, app_id=app_id, role=role))
            db.commit()
        finally:
            db.close()
        log(f"Benutzer des alten OverHub übernommen ({', '.join(u for u, _ in users)}) — bitte mit dem alten Passwort anmelden")
        if failed:
            raise operations.OperationError(f"Wiederhergestellt: {', '.join(restored) or 'nichts'}; nicht möglich: {', '.join(failed)}")
        log(f"Gerät ersetzt: {', '.join(restored) or 'keine Apps'}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
