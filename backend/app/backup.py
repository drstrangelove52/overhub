"""Backups with restic (Etappe 3a).

Per app the data is first staged into a plain directory and then snapshotted
by restic into every repository:

    /opt/overhub/staging/<app>/
        meta.json          app id, version, components, time
        dump.sql           output of the manifest's backup.dump command
        volumes/<v>.tar    contents of each volume in backup.volumes
        secrets.env        only `persistent: true` secrets (contract rule 7)

Repositories:
- the **safety repo** /opt/overhub/safety on the device itself: always there,
  holds the last states before updates so a failed update can roll back
  (does not protect against a disk failure);
- every configured **target** (USB disk, any directory, NAS per SFTP) at
  <location>/overhub: nightly backups, retention 7 daily / 4 weekly / 6 monthly.

All repositories share one password, the instance recovery key in
/opt/overhub/backup.key (shown to the admin once, to keep in a password manager).
"""
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import docker_ops, runner
from app.catalog import Manifest, get_manifest, load_catalog, version_tuple
from app.config import settings
from app.database import SessionLocal
from app.envfile import read_env
from app.models import BackupStatus, BackupTarget, InstalledApp, Setting, utcnow

TAR_IMAGE = "alpine:3.20"
RETENTION = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]
SAFETY_KEEP = ["--keep-last", "3"]
OVERHUB_ID = "_overhub"

# Staging dirs and repos are shared by nightly backups, pre-update backups and
# restores: one at a time.
_lock = threading.RLock()


class BackupError(Exception):
    pass


# ---------- paths, key, settings ----------

def safety_repo() -> Path:
    return settings.data_dir / "safety"


def staging_dir(app_id: str) -> Path:
    return settings.data_dir / "staging" / app_id


def key_file() -> Path:
    return settings.data_dir / "backup.key"


