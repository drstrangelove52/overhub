import shutil

from app import backup
from app.bootstrap import init_db
from app.config import settings
from app.database import Base, engine
from app.envfile import read_env
from tests.conftest import wait_job


def _old_device(admin, tmp_path):
    """Install OverCook, change the admin password, back everything up."""
    job = wait_job(admin, admin.post("/api/apps/overcook/install", json={"components": ["mcp"]}).json()["job_id"])
    assert job["status"] == "success", job["log"]
    admin.put("/api/auth/me/password", json={"current_password": "admin-pass-123", "new_password": "altes-passwort"})
    admin.post("/api/backup/targets", json={"name": "USB alt", "location": str(tmp_path)})
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "success", job["log"]
    return backup.recovery_key(), read_env(settings.apps_dir / "overcook" / ".env")


def _new_device(client):
    """Wipe everything a fresh install would not have; the backup disk stays."""
    Base.metadata.drop_all(engine)
    for sub in ("apps", "safety", "staging", "restore"):
        shutil.rmtree(settings.data_dir / sub, ignore_errors=True)
    backup.key_file().unlink()
    init_db()  # fresh admin from OVERHUB_ADMIN_PASSWORD
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"}).status_code == 200


def test_replace_device_from_backup(admin, host, tmp_path):
    old_key, old_env = _old_device(admin, tmp_path)
    _new_device(admin)
    assert admin.get("/api/apps").json()[0]["installed"] is False

    # wrong key / wrong place are reported clearly
    r = admin.post("/api/backup/replace/scan", json={"location": str(tmp_path), "key": "falsch"})
    assert r.status_code == 400 and "passt nicht" in r.json()["detail"]
    r = admin.post("/api/backup/replace/scan", json={"location": str(tmp_path / "leer"), "key": old_key})
    assert r.status_code == 400

    snaps = admin.post("/api/backup/replace/scan", json={"location": str(tmp_path), "key": old_key}).json()
    assert len(snaps) == 1
    assert snaps[0]["apps"] == [{"id": "overcook", "version": old_env["APP_VERSION"]}]

    r = admin.post("/api/backup/replace", json={"location": str(tmp_path), "key": old_key, "snapshot_id": snaps[0]["id"]})
    job_id = r.json()["job_id"]
    # the job ends by swapping in the old users: the new admin's session is gone
    import time
    for _ in range(300):
        if admin.get(f"/api/jobs/{job_id}").status_code == 401:
            break
        time.sleep(0.05)
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"}).status_code == 401
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "altes-passwort"}).status_code == 200
    job = wait_job(admin, job_id)
    assert job["status"] == "success", job["log"]

    # app back with its old secrets and components, data restored from the newest snapshot
    env = read_env(settings.apps_dir / "overcook" / ".env")
    for name in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SESSION_SECRET", "MCP_API_KEY"):
        assert env[name] == old_env[name]
    app = next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")
    assert app["installed"] and app["enabled_components"] == ["mcp"]
    assert "Spiele die neuesten Daten ein" in job["log"]
    assert any(c[0] == "restic" and c[3] == "restore" and "staging/overcook" in c[4].replace("\\", "/") for c in host.calls)

    # key and targets taken over
    assert backup.recovery_key() == old_key
    overview = admin.get("/api/backup").json()
    assert [t["name"] for t in overview["targets"]] == ["USB alt"]
    assert overview["key_acknowledged"] is True


def test_replace_refused_when_apps_are_installed(admin, host, tmp_path):
    old_key, _ = _old_device(admin, tmp_path)
    r = admin.post("/api/backup/replace", json={"location": str(tmp_path), "key": old_key, "snapshot_id": "00000001"})
    assert r.status_code == 409


NAS = {"kind": "sftp", "host": "nas.local", "user": "overhub_backup", "path": "backup_primary/pi"}


def test_replace_device_from_nas(admin, host, tmp_path):
    job = wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    assert job["status"] == "success", job["log"]
    r = admin.post("/api/backup/targets", json={**NAS, "name": "Synology", "password": "nas-pass-123"})
    assert r.status_code == 200
    admin.post("/api/apps/overcook/emergency-login")
    admin.post("/api/apps/overcook/emergency-login/ack")
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "success", job["log"]
    old_key = backup.recovery_key()
    _new_device(admin)
    assert admin.get("/api/backup").json()["targets"] == []  # nothing knows the NAS yet

    r = admin.post("/api/backup/replace/scan", json={**NAS, "key": old_key, "password": "falsch"})
    assert r.status_code == 400 and "Passwort falsch" in r.json()["detail"]
    r = admin.post("/api/backup/replace/scan", json={**NAS, "key": "falsch", "password": "nas-pass-123"})
    assert r.status_code == 400 and "passt nicht" in r.json()["detail"]
    r = admin.post("/api/backup/replace/scan", json={**NAS, "path": "backup_primary/leer", "key": old_key,
                                                     "password": "nas-pass-123"})
    assert r.status_code == 400 and "kein OverHub-Backup" in r.json()["detail"]

    snaps = admin.post("/api/backup/replace/scan", json={**NAS, "key": old_key, "password": "nas-pass-123"}).json()
    assert len(snaps) == 1 and snaps[0]["apps"][0]["id"] == "overcook"
    r = admin.post("/api/backup/replace", json={**NAS, "key": old_key, "password": "nas-pass-123",
                                                "snapshot_id": snaps[0]["id"]})
    job_id = r.json()["job_id"]
    import time
    for _ in range(300):
        if admin.get(f"/api/jobs/{job_id}").status_code == 401:
            break
        time.sleep(0.05)
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"}).status_code == 200
    job = wait_job(admin, job_id)
    assert job["status"] == "success", job["log"]
    assert "Spiele die neuesten Daten ein" in job["log"]
    # the emergency account came back with OverCook's data: no new password needed
    overcook = next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")
    assert overcook["emergency_login"]["confirmed_at"] and "Notfall-Konto übernommen" in job["log"]

    # the NAS is a target again, with its password: the next backup works without typing it again
    targets = admin.get("/api/backup").json()["targets"]
    assert [(t["name"], t["path"], t["available"]) for t in targets] == [("Synology", "backup_primary/pi", True)]
    assert backup._extra_logins == {}  # the temporary login is gone
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "success", job["log"]
