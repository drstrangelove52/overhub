import sqlite3

from sqlalchemy import create_engine, inspect

from app import backup, bootstrap
from app.catalog import load_catalog
from app.config import settings
from tests.conftest import wait_job

PASS = "geheim-1234"


def _install(admin, app_id="overcook", **extra):
    job = wait_job(admin, admin.post(f"/api/apps/{app_id}/install", json=extra).json()["job_id"], timeout=30)
    return job


def _export(admin, app_id="overcook", passphrase=PASS):
    r = admin.post(f"/api/apps/{app_id}/export", json={"passphrase": passphrase})
    assert r.status_code == 200, r.text
    job = wait_job(admin, r.json()["job_id"])
    assert job["status"] == "success", job["log"]
    return job["result"]["download"]


def test_export_creates_downloadable_file_with_own_passphrase(admin, host):
    _install(admin)
    assert admin.post("/api/apps/overcook/export", json={"passphrase": "kurz"}).status_code == 400
    name = _export(admin)
    assert name.startswith("overcook-") and name.endswith(".overhub")
    r = admin.get(f"/api/exports/{name}")
    assert r.status_code == 200 and r.content == b"fake export"
    # encrypted with the passphrase, not the instance key
    init_env = next(e for c, e in zip([c for c in host.calls if c[0] == "restic"], host.restic_env) if c[3] == "init")
    assert init_env["RESTIC_PASSWORD"] == PASS
    assert admin.get("/api/exports/..%2Foverhub.db").status_code == 404


def test_export_of_app_without_server_data_is_refused(admin, host):
    _install(admin, "overstand")
    assert admin.post("/api/apps/overstand/export", json={"passphrase": PASS}).status_code == 400


def test_uninstall_with_export_then_import_on_reinstall(admin, host):
    _install(admin)
    r = admin.post("/api/apps/overcook/uninstall", json={"delete_data": True, "export_passphrase": PASS})
    job = wait_job(admin, r.json()["job_id"])
    assert job["status"] == "success", job["log"]
    name = job["result"]["download"]
    assert not (settings.apps_dir / "overcook").exists()
    data = admin.get(f"/api/exports/{name}").content

    up = admin.post("/api/imports", files={"file": (name, data, "application/octet-stream")})
    assert up.status_code == 200, up.text
    import_id = up.json()["import_id"]

    job = _install(admin, import_id=import_id, import_passphrase="falsch-falsch")
    assert job["status"] == "failed" and "Passphrase falsch" in job["log"]
    assert not next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")["installed"]

    job = _install(admin, import_id=import_id, import_passphrase=PASS)
    assert job["status"] == "success", job["log"]
    assert "Import abgeschlossen" in job["log"]
    assert job["credentials"] is None  # the imported users apply, no new admin password to show
    assert any(c[6:9] == ["exec", "-T", "db"] and "mariadb-dump" not in " ".join(c) and "mariadb" in " ".join(c)
               for c in host.calls)  # dump fed into the new database
    assert not list(backup.imports_dir().glob(f"{import_id}*"))  # upload discarded


def test_import_rejects_newer_app_version(admin, host, monkeypatch):
    _install(admin)
    monkeypatch.setattr(load_catalog()["overcook"], "version", "0.9.0")
    job = wait_job(admin, admin.post("/api/apps/overcook/update").json()["job_id"])
    assert job["status"] == "success", job["log"]
    name = _export(admin)  # data of 0.9.0
    data = admin.get(f"/api/exports/{name}").content
    wait_job(admin, admin.post("/api/apps/overcook/uninstall", json={"delete_data": True}).json()["job_id"])
    monkeypatch.setattr(load_catalog()["overcook"], "version", "0.1.3")
    import_id = admin.post("/api/imports", files={"file": (name, data)}).json()["import_id"]
    job = _install(admin, import_id=import_id, import_passphrase=PASS)
    assert job["status"] == "failed" and "stammen von OverCook 0.9.0" in job["log"]


def test_upload_rejects_other_files(admin, host):
    assert admin.post("/api/imports", files={"file": ("foto.jpg", b"x")}).status_code == 400


def test_list_and_restore_snapshot(admin, host, tmp_path):
    _install(admin)
    admin.post("/api/backup/targets", json={"name": "USB", "location": str(tmp_path)})
    wait_job(admin, admin.post("/api/backup/run").json()["job_id"])
    snaps = admin.get("/api/apps/overcook/snapshots").json()
    assert len(snaps) == 1 and snaps[0]["repo_label"] == "USB" and snaps[0]["reason"] == "manual"

    bad = admin.post("/api/apps/overcook/restore", json={"repo": "/etc", "snapshot_id": snaps[0]["id"]})
    assert bad.status_code == 400

    r = admin.post("/api/apps/overcook/restore", json={"repo": snaps[0]["repo"], "snapshot_id": snaps[0]["id"]})
    job = wait_job(admin, r.json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert "Sichere den aktuellen Stand" in job["log"]
    assert (str(backup.safety_repo()), str(backup.staging_dir("overcook"))) in host.snapshots  # undo point
    # now the safety copy shows up as well
    labels = {s["repo_label"] for s in admin.get("/api/apps/overcook/snapshots").json()}
    assert labels == {"USB", "Sicherheitskopie auf dem Gerät"}


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, app_id TEXT, action TEXT, status TEXT, log TEXT, credentials TEXT)")
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(bootstrap, "engine", engine)
    bootstrap._add_missing_columns()
    assert "result" in {c["name"] for c in inspect(engine).get_columns("job")}
    bootstrap._add_missing_columns()  # idempotent


def test_old_exports_are_cleaned_up_by_the_scheduler(admin, host):
    import os
    import time
    from datetime import datetime

    from app import scheduler

    _install(admin)
    name = _export(admin)
    path = backup.exports_dir() / name
    scheduler.tick(datetime(2026, 10, 3, 1, 0))  # before 03:00: no backup, but cleanup runs
    assert path.exists()
    old = time.time() - 25 * 3600
    os.utime(path, (old, old))
    scheduler.tick(datetime(2026, 10, 3, 1, 5))
    assert not path.exists()
