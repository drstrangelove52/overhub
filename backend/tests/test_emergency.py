from pathlib import Path

from app import runner
from app.config import settings
from tests.conftest import wait_job


def _capture_stdin(monkeypatch, host):
    """Record what OverHub pipes into `docker compose exec` (the password)."""
    seen = []

    def run(cmd, **kw):
        if kw.get("stdin_path") and "exec" in cmd:
            seen.append((cmd, Path(kw["stdin_path"]).read_text()))
        return host.run(cmd, **kw)

    monkeypatch.setattr(runner, "run", run)
    return seen


def _overcook(admin):
    return next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")


def test_emergency_login_sets_password_via_stdin(admin, host, monkeypatch):
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    assert _overcook(admin)["emergency_login"] == {"username": "notfall", "confirmed_at": None}
    seen = _capture_stdin(monkeypatch, host)

    r = admin.post("/api/apps/overcook/emergency-login")
    assert r.status_code == 200
    password = r.json()["password"]
    assert r.json()["username"] == "notfall" and len(password) >= 16
    (cmd, stdin), = seen
    assert cmd[-9:] == ["exec", "-T", "backend", "python", "-m", "app.create_user", "notfall", "-", "admin"]
    assert password not in " ".join(cmd)
    assert stdin.strip() == password
    assert not list((settings.apps_dir / "overcook").glob(".emergency-*"))  # temp file gone

    assert admin.post("/api/apps/overcook/emergency-login/ack").status_code == 200
    assert _overcook(admin)["emergency_login"]["confirmed_at"]
    # a new password voids the confirmation: the stored one no longer works
    admin.post("/api/apps/overcook/emergency-login")
    assert _overcook(admin)["emergency_login"]["confirmed_at"] is None


def test_emergency_login_only_for_installed_apps_with_a_login(admin, host):
    assert admin.post("/api/apps/overcook/emergency-login").status_code == 404  # not installed
    wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    assert admin.post("/api/apps/overstand/emergency-login").status_code == 404  # static app, no login
    assert next(a for a in admin.get("/api/apps").json() if a["id"] == "overstand")["emergency_login"] is None


def test_delete_all_forgets_the_confirmation(admin, host):
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    admin.post("/api/apps/overcook/emergency-login")
    admin.post("/api/apps/overcook/emergency-login/ack")
    wait_job(admin, admin.post("/api/apps/overcook/uninstall", json={"delete_data": True}).json()["job_id"])
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    assert _overcook(admin)["emergency_login"]["confirmed_at"] is None


def test_emergency_login_needs_an_app_version_that_reads_stdin(admin, host):
    from app.database import SessionLocal
    from app.models import InstalledApp

    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    db = SessionLocal()
    db.get(InstalledApp, "overcook").version = "0.2.2"  # would set "-" as the password
    db.commit()
    db.close()
    assert _overcook(admin)["emergency_login"] is None
    r = admin.post("/api/apps/overcook/emergency-login")
    assert r.status_code == 409 and "0.2.3" in r.json()["detail"]
