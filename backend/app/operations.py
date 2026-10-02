"""Install, update, start and stop catalog apps as background jobs.

Every step is written to the job's log so the UI can follow along. A job
runs in its own thread with its own DB session; only one job per app at a
time.
"""
import json
import platform
import shutil
import threading
import time
from pathlib import Path

import httpx

from app import docker_ops, tailscale_ops
from app.catalog import Manifest, get_manifest, version_tuple
from app.config import settings
from app.database import SessionLocal
from app.envfile import read_env, validate_value, write_env
from app.models import InstalledApp, Job, utcnow
from app.security import generate_secret


class OperationError(Exception):
    pass


_busy: set[str] = set()
_busy_lock = threading.Lock()


def is_busy(app_id: str) -> bool:
    with _busy_lock:
        return app_id in _busy


def app_dir(app_id: str) -> Path:
    return settings.apps_dir / app_id


# ---------- job plumbing ----------

class JobLog:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def __call__(self, message: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, self.job_id)
            job.log += f"[{time.strftime('%H:%M:%S')}] {message}\n"
            db.commit()
        finally:
            db.close()


def start_job(app_id: str, action: str, target, *args) -> int:
    """Create the job row and run `target(log, *args)` in a thread.

    `target` returns show-once credentials (dict) or None and raises
    OperationError with a user-facing message on failure.
    """
    with _busy_lock:
        if app_id in _busy:
            raise OperationError(f"Für {app_id} läuft bereits eine Aktion")
        _busy.add(app_id)
    db = SessionLocal()
    try:
        job = Job(app_id=app_id, action=action)
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    def runner():
        log = JobLog(job_id)
        status, credentials = "failed", None
        try:
            credentials = target(log, *args)
            status = "success"
            log("Fertig.")
        except OperationError as exc:
            log(f"FEHLER: {exc}")
        except Exception as exc:  # unexpected: keep the details for support
            log(f"FEHLER (unerwartet): {exc!r}")
        finally:
            db = SessionLocal()
            try:
                job = db.get(Job, job_id)
                job.status = status
                job.finished_at = utcnow()
                job.credentials = json.dumps(credentials) if credentials else None
                db.commit()
            finally:
                db.close()
            with _busy_lock:
                _busy.discard(app_id)

    threading.Thread(target=runner, name=f"job-{job_id}", daemon=True).start()
    return job_id


# ---------- checks ----------

def host_arch() -> str:
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )


def host_ram_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def preflight(manifest: Manifest, log) -> None:
    arch = host_arch()
    if arch not in manifest.arch:
        raise OperationError(f"{manifest.name} gibt es nicht für {arch} (nur {', '.join(manifest.arch)})")
    if settings.version != "dev" and version_tuple(settings.version) < version_tuple(manifest.min_overhub):
        raise OperationError(f"{manifest.name} braucht OverHub {manifest.min_overhub} oder neuer")
    ram = host_ram_mb()
    if ram is not None and ram < manifest.min_ram_mb:
        log(f"Warnung: {ram} MB RAM, {manifest.name} empfiehlt mindestens {manifest.min_ram_mb} MB")


def wait_healthy(manifest: Manifest, log) -> None:
    directory = app_dir(manifest.id)
    url = f"http://127.0.0.1:{manifest.internal_port}{manifest.health}"
    deadline = time.monotonic() + settings.health_timeout_seconds
    last = None
    while time.monotonic() < deadline:
        states = docker_ops.ps(directory)
        summary = ", ".join(f"{s.service}: {s.health or s.state}" for s in states)
        if summary != last:
            log(f"Status: {summary or 'keine Container'}")
            last = summary
        if docker_ops.all_healthy(states):
            try:
                if httpx.get(url, timeout=5).status_code == 200:
                    log(f"{manifest.health} antwortet mit 200")
                    return
            except httpx.HTTPError:
                pass
        time.sleep(3)
    raise OperationError(
        f"{manifest.name} wurde nicht innerhalb von {settings.health_timeout_seconds} s gesund. Logs prüfen."
    )


# ---------- env ----------

def _generate_missing(spec_generated, env: dict[str, str], credentials: dict[str, str]) -> None:
    for secret in spec_generated:
        if secret.name not in env:  # never regenerated once it exists (contract rule 7)
            env[secret.name] = generate_secret(secret.generate)
            if secret.show_once:
                credentials[secret.name] = env[secret.name]


