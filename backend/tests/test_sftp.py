from app import backup
from app.models import BackupTarget
from tests.conftest import wait_job

NAS = {"kind": "sftp", "name": "NAS", "host": "nas.local", "user": "overhub_backup", "path": "backup_primary"}
REPO = "sftp:overhub_backup@nas.local:/backup_primary/overhub"


def test_add_sftp_target_checks_login_and_hides_password(admin, host):
    r = admin.post("/api/backup/targets", json={**NAS, "password": "wrong-pass"})
    assert r.status_code == 400 and "Passwort falsch" in r.json()["detail"]
    r = admin.post("/api/backup/targets", json={**NAS, "path": "missing", "password": "nas-pass-123"})
    assert r.status_code == 400 and "gibt es auf dem NAS nicht" in r.json()["detail"]
    assert admin.get("/api/backup").json()["targets"] == []  # nothing stored

    r = admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    assert r.status_code == 200 and r.json()["available"] is True
    targets = admin.get("/api/backup").json()["targets"]
    assert targets == [{"id": targets[0]["id"], "name": "NAS", "available": True,
                        "location": "sftp:overhub_backup@nas.local:/backup_primary",
                        "kind": "sftp", "host": "nas.local", "user": "overhub_backup", "path": "backup_primary"}]
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
    command = sftp_calls[0][sftp_calls[0].index("-o") + 1]
    assert "," not in command  # restic splits option values at commas
    assert command.endswith("overhub_backup@nas.local -s sftp")
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


def test_sftp_errors_name_the_cause():
    explain = backup._explain_sftp
    assert "Port 22" in explain("ssh: connect to host nas port 22: Connection refused\nConnection closed", "nas", "/b")
    assert "SFTP ist auf dem NAS nicht aktiv" in explain("subsystem request failed on channel 0\nConnection closed", "nas", "/b")
    assert "gibt es auf dem NAS nicht" in explain('Can\'t ls: "/b" not found', "nas", "/b")
    assert explain("weird thing\nConnection closed", "nas", "/b") == "Keine SFTP-Verbindung zu nas: weird thing"


def test_unreachable_nas_is_explained(admin, host):
    host.sftp_up = False
    r = admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    assert r.status_code == 400 and "Port 22" in r.json()["detail"]


def test_folder_with_another_devices_backup_is_refused(admin, host):
    host.repos[REPO] = "key-of-another-device"
    r = admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    assert r.status_code == 409 and "anderen OverHub-Geräts" in r.json()["detail"]
    assert admin.get("/api/backup").json()["targets"] == []

    # own subfolder works
    r = admin.post("/api/backup/targets", json={**NAS, "path": "backup_primary/pi", "password": "nas-pass-123"})
    assert r.status_code == 200


def test_backup_names_a_foreign_repository(admin, host):
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    host.repos[REPO] = "key-of-another-device"  # e.g. targets set up before this check existed
    job = wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    assert job["status"] == "failed" and "anderen OverHub-Geräts" in job["log"]
    assert not any(c[0] == "restic" and c[2] == REPO and "init" in c for c in host.calls)


def test_edit_sftp_target(admin, host):
    admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    target = admin.get("/api/backup").json()["targets"][0]
    assert target["kind"] == "sftp" and target["host"] == "nas.local" and target["path"] == "backup_primary"
    assert "password" not in target

    # rename, keep the password (empty field)
    r = admin.put(f"/api/backup/targets/{target['id']}", json={**NAS, "name": "Synology", "password": ""})
    assert r.status_code == 200 and r.json()["available"] is True
    assert admin.get("/api/backup").json()["targets"][0]["name"] == "Synology"

    # a wrong new password is refused and nothing changes
    r = admin.put(f"/api/backup/targets/{target['id']}", json={**NAS, "password": "wrong-pass"})
    assert r.status_code == 400
    host.sftp_password = "changed-on-nas"
    backup._available_cache.clear()
    assert admin.get("/api/backup").json()["targets"][0]["available"] is False
    r = admin.put(f"/api/backup/targets/{target['id']}", json={**NAS, "password": "changed-on-nas"})
    assert r.status_code == 200 and r.json()["available"] is True

    # another folder; one with another device's backup is refused and the old one stays
    host.repos["sftp:overhub_backup@nas.local:/backup_primary/other/overhub"] = "key-of-another-device"
    r = admin.put(f"/api/backup/targets/{target['id']}", json={**NAS, "path": "backup_primary/other", "password": ""})
    assert r.status_code == 409
    assert admin.get("/api/backup").json()["targets"][0]["path"] == "backup_primary"
    r = admin.put(f"/api/backup/targets/{target['id']}", json={**NAS, "path": "backup_primary/pi", "password": ""})
    assert r.status_code == 200 and admin.get("/api/backup").json()["targets"][0]["path"] == "backup_primary/pi"


def test_edit_folder_target_name(admin, host):
    admin.post("/api/backup/targets", json={**NAS, "password": "nas-pass-123"})
    admin.post("/api/backup/targets", json={**NAS, "path": "backup_primary/two", "password": "nas-pass-123"})
    first, second = admin.get("/api/backup").json()["targets"]
    r = admin.put(f"/api/backup/targets/{second['id']}", json={**NAS, "password": ""})
    assert r.status_code == 409  # same folder as the first target
    assert admin.put("/api/backup/targets/999", json={**NAS}).status_code == 404
