from app import backup
from app.models import BackupTarget
from tests.conftest import wait_job

NAS = {"kind": "sftp", "name": "NAS", "host": "nas.local", "user": "overhub_backup", "path": "backup_primary"}
REPO = "sftp:overhub_backup@nas.local:/backup_primary/overhub"


def test_add_sftp_target_checks_login_and_hides_password(admin, host):
    r = admin.post("/api/backup/targets", json={**NAS, "password": "wrong-pass"})
    assert r.status_code == 400 and "Passwort falsch" in r.json()["detail"]
    assert admin.get("/api/backup").json()["targets"] == []  # nothing stored

    r = admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    assert r.status_code == 200 and r.json()["available"] is True
    targets = admin.get("/api/backup").json()["targets"]
    assert targets == [{"id": targets[0]["id"], "name": "NAS", "available": True,
                        "location": "sftp:overhub_backup@nas.local:/backup_primary"}]
    assert "nas-pass-123" not in admin.get("/api/backup").text
    login = next(c for c in host.calls if c[0] == "sshpass")
    assert "nas-pass-123" not in " ".join(login) and "StrictHostKeyChecking=accept-new" in login

    r = admin.post("/api/backup/targets", json={**NAS, "path": "/backup_primary/", "password": "nas-pass-123"})
    assert r.status_code == 409  # same folder twice


def test_invalid_sftp_input(admin, host):
    for bad in ({"host": "nas local"}, {"user": "a b"}, {"path": "../etc"}):
        assert admin.post("/api/backup/targets", json={**NAS, "password": "x" * 10, **bad}).status_code == 400
    assert admin.post("/api/backup/targets", json={**NAS}).status_code == 400  # no password


def test_backup_to_sftp_and_restore(admin, host):
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert REPO in host.repos
    sftp_calls = [c for c in host.calls if c[0] == "restic" and c[2] == REPO]
    assert sftp_calls and all("-o" in c and "nas-pass-123" not in " ".join(c) for c in sftp_calls)
    assert all(e.get("SSHPASS") == "nas-pass-123" for e in host.restic_env if e.get("SSHPASS"))

    snaps = [s for s in admin.get("/api/apps/overcook/snapshots").json() if s["repo"] == REPO]
    assert snaps and snaps[0]["repo_label"] == "NAS"
    job = wait_job(admin, admin.post("/api/apps/overcook/restore",
                                     json={"repo": REPO, "snapshot_id": snaps[0]["id"]}).json()["job_id"])
    assert job["status"] == "success", job["log"]


def test_nas_down_is_skipped_not_fatal(admin, host):
    admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    backup._available_cache.clear()
    host.sftp_up = False
    assert admin.get("/api/backup").json()["targets"][0]["available"] is False