def build_env(manifest: Manifest, user_settings: dict[str, str], components: list[str],
              existing: dict[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    env = dict(existing or {})
    credentials: dict[str, str] = {}
    env["APP_VERSION"] = manifest.version
    env["APP_INTERNAL_PORT"] = str(manifest.internal_port)
    url = tailscale_ops.app_url(manifest.port)
    if url:
        env["APP_PUBLIC_URL"] = url
    env.setdefault("TZ", settings.tz)
    if components:
        env["COMPOSE_PROFILES"] = ",".join(components)
    else:
        env.pop("COMPOSE_PROFILES", None)

    env.update(manifest.env.fixed)
    _generate_missing(manifest.env.generated, env, credentials)
    chosen: dict[str, str] = {}
    for setting in manifest.env.settings:
        if setting.name in env and existing is not None:
            continue  # update, or a retried install: keep what the app was set up with
        value = user_settings.get(setting.name, setting.default)
        validate_value(value)
        env[setting.name] = value
        chosen[setting.name] = value

    for name in components:
        component = manifest.components[name]
        env.update(component.env.fixed)
        _generate_missing(component.env.generated, env, credentials)
    if credentials:
        # Show e.g. ADMIN_USERNAME next to the generated ADMIN_PASSWORD.
        credentials = {**chosen, **credentials}
    return env, credentials


# ---------- actions ----------

def _pull_verify_up(manifest: Manifest, log) -> None:
    directory = app_dir(manifest.id)
    log(f"Lade Images für Version {manifest.version} …")
    result = docker_ops.compose(directory, "pull", "--quiet")
    if not result.ok:
        raise OperationError(f"Images konnten nicht geladen werden:\n{result.output[-2000:]}")
    problems = docker_ops.verify_digests(directory, manifest.images)
    if problems:
        raise OperationError("Prüfung der Images fehlgeschlagen:\n" + "\n".join(problems))
    log("Images geprüft (Digests stimmen mit dem Katalog überein)")
    log("Starte Container …")
    result = docker_ops.compose(directory, "up", "-d", "--remove-orphans")
    if not result.ok:
        raise OperationError(f"Start fehlgeschlagen:\n{result.output[-2000:]}")
    wait_healthy(manifest, log)


def install(log, app_id: str, user_settings: dict[str, str], components: list[str]) -> dict | None:
    manifest = get_manifest(app_id)
    db = SessionLocal()
    try:
        if db.get(InstalledApp, app_id):
            raise OperationError(f"{manifest.name} ist bereits installiert")
    finally:
        db.close()
    preflight(manifest, log)

    directory = app_dir(app_id)
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest.compose_file, directory / "compose.yml")
    env, credentials = build_env(manifest, user_settings, components, read_env(directory / ".env") or None)
    write_env(directory / ".env", env)
    log(f"{directory}/.env geschrieben ({len(env)} Einträge)")

    _pull_verify_up(manifest, log)

    log(f"Richte tailscale serve ein: https Port {manifest.port} → 127.0.0.1:{manifest.internal_port}")
    result = tailscale_ops.serve(manifest.port, manifest.internal_port)
    if not result.ok:
        raise OperationError(f"tailscale serve fehlgeschlagen:\n{result.output}")

    db = SessionLocal()
    try:
        db.add(InstalledApp(id=app_id, version=manifest.version, components=",".join(components)))
        db.commit()
    finally:
        db.close()
    url = tailscale_ops.app_url(manifest.port)
    log(f"{manifest.name} {manifest.version} läuft: {url}")
    return {"url": url, **credentials} if credentials else None


def update(log, app_id: str) -> dict | None:
    manifest = get_manifest(app_id)
    db = SessionLocal()
    try:
        installed = db.get(InstalledApp, app_id)
        if installed is None:
            raise OperationError(f"{manifest.name} ist nicht installiert")
        old_version = installed.version
        components = [c for c in installed.components.split(",") if c]
    finally:
        db.close()
    if version_tuple(manifest.version) <= version_tuple(old_version):
        raise OperationError(f"{manifest.name} {old_version} ist bereits aktuell")
    preflight(manifest, log)
    log(f"Update {manifest.name} {old_version} → {manifest.version}")
    log("Hinweis: automatisches Backup und Rollback kommen erst mit Etappe 3")

    directory = app_dir(app_id)
    shutil.copyfile(manifest.compose_file, directory / "compose.yml")
    env, credentials = build_env(manifest, {}, components, read_env(directory / ".env"))
    write_env(directory / ".env", env)
    _pull_verify_up(manifest, log)

    db = SessionLocal()
    try:
        installed = db.get(InstalledApp, app_id)
        installed.version = manifest.version
        installed.updated_at = utcnow()
        db.commit()
    finally:
        db.close()
    return credentials or None


def start(log, app_id: str) -> None:
    manifest = get_manifest(app_id)
    result = docker_ops.compose(app_dir(app_id), "up", "-d")
    if not result.ok:
        raise OperationError(result.output[-2000:])
    wait_healthy(manifest, log)


def stop(log, app_id: str) -> None:
    result = docker_ops.compose(app_dir(app_id), "stop")
    log(result.output.strip() or "gestoppt")
    if not result.ok:
        raise OperationError("Stoppen fehlgeschlagen")