def recovery_key() -> str:
    """The instance key, created on first use (0600, never on a command line)."""
    path = key_file()
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def get_setting(key: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        return row.value if row else None
    finally:
        db.close()


def set_setting(key: str, value: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
        db.commit()
    finally:
        db.close()


# ---------- targets ----------

def is_sftp(location) -> bool:
    return str(location).startswith("sftp:")


def target_repo(target: BackupTarget) -> Path | str:
    if is_sftp(target.location):
        return target.location.rstrip("/") + "/overhub"
    return Path(target.location) / "overhub"


# ---------- SFTP (NAS) ----------
# restic talks SFTP through an ssh process. Logging in with a password needs
# sshpass (-e: password from $SSHPASS, never on a command line). The NAS's host
# key is remembered on first contact (accept-new) and checked from then on.

SFTP_LOCATION = re.compile(r"sftp:([A-Za-z0-9._-]+)@([A-Za-z0-9.-]+):(/[A-Za-z0-9._/ -]*)")


def sftp_location(user: str, host: str, path: str) -> str:
    """Normalised location, or ValueError with a message for the user."""
    path = "/" + path.strip().strip("/")
    location = f"sftp:{user.strip()}@{host.strip()}:{path}"
    if not SFTP_LOCATION.fullmatch(location) or ".." in path.split("/"):
        raise ValueError("Ungültige Angaben: Server z.B. nas.local, Benutzer ohne Leerzeichen, Ordner z.B. backup")
    return location


def _sftp_parts(location: str) -> tuple[str, str, str]:
    user, host, path = SFTP_LOCATION.fullmatch(location).groups()
    return user, host, path


def _ssh_options() -> list[str]:
    ssh_dir = settings.data_dir / "ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    return [
        "-o", f"UserKnownHostsFile={ssh_dir / 'known_hosts'}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "PubkeyAuthentication=no",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30",
    ]


def _sftp_target(repo) -> BackupTarget | None:
    for target in list_targets():
        if is_sftp(target.location) and str(repo).startswith(target.location.rstrip("/") + "/"):
            return target
    return None


# sshpass exit codes worth explaining
_SSHPASS_ERRORS = {
    5: "Anmeldung abgelehnt (Benutzer oder Passwort falsch)",
    6: "Der Server meldet einen anderen Schlüssel als beim ersten Kontakt — Ziel entfernen und neu einrichten",
}


def sftp_check(location: str, password: str) -> str | None:
    """None if the folder can be listed with these credentials, else a message.

    The commands go in on stdin, not with `sftp -b`: batch mode makes ssh skip
    the password prompt, so sshpass never gets to answer it. Without -b sftp
    does not exit non-zero when a command fails, hence the look at the output.
    """
    user, host, path = _sftp_parts(location)
    commands = settings.data_dir / "cache" / "sftp-check"
    commands.parent.mkdir(parents=True, exist_ok=True)
    commands.write_text(f'ls "{path}"\n')
    result = runner.run(
        ["sshpass", "-e", "sftp", *_ssh_options(), f"{user}@{host}"],
        env={"SSHPASS": password}, merge_stderr=True, timeout=60, stdin_path=commands,
    )
    if result.returncode in _SSHPASS_ERRORS:
        return _SSHPASS_ERRORS[result.returncode]
    if result.ok and not any(m in result.output for m in _SFTP_COMMAND_FAILED):
        return None
    return _explain_sftp(result.output, host, path)


# sftp output when a command failed after a successful login
_SFTP_COMMAND_FAILED = ("Can't ls", "not found", "No such file", "Permission denied")


# ssh/sftp messages -> what to check. sftp always ends with a generic
# "Connection closed", so the cause sits in an earlier line.
_SFTP_HINTS = [
    ("Could not resolve hostname", "Server-Name {host} unbekannt — IP-Adresse versuchen"),
    ("Connection refused", "{host} nimmt auf Port 22 keine Verbindung an — SFTP-Dienst aus oder Firewall des NAS"),
    ("timed out", "{host} antwortet nicht — Adresse richtig, Gerät eingeschaltet?"),
    ("No route to host", "{host} ist nicht erreichbar"),
    ("subsystem request failed", "Anmeldung ok, aber SFTP ist auf dem NAS nicht aktiv oder für den Benutzer nicht erlaubt"),
    ("Host key verification failed", _SSHPASS_ERRORS[6]),
    ("Permission denied", "Anmeldung abgelehnt (Benutzer oder Passwort falsch)"),
    ("No such file", "Ordner {path} gibt es auf dem NAS nicht (bei Synology: Name des freigegebenen Ordners)"),
    ("not found", "Ordner {path} gibt es auf dem NAS nicht (bei Synology: Name des freigegebenen Ordners)"),
]


def _explain_sftp(output: str, host: str, path: str) -> str:
    for needle, hint in _SFTP_HINTS:
        if needle.lower() in output.lower():
            return hint.format(host=host, path=path)
    lines = [l.strip() for l in output.splitlines() if l.strip() and l.strip() != "Connection closed"]
    detail = " / ".join(lines[-3:])[:300] or "Verbindung nach der Anmeldung beendet"
    return f"Keine SFTP-Verbindung zu {host}: {detail}"


def _real_mount(path: str) -> bool:
    """True if a real filesystem (not just the systemd automount point, which
    is `autofs`) is mounted exactly at path."""
    try:
        with open("/proc/self/mounts") as f:
            return any(
                (parts := line.split())[1] == path and parts[2] != "autofs" for line in f if len(line.split()) > 2
            )
    except OSError:
        return os.path.ismount(path)


def check_location(location: str) -> str:
    """Normalised backup location, or ValueError with a message for the user."""
    location = location.strip().rstrip("/\\")
    path = Path(location)
    root = settings.backup_root
    if not path.is_absolute() or ".." in path.parts or root not in path.parents:
        raise ValueError(
            f"Der Ordner muss unter {root}/ liegen (z.B. {root}/backup für die USB-Disk oder {root}/disk2 für eine "
            f"zweite Disk) — nur dort sieht OverHub die Disks des Geräts."
        )
    data_dir = settings.data_dir.resolve()
    if path.resolve() == data_dir or data_dir in path.resolve().parents:
        raise ValueError("Das Ziel darf nicht im OverHub-Datenordner liegen (dieselbe Disk schützt nicht)")
    return location


_available_cache: dict[tuple[str, bool], tuple[float, bool]] = {}
AVAILABLE_CACHE_SECONDS = 30


def target_available(target: BackupTarget, writable: bool = True, fresh: bool = False) -> bool:
    """writable=False: only reading is needed (taking over an old backup).

    Probing a USB automount point without a disk waits for the device timeout,
    so the answer is kept for 30 s (the UI asks every few seconds). Actions
    that write (backup, adding a target) pass fresh=True.
    """
    if is_sftp(target.location):
        writable = True  # one probe answers both
    key = (target.location, writable)
    hit = _available_cache.get(key)
    if not fresh and hit and hit[0] > time.monotonic():
        return hit[1]
    result = _probe(target, writable)
    _available_cache[key] = (time.monotonic() + AVAILABLE_CACHE_SECONDS, result)
    return result


def _probe(target: BackupTarget, writable: bool) -> bool:
    if is_sftp(target.location):
        return sftp_check(target.location, target.password or "") is None
    location = Path(target.location)
    try:
        os.listdir(location)  # triggers the automount of a plugged-in USB disk
    except OSError:
        return False
    if not writable:
        return True
    if not os.access(location, os.W_OK):
        return False
    # A USB disk that is not plugged in leaves the bare mount point behind:
    # writing there would silently fill the system disk instead.
    if settings.require_mounted_targets and not _real_mount(str(location)):
        return False
    return True


def list_targets() -> list[BackupTarget]:
    db = SessionLocal()
    try:
        return list(db.query(BackupTarget).order_by(BackupTarget.id))
    finally:
        db.close()


# ---------- restic ----------

def restic(repo: Path | str, *args: str, timeout: int = 3600, password: str | None = None) -> runner.Result:
    """password: defaults to the instance recovery key; exports use their own passphrase."""
    env = {"RESTIC_PASSWORD": password or recovery_key(), "RESTIC_CACHE_DIR": str(settings.data_dir / "cache")}
    extra: list[str] = []
    if is_sftp(repo):
        target = _sftp_target(repo)
        if target is None:
            return runner.Result(1, "", f"Kein Backup-Ziel für {repo}")
        user, host, _ = _sftp_parts(target.location)
        ssh = ["sshpass", "-e", "ssh", *_ssh_options(), f"{user}@{host}", "-s", "sftp"]
        extra = ["-o", "sftp.command=" + shlex.join(ssh)]
        env["SSHPASS"] = target.password or ""
    return runner.run(
        ["restic", "-r", str(repo), *args, *extra],
        env=env,
        merge_stderr=False,
        timeout=timeout,
    )


def ensure_repo(repo: Path | str) -> None:
    if restic(repo, "cat", "config", timeout=120).ok:
        return
    if not is_sftp(repo):  # over SFTP restic creates the folders itself
        repo.mkdir(parents=True, exist_ok=True)
    result = restic(repo, "init", timeout=120)
    if not result.ok:
        raise BackupError(f"Backup-Repository {repo} konnte nicht angelegt werden: {result.stderr or result.output}")


def snapshot(repo: Path, path: Path, tags: dict[str, str]) -> str:
    ensure_repo(repo)
    args = ["backup", str(path), "--host", "overhub", "--json"]
    for key, value in tags.items():
        args += ["--tag", f"{key}:{value}"]
    result = restic(repo, *args)
    if not result.ok:
        raise BackupError(f"restic backup nach {repo} fehlgeschlagen: {(result.stderr or result.output)[-1000:]}")
    for line in reversed(result.output.splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("message_type") == "summary":
            return data["snapshot_id"]
    raise BackupError("restic hat keine Snapshot-ID gemeldet")


def snapshots(repo: Path, app_id: str, password: str | None = None) -> list[dict]:
    result = restic(repo, "snapshots", "--json", "--host", "overhub", "--tag", f"app:{app_id}",
                    timeout=300, password=password)
    if not result.ok:
        return []
    try:
        return json.loads(result.output or "[]")
    except json.JSONDecodeError:
        return []


def read_meta(repo: Path, snapshot_id: str, app_id: str, password: str | None = None) -> dict:
    """meta.json of a snapshot without restoring it."""
    result = restic(repo, "dump", snapshot_id, str(staging_dir(app_id) / "meta.json"), timeout=300, password=password)
    try:
        return json.loads(result.output) if result.ok else {}
    except json.JSONDecodeError:
        return {}


def forget(repo: Path, policy: list[str]) -> None:
    restic(repo, "forget", "--host", "overhub", "--group-by", "paths", *policy, "--prune")


# ---------- staging ----------

def _service_running(app_dir: Path, service: str) -> bool:
    return any(s.service == service and s.state == "running" for s in docker_ops.ps(app_dir))


def stage_app(manifest: Manifest, log) -> Path:
    app_dir = settings.apps_dir / manifest.id
    stage = staging_dir(manifest.id)
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "volumes").mkdir(parents=True)

    db = SessionLocal()
    try:
        installed = db.get(InstalledApp, manifest.id)
        meta = {
            "app": manifest.id,
            "version": installed.version if installed else None,
            "components": installed.components if installed else "",
            "overhub": settings.version,
            "created": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()
    (stage / "meta.json").write_text(json.dumps(meta, indent=2))

    env = read_env(app_dir / ".env")
    persistent = [s.name for s in manifest.env.generated if s.persistent]
    for component in manifest.components.values():
        persistent += [s.name for s in component.env.generated if s.persistent]
    (stage / "secrets.env").write_text("".join(f"{k}={env[k]}\n" for k in persistent if k in env))
    os.chmod(stage / "secrets.env", 0o600)

    spec = manifest.backup
    if spec and spec.dump:
        if not _service_running(app_dir, spec.dump.service):
            raise BackupError(f"Dienst {spec.dump.service} läuft nicht — App zuerst starten")
        log(f"Datenbank-Dump ({spec.dump.service}) …")
        result = docker_ops.compose(
            app_dir, "exec", "-T", spec.dump.service, *shlex.split(spec.dump.command),
            stdout_path=stage / "dump.sql",
        )
        if not result.ok:
            raise BackupError(f"Dump fehlgeschlagen: {result.output[-1000:]}")
        log(f"Dump: {(stage / 'dump.sql').stat().st_size // 1024} KB")
    for volume in spec.volumes if spec else []:
        log(f"Volume {volume} …")
        result = runner.run([
            "docker", "run", "--rm",
            "-v", f"{manifest.id}_{volume}:/data:ro",
            "-v", f"{stage / 'volumes'}:/out",
            TAR_IMAGE, "tar", "-cf", f"/out/{volume}.tar", "-C", "/data", ".",
        ], timeout=3600)
        if not result.ok:
            raise BackupError(f"Volume {volume} konnte nicht gesichert werden: {result.output[-1000:]}")
    return stage


def stage_overhub(log) -> Path:
    """OverHub itself: database, its settings and every app's .env/compose.yml
    (needed to rebuild a device from scratch)."""
    stage = staging_dir(OVERHUB_ID)
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    db = SessionLocal()
    try:
        apps = [{"id": a.id, "version": a.version} for a in db.query(InstalledApp).order_by(InstalledApp.id)]
    finally:
        db.close()
    (stage / "meta.json").write_text(json.dumps({
        "app": OVERHUB_ID,
        "version": settings.version,
        "created": datetime.now(timezone.utc).isoformat(),
        "apps": apps,  # shown when replacing a device
    }, indent=2))
    src = sqlite3.connect(settings.data_dir / "overhub.db")
    dst = sqlite3.connect(stage / "overhub.db")
    try:
        src.backup(dst)  # consistent copy while OverHub keeps running
    finally:
        dst.close()
        src.close()
    for name in ("overhub.env", "compose.yml", ".env"):
        if (settings.data_dir / name).exists():
            shutil.copy2(settings.data_dir / name, stage / name)
    for app_dir in settings.apps_dir.glob("*"):
        for name in (".env", "compose.yml"):
            if (app_dir / name).exists():
                (stage / "apps" / app_dir.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(app_dir / name, stage / "apps" / app_dir.name / name)
    log("OverHub-Einstellungen gesammelt")
    return stage


# ---------- status ----------

def record(app_id: str, error: str | None) -> None:
    db = SessionLocal()
    try:
        row = db.get(BackupStatus, app_id) or BackupStatus(app_id=app_id)
        if error:
            row.last_error, row.last_error_at = error, utcnow()
        else:
            row.last_success_at, row.last_error = utcnow(), None
        db.merge(row)
        db.commit()
    finally:
        db.close()


# ---------- actions ----------

def backup_app(app_id: str, reason: str, log, safety: bool = False, targets: bool = True) -> dict[str, str]:
    """Stage and snapshot one app. Returns {repo: snapshot_id}."""
    with _lock:
        return _backup_app(app_id, reason, log, safety, targets)


def _backup_app(app_id: str, reason: str, log, safety: bool, targets: bool) -> dict[str, str]:
    manifest = get_manifest(app_id)
    if manifest.backup is None:
        log(f"{manifest.name}: keine Daten auf dem Server, nichts zu sichern")
        return {}
    stage = stage_app(manifest, log)
    tags = {"app": app_id, "reason": reason}
    done: dict[str, str] = {}
    try:
        if safety:
            done[str(safety_repo())] = snapshot(safety_repo(), stage, tags)
            forget(safety_repo(), SAFETY_KEEP)
            log("Sicherheitskopie auf dem Gerät erstellt")
        for target in list_targets() if targets else []:
            if not target_available(target, fresh=True):
                log(f"Ziel „{target.name}“ nicht verfügbar ({target.location}) — übersprungen")
                continue
            done[str(target_repo(target))] = snapshot(target_repo(target), stage, tags)
            log(f"Gesichert auf „{target.name}“")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return done


def backup_all(log, reason: str = "scheduled") -> None:
    """Every installed app plus OverHub itself onto every available target."""
    with _lock:
        _backup_all(log, reason)


def _backup_all(log, reason: str) -> None:
    targets = [t for t in list_targets() if target_available(t, fresh=True)]
    if not targets:
        raise BackupError("Kein Backup-Ziel verfügbar (z.B. USB-Disk nicht eingesteckt)")
    db = SessionLocal()
    try:
        app_ids = [a.id for a in db.query(InstalledApp).order_by(InstalledApp.id)]
    finally:
        db.close()
    failed = []
    for app_id in app_ids:
        if get_manifest(app_id) is None:
            continue
        try:
            log(f"== {get_manifest(app_id).name}")
            backup_app(app_id, reason, log)
            record(app_id, None)
        except BackupError as exc:
            log(f"FEHLER: {exc}")
            record(app_id, str(exc))
            failed.append(app_id)
    try:
        log("== OverHub")
        stage = stage_overhub(log)
        try:
            for target in targets:
                snapshot(target_repo(target), stage, {"app": OVERHUB_ID, "reason": reason})
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        record(OVERHUB_ID, None)
    except BackupError as exc:
        log(f"FEHLER: {exc}")
        record(OVERHUB_ID, str(exc))
        failed.append(OVERHUB_ID)
    for target in targets:
        forget(target_repo(target), RETENTION)
    log(f"Aufbewahrung angewendet ({' '.join(RETENTION)})")
    if failed:
        raise BackupError(f"Backup unvollständig: {', '.join(failed)}")


def fetch(repo: Path, snapshot_id: str, app_id: str, target: Path, password: str | None = None) -> dict:
    """Copy a snapshot's staged files into target. Returns its meta.json."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    result = restic(repo, "restore", f"{snapshot_id}:{staging_dir(app_id)}", "--target", str(target), password=password)
    if not result.ok:
        raise BackupError(f"Wiederherstellen aus {repo} fehlgeschlagen: {(result.stderr or result.output)[-500:]}")
    try:
        return json.loads((target / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def check_version(manifest: Manifest, meta: dict) -> None:
    """Data of an older app version may go into a newer one (migrations run on
    start), never the other way round (contract rule 5)."""
    if meta.get("app") not in (None, manifest.id):
        raise BackupError(f"Die Daten gehören zu {meta.get('app')}, nicht zu {manifest.name}")
    db = SessionLocal()
    try:
        app = db.get(InstalledApp, manifest.id)
        installed = app.version if app else manifest.version
    finally:
        db.close()
    data_version = meta.get("version")
    if data_version and version_tuple(data_version) > version_tuple(installed):
        raise BackupError(
            f"Die Daten stammen von {manifest.name} {data_version}, installiert ist {installed}. "
            f"Zuerst {manifest.name} auf {data_version} oder neuer aktualisieren."
        )


def apply_restore(app_id: str, source: Path, log) -> None:
    """Put staged data (from a snapshot or an import) into the app's volumes and
    database. Order: stop the app, refill the volumes, start only the database
    service, feed the dump. The caller starts the whole app afterwards."""
    manifest = get_manifest(app_id)
    spec = manifest.backup
    if spec is None:
        return
    app_dir = settings.apps_dir / app_id
    docker_ops.compose(app_dir, "stop")
    for volume in spec.volumes:
        if not (source / "volumes" / f"{volume}.tar").exists():
            log(f"Hinweis: Volume {volume} ist in den Daten nicht enthalten")
            continue
        result = runner.run([
            "docker", "run", "--rm",
            "-v", f"{app_id}_{volume}:/data",
            "-v", f"{source / 'volumes'}:/in:ro",
            TAR_IMAGE, "sh", "-c", f"find /data -mindepth 1 -delete && tar -xf /in/{volume}.tar -C /data",
        ], timeout=3600)
        if not result.ok:
            raise BackupError(f"Volume {volume} konnte nicht zurückgespielt werden: {result.output[-1000:]}")
        log(f"Volume {volume} zurückgespielt")
    if spec.restore and (source / "dump.sql").exists():
        service = spec.restore.service
        docker_ops.compose(app_dir, "up", "-d", service)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            states = [s for s in docker_ops.ps(app_dir) if s.service == service]
            if states and docker_ops.all_healthy(states):
                break
            time.sleep(3)
        result = docker_ops.compose(
            app_dir, "exec", "-T", service, *shlex.split(spec.restore.command),
            stdin_path=source / "dump.sql",
        )
        if not result.ok:
            raise BackupError(f"Dump konnte nicht eingespielt werden: {result.output[-1000:]}")
        log("Datenbank zurückgespielt")


def restore_app(app_id: str, repo: Path, snapshot_id: str, log, password: str | None = None,
                check: bool = False) -> None:
    """Fetch a snapshot and apply it (rollback, UI restore). The caller starts the app afterwards."""
    with _lock:
        target = settings.data_dir / "restore" / app_id
        try:
            meta = fetch(repo, snapshot_id, app_id, target, password)
            log("Daten aus der Sicherung geholt")
            if check:
                check_version(get_manifest(app_id), meta)
            apply_restore(app_id, target, log)
        finally:
            shutil.rmtree(target, ignore_errors=True)


def list_snapshots(app_id: str) -> list[dict]:
    """Every snapshot of an app on the safety repo and all available targets, newest first."""
    repos = [("Sicherheitskopie auf dem Gerät", safety_repo())] if (safety_repo() / "config").exists() else []
    repos += [(t.name, target_repo(t)) for t in list_targets() if target_available(t)]
    result = []
    for label, repo in repos:
        for snap in snapshots(repo, app_id):
            tags = dict(t.split(":", 1) for t in snap.get("tags", []) if ":" in t)
            result.append({
                "repo": str(repo),
                "repo_label": label,
                "id": snap.get("short_id") or snap.get("id", "")[:8],
                "time": snap.get("time"),
                "reason": tags.get("reason"),
            })
    return sorted(result, key=lambda s: s["time"] or "", reverse=True)


# ---------- export / import ----------

EXPORT_SUFFIX = ".overhub"


def exports_dir() -> Path:
    return settings.data_dir / "exports"


def cleanup_exports(max_age_hours: int = 24) -> None:
    """Exports hold the app's data: they are only kept for a day."""
    if not exports_dir().exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for path in exports_dir().iterdir():
        if path.stat().st_mtime < cutoff:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def export_app(app_id: str, passphrase: str, log) -> str:
    """One file with the app's data: a fresh restic repo (encrypted with the
    passphrase) holding one snapshot of the staged data, packed with tar.
    Readable without OverHub: untar, then `restic -r <dir> restore latest`."""
    manifest = get_manifest(app_id)
    if manifest.backup is None:
        raise BackupError(f"{manifest.name} hat keine Daten auf dem Server, die sich exportieren liessen")
    with _lock:
        cleanup_exports()
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        stage = stage_app(manifest, log)
        version = json.loads((stage / "meta.json").read_text()).get("version") or "x"
        name = f"{app_id}-{version}-{stamp}{EXPORT_SUFFIX}"
        repo = exports_dir() / f"tmp-{app_id}"
        shutil.rmtree(repo, ignore_errors=True)
        try:
            repo.mkdir(parents=True)
            result = restic(repo, "init", password=passphrase, timeout=120)
            if not result.ok:
                raise BackupError(f"Export konnte nicht angelegt werden: {result.stderr or result.output}")
            result = restic(repo, "backup", str(stage), "--host", "overhub", "--tag", f"app:{app_id}",
                            "--tag", f"version:{version}", password=passphrase)
            if not result.ok:
                raise BackupError(f"Export fehlgeschlagen: {(result.stderr or result.output)[-500:]}")
            out = exports_dir() / name
            result = runner.run(["tar", "-cf", str(out), "-C", str(repo), "."], timeout=3600)
            if not result.ok:
                raise BackupError(f"Exportdatei konnte nicht geschrieben werden: {result.output}")
            os.chmod(out, 0o600)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(stage, ignore_errors=True)
    log(f"Export erstellt: {name}")
    return name


def imports_dir() -> Path:
    return settings.data_dir / "imports"


def open_import(import_id: str, app_id: str, passphrase: str, log) -> Path:
    """Unpack an uploaded export and fetch its data into a staging copy.
    Returns the directory with dump.sql, volumes/ and secrets.env."""
    upload = imports_dir() / f"{import_id}{EXPORT_SUFFIX}"
    if not upload.exists():
        raise BackupError("Die hochgeladene Datei ist nicht mehr vorhanden, bitte erneut hochladen")
    repo = imports_dir() / f"{import_id}-repo"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    try:
        result = runner.run(["tar", "-xf", str(upload), "-C", str(repo)], timeout=3600)
        if not result.ok:
            raise BackupError("Die Datei ist kein OverHub-Export")
        result = restic(repo, "snapshots", "--json", "--tag", f"app:{app_id}", password=passphrase, timeout=300)
        if not result.ok:
            raise BackupError("Passphrase falsch oder Datei beschädigt")
        snaps = json.loads(result.output or "[]")
        if not snaps:
            raise BackupError(f"Die Datei enthält keine Daten von {get_manifest(app_id).name}")
        target = imports_dir() / f"{import_id}-data"
        meta = fetch(repo, snaps[-1]["id"], app_id, target, passphrase)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    check_version(get_manifest(app_id), meta)
    log(f"Export geöffnet: {get_manifest(app_id).name} {meta.get('version')} vom {str(meta.get('created', '?'))[:10]}")
    return target


def discard_import(import_id: str) -> None:
    for path in imports_dir().glob(f"{import_id}*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def overdue_apps() -> list[str]:
    """Apps (and OverHub) without a successful backup in the last 2 days."""
    if not list_targets():
        return []
    db = SessionLocal()
    try:
        ids = [a.id for a in db.query(InstalledApp) if (m := get_manifest(a.id)) and m.backup] + [OVERHUB_ID]
        late = []
        now = datetime.now(timezone.utc)
        for app_id in ids:
            row = db.get(BackupStatus, app_id)
            last = row.last_success_at if row else None
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last is None or (now - last).total_seconds() > 2 * 86400:
                late.append(app_id)
        return late
    finally:
        db.close()


def apps_with_backup() -> list[str]:
    return [m.id for m in load_catalog().values() if m.backup]
