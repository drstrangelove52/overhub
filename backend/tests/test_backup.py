from datetime import datetime

from app import backup, scheduler
from app.catalog import load_catalog
from app.config import settings
from app.envfile import read_env
from tests.conftest import wait_job


def _install(admin, app_id="overcook"):
    job = wait_job(admin, admin.post(f"/api/apps/{app_id}/install", json={}).json()["job_id"])
    assert job["status"] == "success", job["log"]


def _add_target(admin, tmp_path):
    r = admin.post("/api/backup/targets", json={"name": "USB", "location": str(tmp_path)})
    assert r.status_code == 200, r.text
    return r.json()


def test_update_takes_a_safety_backup_first(admin, host, monkeypatch):
    _install(admin)
    monkeypatch.setattr(load_catalog()["overcook"], "version", "0.9.0")
    job = wait_job(admin, admin.post("/api/apps/overcook/update").json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert (str(backup.safety_repo()), str(backup.staging_dir("overcook"))) in host.snapshots
    # dump via the manifest command, inside the db service
    assert any(c[6:9] == ["exec", "-T", "db"] and "mariadb-dump" in " ".join(c) for c in host.calls)
    # the recovery key goes through the environment, never the command line
    key = backup.recovery_key()
    assert all(key not in " ".join(c) for c in host.calls)
    assert host.restic_env[-1]["RESTIC_PASSWORD"] == key
    assert not backup.staging_dir("overcook").exists()  # staging cleaned up


def test_failed_update_rolls_back(admin, host, monkeypatch):
    _install(admin)
    old = read_env(settings.apps_dir / "overcook" / ".env")
    manifest = load_catalog()["overcook"]
    monkeypatch.setattr(manifest, "version", "0.9.0")
    host.unhealthy_version = "0.9.0"

    job = wait_job(admin, admin.post("/api/apps/overcook/update").json()["job_id"], timeout=30)
    assert job["status"] == "failed"
    assert "Rollback erfolgreich" in job["log"]
    assert read_env(settings.apps_dir / "overcook" / ".env")["APP_VERSION"] == old["APP_VERSION"]
    # data came back from the pre-update snapshot: restic restore + dump fed into the db
    assert any(c[0] == "restic" and c[3] == "restore" for c in host.calls)
    assert any(c[6:9] == ["exec", "-T", "db"] and "mariadb" in " ".join(c) and "dump" not in " ".join(c)
               for c in host.calls)
    app = next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")
    assert app["version"] == old["APP_VERSION"]


def test_update_aborts_without_change_when_backup_fails(admin, host, monkeypatch):
    _install(admin)
    before = (settings.apps_dir / "overcook" / ".env").read_text()
    monkeypatch.setattr(load_catalog()["overcook"], "version", "0.9.0")
    host.restic_fail = True
    host.pulled_version = None
    job = wait_job(admin, admin.post("/api/apps/overcook/update").json()["job_id"])
    assert job["status"] == "failed" and "Sicherung fehlgeschlagen" in job["log"]
    assert (settings.apps_dir / "overcook" / ".env").read_text() == before
    assert host.pulled_version is None


def test_app_without_server_data_updates_without_backup(admin, host, monkeypatch):
    _install(admin, "overstand")
    monkeypatch.setattr(load_catalog()["overstand"], "version", "0.9.0")
    job = wait_job(admin, admin.post("/api/apps/overstand/update").json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert host.snapshots == []


def test_backup_now_to_target(admin, host, tmp_path):
    _install(admin)
    assert admin.post("/api/backup/run").status_code == 409  # no target yet
    _add_target(admin, tmp_path)
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "success", job["log"]
    repo = str(tmp_path / "overhub")
    assert (repo, str(backup.staging_dir("overcook"))) in host.snapshots
    assert (repo, str(backup.staging_dir(backup.OVERHUB_ID))) in host.snapshots
    assert any(c[0] == "restic" and c[3] == "forget" and "--keep-monthly" in c for c in host.calls)
    overview = admin.get("/api/backup").json()
    assert {a["id"]: a["last_success_at"] is not None for a in overview["apps"]} == {"overcook": True, "_overhub": True}
    assert overview["overdue"] == []


def test_targets_validation_and_availability(admin, host, tmp_path):
    assert admin.post("/api/backup/targets", json={"name": "x", "location": "relativ/pfad"}).status_code == 400
    r = admin.post("/api/backup/targets", json={"name": "x", "location": "/srv/backup"})  # container can't see it
    assert r.status_code == 400 and "muss unter" in r.json()["detail"]
    inside = settings.data_dir / "sub"
    assert admin.post("/api/backup/targets", json={"name": "x", "location": str(inside)}).status_code == 400
    missing = _add_target(admin, tmp_path / "gibt-es-nicht")
    assert missing["available"] is False
    targets = admin.get("/api/backup").json()["targets"]
    assert targets[0]["available"] is False
    admin.delete(f"/api/backup/targets/{targets[0]['id']}")
    assert admin.get("/api/backup").json()["targets"] == []


def test_overdue_after_target_added(admin, host, tmp_path):
    _install(admin)
    assert admin.get("/api/backup").json()["overdue"] == []  # no target: no nagging
    _add_target(admin, tmp_path)
    assert set(admin.get("/api/backup").json()["overdue"]) == {"overcook", "_overhub"}


def test_recovery_key(admin, host):
    key = admin.get("/api/backup/key").json()["key"]
    assert len(key) >= 40 and key == backup.recovery_key()
    assert admin.get("/api/backup").json()["key_acknowledged"] is False
    assert admin.post("/api/backup/key/ack", json={"confirm": "abc"}).status_code == 400  # too short
    assert admin.post("/api/backup/key/ack", json={"confirm": "x" + key[-7:]}).status_code == 400  # wrong
    assert admin.get("/api/backup").json()["key_acknowledged"] is False
    assert admin.post("/api/backup/key/ack", json={"confirm": key[-6:]}).status_code == 200
    assert admin.get("/api/backup").json()["key_acknowledged"] is True


def test_scheduler_runs_once_per_night(admin, host, tmp_path):
    _install(admin)
    tz = __import__("zoneinfo").ZoneInfo(settings.tz)
    assert scheduler.tick(datetime(2026, 10, 3, 3, 5, tzinfo=tz)) is None  # no target yet
    _add_target(admin, tmp_path)
    assert scheduler.tick(datetime(2026, 10, 3, 2, 55, tzinfo=tz)) is None  # too early
    job_id = scheduler.tick(datetime(2026, 10, 3, 3, 5, tzinfo=tz))
    assert job_id is not None
    assert wait_job(admin, job_id)["status"] == "success"
    assert scheduler.tick(datetime(2026, 10, 3, 9, 0, tzinfo=tz)) is None  # once per day
    next_job = scheduler.tick(datetime(2026, 10, 4, 3, 1, tzinfo=tz))
    assert next_job is not None
    wait_job(admin, next_job)  # don't leave a job running into the next test


def test_same_target_cannot_be_added_twice(admin, host, tmp_path):
    _add_target(admin, tmp_path)
    r = admin.post("/api/backup/targets", json={"name": "Nochmal", "location": str(tmp_path) + "/"})
    assert r.status_code == 409 and "schon als Ziel" in r.json()["detail"]
    assert len(admin.get("/api/backup").json()["targets"]) == 1
